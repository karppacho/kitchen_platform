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
from kitchen.sync.reader import sheet_label

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

# Так читатель (`SheetsReader._read_book`) помечает каждый лист таблицы,
# которая не открылась целиком.
_BOOK_NOT_OPENED = "не открылась таблица: "


def explain(title: str, error: str) -> str:
    """Причина сбоя чтения словами, которые поймёт шеф.

    Текст исключения gspread написан для разработчика, а полосу на сайте видят
    все (решение Александра 23.09). Известные случаи переводим, остальное —
    как есть, но с именем листа. Кроме отказа открытия всей таблицы: он приходит
    одинаковым на каждый её лист, дело не в листе, и без имени листа пять
    одинаковых причин сливаются в одну.
    """
    lowered = error.lower()
    if "[429]" in error or "quota" in lowered:
        return "Google временно ограничил число запросов — следующая попытка через 5 минут"
    if "[403]" in error or "permission" in lowered:
        return (
            "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"
        )
    if any(sign in lowered for sign in _NO_ANSWER):
        return "Google не ответил — следующая попытка через 5 минут"
    if "нет в таблице" in error or "не задан идентификатор" in error:
        return error
    if error.startswith(_BOOK_NOT_OPENED):
        return f"не удалось открыть таблицу: {error.removeprefix(_BOOK_NOT_OPENED)[:200]}"
    return f"не удалось прочитать лист «{title}»: {error[:200]}"


def _header_problem(title: str, issues: Sequence[str]) -> str:
    if tuple(issues) == ("лист пуст",):
        return f"лист «{title}» пуст"
    if all(issue.startswith("колонка ") for issue in issues):
        return f"в листе «{title}» сдвинулись колонки — " + "; ".join(issues)
    return f"лист «{title}»: " + "; ".join(issues)


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
        # повторять одну причину пять раз незачем.
        return Verdict(problem="; ".join(dict.fromkeys(problems)), fingerprint=None)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return Verdict(problem=None, fingerprint=digest)
