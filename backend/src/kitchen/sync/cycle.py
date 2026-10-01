"""Цикл синхронизации «лист → база».

Воркер запускает его раз в пять минут, `scripts/import_sheets.py` — вручную.
Логика одна, чтобы ручной перенос не обходил правил автоматического
(спека docs/superpowers/specs/2026-09-23-sinhronizatsiya-design.md):

* **Книга переносится целиком или не переносится.** Лист не прочитан или у
  него сдвинулись колонки — книга в этом цикле не трогается, старые данные
  остаются, причина уходит на сайт. Колонки читаются по позиции: сдвиг дал бы
  цену из колонки веса — тихо и каждые пять минут. База отвергла данные книги
  при переносе — откатывается только эта книга (своя точка сохранения), и
  причина тоже уходит на сайт.
* **Только изменения.** Отпечаток книги совпал с перенесённым — в базу
  пишется одна отметка «проверено».
* **Сбой в журнал — один раз**, когда причина появилась или сменилась. Рядом
  с причиной словами шефа — исходный текст ошибки: по нему её чинят.
* **Не ждать вечно.** Чужая блокировка или зависший запрос роняют цикл по
  пределу, и он повторится через пять минут.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from kitchen.db import models
from kitchen.sync import specs
from kitchen.sync.client import GspreadClient
from kitchen.sync.importer import Importer, ImportResult, take_import_lock
from kitchen.sync.reader import (
    BOOK_OPEN_FAILED,
    BOOK_READ_FAILED,
    SheetsReader,
    describe_error,
    sheet_label,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sqlalchemy.orm import Session, sessionmaker

    from kitchen.config import Settings
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
    details: str | None
    """Исходный текст ошибок читателя — журналу и логу воркера, не сайту.

    Перевод для шефа сводит разные отказы к одной фразе: «доступ закрыт» — и
    нет доступа, и выключенный API. Чинить на бою без исходника — гадать.
    Здесь только то, чего в причине нет: исходник, который уже стоит в ней
    целиком, второй раз не повторяем."""


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

_UNKNOWN = "причина не распознана — сообщите разработчику"
"""Хвост причины для отказа без перевода: сам исходник — в `details`.

Шеф журнала синхронизации не видит — отправлять его туда незачем. Ему нужно
одно: что делать; разработчик найдёт исходник в журнале и логе воркера."""


def explain(title: str, error: str) -> str:
    """Причина сбоя чтения словами, которые поймёт шеф.

    Текст исключения gspread написан для разработчика, а полосу на сайте видят
    все (решение Александра 23.09). Известные случаи переводим. Отказ всей
    таблицы — открытия или пакетного чтения — приходит одинаковым на каждый её
    лист; дело не в листе, и без имени листа одинаковые причины сливаются в
    одну.

    Непереведённое — постоянной фразой: что не удалось (открыть таблицу,
    прочитать таблицу, прочитать лист «X») и что причину нужно передать
    разработчику. Сырого текста исключения здесь нет никогда: причину видит
    каждый вошедший на каждом экране (спека: простыми словами), а в исходнике —
    имя класса, путь к файлу ключа, ответ сервера. Исходник цикл кладёт в
    `details` — в журнал и лог воркера, по нему и чинят. Цена: разные
    неизвестные сбои подряд — одна причина и одно событие журнала; зато
    изменчивый текст неизвестной ошибки не плодит событие каждые пять минут.

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
        return f"не удалось открыть таблицу — {_UNKNOWN}"
    if error.startswith(BOOK_READ_FAILED):
        return f"не удалось прочитать таблицу — {_UNKNOWN}"
    return f"не удалось прочитать лист «{title}» — {_UNKNOWN}"


def explain_import(error: SQLAlchemyError) -> str:
    """Причина, по которой база не приняла перенос книги, — словами для шефа.

    Книга прочиталась, заголовки на месте, а база отвергла значение: доля
    потерь «150» без знака «%» не влезает в колонку, id длиннее 32 знаков.
    Причина в ячейке, и сбой повторяется каждые пять минут, пока её не
    исправят, — поэтому «на данных листа». Какая ячейка — видно по исходнику в
    журнале, его читает разработчик. Прочее (таймаут, обрыв соединения) — без
    догадки про лист.
    """
    if isinstance(error, DataError | IntegrityError):
        return "перенос в базу не удался на данных листа — сообщите разработчику"
    return "перенос в базу не удался — сообщите разработчику"


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
    raw: list[str] = []
    lines: list[str] = []
    for spec in BOOKS[book]:
        data = sheets.get(sheet_label(spec))
        if data is None:
            problems.append(f"лист «{spec.title}» не прочитан")
        elif isinstance(data, str):
            problem = explain(spec.title, data)
            problems.append(problem)
            # Исходник, который читатель сам сказал простыми словами («листа
            # нет», «не задан идентификатор»), стоит в причине дословно — рядом
            # не повторяем. Всё остальное — переведённое или «причина не
            # распознана» — идёт в details: на сайт исходник не попадает.
            if data not in problem:
                raw.append(data)
        elif data.header_issues:
            problems.append(_header_problem(data.title, data.header_issues))
        else:
            lines.extend(f"{spec.title}|{row.number}|{row.content_hash}" for row in data.rows)
    if problems:
        # Отказ открытия таблицы приходит одинаковым на каждый её лист —
        # повторять одну причину пять раз незачем. Причины разных листов
        # разделяет « | »: внутри причины листа уже стоят «; ». Исходники —
        # так же.
        return Verdict(
            problem=" | ".join(dict.fromkeys(problems)),
            fingerprint=None,
            details=" | ".join(dict.fromkeys(raw)) or None,
        )
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return Verdict(problem=None, fingerprint=digest, details=None)


Action = Literal["imported", "unchanged", "failed", "stale"]


@dataclass(frozen=True, slots=True)
class BookOutcome:
    """Чем кончился цикл для книги."""

    action: Action
    problem: str | None = None
    details: str | None = None
    """Исходный текст ошибок читателя (см. `Verdict.details`) — для лога."""


@dataclass(slots=True)
class CycleResult:
    outcomes: dict[str, BookOutcome] = field(default_factory=dict)
    imported: dict[str, ImportResult] = field(default_factory=dict)
    """Итоги переноса по книгам. Книга переносится в своей точке сохранения,
    и прогон журнала у каждой свой."""


LOCK_TIMEOUT = timedelta(seconds=60)
"""Сколько цикл ждёт чужую блокировку. Ручной импорт идёт секунды; минута —
с запасом, а дольше — значит, тот завис."""

STATEMENT_TIMEOUT = timedelta(seconds=120)
"""Предел одного запроса. Перенос целиком идёт секунды; запрос дольше двух
минут — зависший."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reader_from(settings: Settings) -> SheetsReader:
    """Читатель боевых таблиц: воркер и ручной импорт собирают его одинаково."""
    return SheetsReader(
        GspreadClient(settings.google_credentials_path, timeout=settings.google_timeout),
        {
            "kitchen": settings.sheets_id_kitchen,
            "competitors": settings.sheets_id_competitors,
            "ingredient_cards": settings.sheets_id_ingredient_cards,
            "tastings": settings.sheets_id_tastings,
        },
    )


def _limit_waits(session: Session, lock: timedelta, statement: timedelta) -> None:
    """Не ждать вечно ни чужой блокировки, ни зависшего запроса.

    Цикл встаёт в очередь импорта и ждёт, пока другой перенос закончит.
    Зависни тот (ручной импорт, забытый в консоли) — воркер ждал бы вечно, а
    смоук этого не видит: контейнер работает. Упав по пределу, цикл повторится
    через пять минут.

    Только `SET LOCAL` — на эту транзакцию: через пулер в transaction-режиме
    сессионный SET остался бы на чужом соединении. Параметров SET не
    принимает; значение — целые миллисекунды из `timedelta`, не текст извне.
    """
    for name, limit in (("lock_timeout", lock), ("statement_timeout", statement)):
        milliseconds = int(limit / timedelta(milliseconds=1))
        session.execute(text(f"set local {name} = '{milliseconds}ms'"))


def _mark_checked(state: models.SyncState, now: datetime) -> None:
    state.checked_at = now
    state.problem = None
    state.problem_since = None


def _record_failure(
    session: Session,
    state: models.SyncState,
    problem: str,
    details: str | None,
    now: datetime,
) -> None:
    """Причина держится в состоянии книги; в журнал — только новая.

    Новизна — по переведённой причине: исходник той же беды от раза к разу
    разный (в тексте сетевой ошибки — адрес объекта в памяти), и по нему
    журнал пополнялся бы каждые пять минут. Зато в запись журнала исходник
    идёт рядом с причиной: по нему её и чинят.
    """
    if state.problem == problem:
        return
    state.problem = problem
    state.problem_since = now
    note = f"{state.book}: {problem}"
    if details:
        note += f" — {details[:500]}"
    session.add(models.SyncRun(finished_at=now, ok=False, note=note))


class SyncCycle:
    """Один цикл: прочитать книги, решить по каждой, перенести нужное."""

    def __init__(
        self,
        reader: SheetsReader,
        sessions: sessionmaker[Session],
        clock: Callable[[], datetime] = _utcnow,
        *,
        lock_timeout: timedelta = LOCK_TIMEOUT,
        statement_timeout: timedelta = STATEMENT_TIMEOUT,
    ) -> None:
        self._reader = reader
        self._sessions = sessions
        self._clock = clock
        self._lock_timeout = lock_timeout
        self._statement_timeout = statement_timeout

    def run(self, *, force: bool = False, books: Sequence[str] | None = None) -> CycleResult:
        """`force` — переносить и без изменений (ручной запуск).

        `books` — какие книги прочитать и перенести; по умолчанию все. Сразу
        после записи карточки переносится одна книга карточек: карточка
        попадает в базу обычным путём, а кухню незачем ни читать (квота
        Google), ни трогать. Неизвестная книга — ValueError до чтения.
        """
        chosen = tuple(BOOKS) if books is None else tuple(books)
        unknown = [book for book in chosen if book not in BOOKS]
        if unknown or not chosen:
            raise ValueError(
                f"Неизвестные книги: {', '.join(unknown) or 'список пуст'}; есть {', '.join(BOOKS)}"
            )
        selected = [book for book in BOOKS if book in chosen]
        read_started_at = self._clock()
        sheets = self._reader.read_many([spec for book in selected for spec in BOOKS[book]])
        verdicts = {book: judge(book, sheets) for book in selected}
        result = CycleResult()
        importer = Importer(self._reader, self._sessions)

        with self._sessions() as session, session.begin():
            _limit_waits(session, self._lock_timeout, self._statement_timeout)
            take_import_lock(session)
            now = self._clock()
            states = {state.book: state for state in session.scalars(select(models.SyncState))}
            to_import: list[str] = []

            for book, verdict in verdicts.items():
                state = states.get(book)
                if state is None:
                    state = models.SyncState(book=book)
                    session.add(state)
                    states[book] = state

                if state.read_started_at is not None and state.read_started_at > read_started_at:
                    # Пока мы читали, другой импорт перенёс более свежее чтение:
                    # наше старше. Ни переносить его, ни судить по нему о сбое —
                    # причина поверх свежего переноса была бы неправдой.
                    result.outcomes[book] = BookOutcome("stale")
                elif verdict.problem is not None:
                    _record_failure(session, state, verdict.problem, verdict.details, now)
                    result.outcomes[book] = BookOutcome("failed", verdict.problem, verdict.details)
                elif force or state.fingerprint != verdict.fingerprint:
                    to_import.append(book)
                else:
                    _mark_checked(state, now)
                    result.outcomes[book] = BookOutcome("unchanged")

            # До точек сохранения записать всё, что уже накоплено во внешней
            # транзакции (новые строки sync_state, сбои чтения). Иначе это
            # сделал бы flush внутри begin_nested(), его ошибка попала бы в
            # except ниже и была бы приписана книге — а внешняя транзакция уже
            # мертва, и в первом цикле она закрылась бы молча, без коммита.
            # Отсюда ошибка базы летит наружу и громко роняет цикл.
            session.flush()

            # В порядке BOOKS: кухня раньше карточек — пары карточек
            # подбираются по уже обновлённому справочнику.
            for book in to_import:
                state = states[book]
                wanted = {sheet_label(spec) for spec in BOOKS[book]}
                try:
                    # Своя точка сохранения у каждой книги. База отвергла данные
                    # одной — откатывается только она, а другая книга, причина
                    # и событие журнала уходят тем же коммитом. Без этого одна
                    # ячейка шефа останавливала бы всю синхронизацию, а сайт
                    # через 15 минут сказал бы «синхронизация не запущена».
                    with session.begin_nested():
                        imported = importer.apply(
                            session,
                            {label: data for label, data in sheets.items() if label in wanted},
                        )
                except SQLAlchemyError as error:
                    # Только ошибки базы: остальное — поломка кода, её ловит
                    # и пишет в лог цикл воркера.
                    problem = explain_import(error)
                    details = describe_error(error)
                    _record_failure(session, state, problem, details, now)
                    result.outcomes[book] = BookOutcome("failed", problem, details)
                    continue

                result.imported[book] = imported
                fingerprint = verdicts[book].fingerprint
                if state.fingerprint != fingerprint:
                    state.changed_at = now
                state.fingerprint = fingerprint
                state.read_started_at = read_started_at
                _mark_checked(state, now)
                result.outcomes[book] = BookOutcome("imported")

        return result
