"""Цикл синхронизации «лист → база».

Воркер запускает его раз в пять минут, `scripts/import_sheets.py` — вручную.
Логика одна, чтобы ручной перенос не обходил правил автоматического
(спека docs/superpowers/specs/2026-09-23-sinhronizatsiya-design.md):

* **Книга переносится целиком или не переносится.** Лист не прочитан или у
  него сдвинулись колонки — книга в этом цикле не трогается, старые данные
  остаются, причина уходит на сайт. Колонки читаются по позиции: сдвиг дал бы
  цену из колонки веса — тихо и каждые пять минут.
* **Только изменения.** Отпечаток книги совпал с перенесённым — в базу
  пишется одна отметка «проверено».
* **Сбой в журнал — один раз**, когда причина появилась или сменилась.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kitchen.sync import specs
from kitchen.sync.reader import BOOK_OPEN_FAILED, BOOK_READ_FAILED, sheet_label

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from kitchen.sync.ownership import SheetSpec
    from kitchen.sync.reader import SheetData

BOOKS: dict[str, tuple[SheetSpec, ...]] = {
    "kitchen": (specs.INGREDIENTS, specs.PACKAGING, specs.COOKING_METHODS, specs.DISHES, specs.TTK),
    "ingredient_cards": (specs.INGREDIENT_CARDS,),
}
"""Книги (Google-таблицы) и их листы. Листы кухни ссылаются друг на друга и
переносятся только вместе; карточки от кухни не зависят."""

BOOK_TITLES: dict[str, str] = {
    "kitchen": "таблица кухни",
    "ingredient_cards": "карточки ингредиентов",
}
"""Как книга называется на сайте. Со строчной: стоит посреди фразы."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """Что показало чтение книги: причина не переносить — или отпечаток."""

    problem: str | None
    fingerprint: str | None


# Признаки «Google не ответил», без учёта регистра.
_NO_ANSWER = (
    # Сеть: ответа не дождались.
    "timeout",
    "timed out",
    "connection",
    "max retries",
    # Сбой у самого Google (5xx). Для шефа это то же «не ответил», и лечится
    # так же — следующим циклом (спека 4.5 ставит их в один ряд).
    "[500]",
    "[502]",
    "[503]",
    "[504]",
    "internal error",
    "backend error",
    "unavailable",
)


def explain(title: str, error: str) -> str:
    """Причина сбоя чтения словами, которые поймёт шеф.

    Текст исключения gspread написан для разработчика, а полосу на сайте видят
    все (решение Александра 23.09). Известные случаи переводим. Отказ всей
    таблицы — открытия или пакетного чтения — приходит одинаковым на каждый её
    лист; дело не в листе, и без имени листа одинаковые причины сливаются в
    одну. Остальное — как есть, с именем листа.

    `error` — строка читателя: метка вроде `BOOK_OPEN_FAILED` и
    `describe_error` исключения, в котором есть имя класса и HTTP-код.
    """
    lowered = error.lower()
    # «[Errno N]» — ошибка ОС: на нашей стороне (нет прав на файл ключа, нет
    # самого файла) или в сети. Это не ответ Google о таблице — ни квота, ни
    # доступ, ни «не найдена»: такой перевод увёл бы искать не там. Сетевой
    # сбой при этом узнаёт проверка «не ответил» ниже, по своим признакам.
    google_answered = "[errno" not in lowered
    if google_answered and ("[429]" in error or "quota" in lowered):
        return "Google временно ограничил число запросов — следующая попытка через 5 минут"
    # 403: `APIError` с [403] или голый `PermissionError()` — так gspread
    # отвечает на закрытый доступ при открытии таблицы.
    if google_answered and ("[403]" in error or "permission" in lowered):
        return (
            "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"
        )
    # 404: `SpreadsheetNotFound` при открытии или `APIError` с [404]. Имя
    # класса — целиком: «NotFound» есть и в ModuleNotFoundError (gspread
    # импортируется лениво, внутри `open()`), а это поломка у нас.
    if google_answered and ("[404]" in error or "spreadsheetnotfound" in lowered):
        return (
            "таблица не найдена — проверьте её идентификатор в настройках (SHEETS_ID_*) "
            "и доступ сервисного аккаунта"
        )
    if any(sign in lowered for sign in _NO_ANSWER):
        return "Google не ответил — следующая попытка через 5 минут"
    if "нет в таблице" in error or "не задан идентификатор" in error:
        return error
    if error.startswith(BOOK_OPEN_FAILED):
        return f"не удалось открыть таблицу: {error.removeprefix(BOOK_OPEN_FAILED)[:200]}"
    if error.startswith(BOOK_READ_FAILED):
        return f"не удалось прочитать таблицу: {error.removeprefix(BOOK_READ_FAILED)[:200]}"
    return f"не удалось прочитать лист «{title}»: {error[:200]}"


_SHOWN_ISSUES = 3
"""Сколько расхождений заголовков называть. Съехавший лист даёт расхождение в
каждой колонке, а полосу на сайте читают целиком."""


def _header_problem(title: str, issues: Sequence[str]) -> str:
    if tuple(issues) == ("лист пуст",):
        return f"лист «{title}» пуст"
    listed = "; ".join(issues[:_SHOWN_ISSUES])
    if len(issues) > _SHOWN_ISSUES:
        listed += f"; и ещё {len(issues) - _SHOWN_ISSUES}"
    if all(issue.startswith("колонка ") for issue in issues):
        return f"в листе «{title}» сдвинулись колонки — {listed}"
    return f"лист «{title}»: {listed}"


def judge(book: str, sheets: Mapping[str, SheetData | str]) -> Verdict:
    """Можно ли переносить книгу и что в ней.

    Отпечаток — SHA-256 от «лист | номер строки | хеш строки» по всем листам в
    порядке описаний. Хеш строки считает читатель ровно по импортируемым
    колонкам, поэтому заметка на полях перенос не будит.
    """
    problems: list[str] = []
    lines: list[str] = []
    for spec in BOOKS[book]:
        data = sheets.get(sheet_label(spec))
        if data is None:
            problems.append(f"лист «{spec.title}» не прочитан")
        elif isinstance(data, str):
            problems.append(explain(spec.title, data))
        elif data.header_issues:
            problems.append(_header_problem(data.title, data.header_issues))
        else:
            lines.extend(f"{spec.title}|{row.number}|{row.content_hash}" for row in data.rows)
    if problems:
        # Отказ открытия таблицы приходит одинаковым на каждый её лист —
        # повторять одну причину пять раз незачем. Причины разных листов
        # разделяет « | »: внутри причины листа уже стоят «; ».
        return Verdict(problem=" | ".join(dict.fromkeys(problems)), fingerprint=None)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return Verdict(problem=None, fingerprint=digest)


class SyncCycle:
    """Цикл синхронизации — пока заготовка без поведения.

    Интеграционные тесты импортируют имя на уровне модуля; без него падал бы
    сбор всего набора, офлайн-части тоже. Поведение — следующим коммитом.
    """

    def __init__(self, *_: object) -> None:
        pass

    def run(self, *, force: bool = False) -> None:
        raise NotImplementedError
