"""Общее у писателей листов: очередь, ошибки, числа, сверка ячейки, чтение.

Писателей два, и оба — по ADR-0003: строка карточки в «Лист1» книги
карточек (:mod:`kitchen.sync.writer`) и ручные ячейки строки ING книги кухни
(:mod:`kitchen.sync.reference_writer`). Механика у них одна, поэтому она
здесь, а не в каждом по копии:

* **очередь писателей** — один ключ advisory-блокировки на всех: второй
  писатель ждёт первого, какой бы лист тот ни писал;
* **ошибки** — текст каждой для человека, который нажал кнопку;
* **числа** — JSON-числами без потери точности, а не текстом;
* **сверка ячейки** после записи — точно, по значению;
* **чтение** диапазонов одним запросом.

Самих запросов записи здесь нет: они — в писателях, под очередью, со сверкой
и журналом (правило 9 sheets-guard, храповик в ``tests/unit/test_writer.py``).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from kitchen.sync.reader import describe_error

if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextlib import AbstractContextManager

    from kitchen.config import Settings
    from kitchen.db.journal import NewWrite, OpenWrite, SentWrite
    from kitchen.sync.client import Spreadsheet

SHEET_WRITE_LOCK_KEY = 20260930
"""Ключ advisory-блокировки писателей — свой, не импортный.

Перенос листов в базу стоит в своей очереди (``IMPORT_LOCK_KEY``): запись в
лист его не ждёт, и он — её. Друг друга ждут только писатели."""

LOCK_TIMEOUT = timedelta(seconds=30)
"""Сколько ждать другого писателя. Запись со сверкой — секунды; дольше —
«Таблица занята, попробуйте ещё раз»."""

HOLD_MARGIN = timedelta(seconds=60)
"""Запас сверху к худшей записи в :func:`hold_limit_for`."""

_DEFAULT_REQUEST = timedelta(seconds=10 + 60)
"""Запрос к Google при таймаутах по умолчанию: подключение и ответ."""


def hold_limit_for(worst_requests: int, settings: Settings | None = None) -> timedelta:
    """Сколько одна запись может держать очередь писателей.

    Каждый запрос к Google ждёт не дольше «подключение + ответ» секунд из
    настроек (без настроек — 10 и 60 с); худшая запись — ``worst_requests``
    таких и минута сверху. Дольше — процесс завис, и база вправе оборвать
    его транзакцию, отпустив очередь.
    """
    per_request = (
        _DEFAULT_REQUEST
        if settings is None
        else timedelta(seconds=settings.google_connect_timeout + settings.google_read_timeout)
    )
    return worst_requests * per_request + HOLD_MARGIN


FORMATTED = "FORMATTED_VALUE"
"""Как видит шеф: раскладка листа, снимки, хеш."""
UNFORMATTED = "UNFORMATTED_VALUE"
"""Числа числами: так сверяются записанные значения."""
FORMULA = "FORMULA"
"""Формулы текстом, значения без оформления (числа числами), а вывод чужой
формулы-массива (``QUERY``) — пусто."""

SentValue = str | int | float | None
"""Значение ячейки в теле записи: текст, число JSON или ``None`` — null, «эту
ячейку не трогать»."""


# ---------------------------------------------------------------------------
# Ошибки. Текст каждой — для человека: его показывает экран.
# ---------------------------------------------------------------------------
class WriteRefusedError(RuntimeError):
    """Запись не состоялась или не подтверждена. Ввод человека цел."""


class WriteNotConfirmedError(WriteRefusedError):
    """Запрос записи ушёл, но записанное не подтверждено.

    ``layout_confirmed`` — лист на месте, как при чтении, а наша запись либо
    не легла, либо её правили и наши ячейки убраны. Иначе (False) строки
    сдвигали, возврат своих ячеек не подтвердился или исход неизвестен —
    нужен разбор по журналу ``journal_id``.
    """

    def __init__(self, message: str, *, row: int, journal_id: int, layout_confirmed: bool) -> None:
        super().__init__(message)
        self.row = row
        self.journal_id = journal_id
        self.layout_confirmed = layout_confirmed


class SheetBusyError(WriteRefusedError):
    """Очередь писателей недоступна: занята дольше предела или потеряна."""

    def __init__(self, message: str = "Таблица занята, попробуйте ещё раз.") -> None:
        super().__init__(message)


class SheetUnavailableError(WriteRefusedError):
    """Google отказал до записи — в лист ничего не ушло."""


def trouble(error: Exception, title: str) -> str:
    """Что ответил Google — словами для человека.

    «Не ответил» чинится повтором, «доступ закрыт» и «лист переименован» —
    нет: их путать нельзя. Исходный текст — в лог и журнал. ``title`` — лист,
    который читали или писали.
    """
    raw = describe_error(error)
    lowered = raw.lower()
    if "[errno" in lowered:
        # Ошибка ОС: сеть или файл ключа — не ответ Google о таблице.
        return "Google-таблица не ответила"
    if "[403]" in raw or lowered.startswith("permissionerror"):
        return "Доступ платформы к таблице закрыт"
    if "worksheetnotfound" in lowered or ("[400]" in raw and "parse range" in lowered):
        return f"Лист «{title}» не найден — возможно, его переименовали"
    if "[404]" in raw or "spreadsheetnotfound" in lowered:
        return "Таблица не найдена"
    if "[429]" in raw or "quota" in lowered:
        return "Google ограничил число запросов"
    return "Google-таблица не ответила"


# ---------------------------------------------------------------------------
# Журнал, на который опираются писатели
# ---------------------------------------------------------------------------
class WritersLock(Protocol):
    """Очередь писателей, которую держит писатель."""

    def alive(self) -> bool:
        """Держится ли она ещё — не оборвала ли её база."""
        ...


class SheetJournal(Protocol):
    """Журнал записей и очередь писателей — ``kitchen.db.journal.DbJournal``.

    Протоколом, а не классом: писатели проверяются офлайн на журнале в
    памяти, а настоящий — на Postgres."""

    def writers_lock(
        self, key: int, wait: timedelta, hold: timedelta
    ) -> AbstractContextManager[WritersLock]: ...

    def find_open(self, request_key: str) -> OpenWrite | None: ...

    def unconfirmed_attempts(self, request_key: str) -> tuple[SentWrite, ...]: ...

    def start(self, write: NewWrite) -> int: ...

    def found(
        self,
        write: NewWrite,
        *,
        content_hash: str,
        note: str,
        after: Mapping[str, object] | None = None,
    ) -> int: ...

    def finish(
        self,
        write_id: int,
        *,
        status: str,
        content_hash: str | None = None,
        error: str | None = None,
        note: str | None = None,
        before: Mapping[str, object] | None = None,
        after: Mapping[str, object] | None = None,
    ) -> None: ...

    def annotate(self, write_id: int, *, error: str) -> None: ...


# ---------------------------------------------------------------------------
# Числа и сверка
# ---------------------------------------------------------------------------
def sheet_number(value: Decimal) -> int | float:
    """Число для тела записи: JSON-число без потери точности.

    ``RAW`` кладёт JSON-число числом — шеф сортирует и считает формулами. Но
    ``json.dumps`` не знает ``Decimal``, а через ``float`` число может тихо
    стать другим. Поэтому ``float`` — только на этой границе и только если
    число возвращается из него тем же: ``Decimal(repr(float(d))) == d``.
    Целое уходит целым. Иначе — :class:`WriteRefusedError`; его текст — для
    повара, писатель справочника называет поле формы сам.
    """
    if value.is_finite():
        number: int | float = int(value) if value == value.to_integral_value() else float(value)
        if Decimal(repr(float(number))) == value:
            return number
    shown = format(value, "f").replace(".", ",")
    raise WriteRefusedError(
        f"число {shown} не записать в таблицу точно — проверьте его. Черновик сохранён."
    )


def same_cell(sent: SentValue, got: object) -> bool:
    """Лежит ли в ячейке ровно отправленное.

    ``None`` (null) — ячейка должна быть пустой: чужое в ней — чужое. Текст —
    ``==``, без обрезки пробелов: ``convert_cell`` их срезал бы и спрятал
    правку. Число — ``Decimal(repr(x))``: так сравниваются значения, а не их
    запись.
    """
    if sent is None:
        return got is None or got == ""
    if isinstance(sent, str):
        return isinstance(got, str) and got == sent
    if isinstance(got, bool) or not isinstance(got, int | float):
        return False
    return Decimal(repr(got)) == Decimal(repr(sent))


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------
def quoted(title: str) -> str:
    """Имя листа для диапазона: в кавычках, кавычка внутри удваивается."""
    return "'" + title.replace("'", "''") + "'"


def _matrix(block: object) -> list[list[object]]:
    if not isinstance(block, dict):
        return []
    values = block.get("values")
    if not isinstance(values, list):
        return []
    return [list(line) if isinstance(line, list) else [] for line in values]


def read_ranges(book: Spreadsheet, ranges: list[str], render: str) -> list[list[list[object]]]:
    """Несколько диапазонов одним запросом — по матрице на диапазон.

    ``params`` — свежий словарь: gspread дописывает в него ``ranges``. Ответ
    короче запроса — ошибка, а не молча пустой диапазон.
    """
    payload = book.values_batch_get(ranges, {"valueRenderOption": render})
    blocks = payload.get("valueRanges")
    if not isinstance(blocks, list) or len(blocks) < len(ranges):
        raise RuntimeError(f"Google не вернул диапазон {', '.join(ranges)}")
    return [_matrix(block) for block in blocks[: len(ranges)]]


def joined(*parts: str) -> str:
    """Части пометки журнала через «; » — пустые пропускаются."""
    return "; ".join(part for part in parts if part)
