"""Писатель строки карточки: новая карточка — одной безопасной записью.

Первая запись платформы в Google-таблицу (docs/adr/0003): новая строка в
«Лист1» книги карточек. Лист — рабочая площадка шефа и единственная копия
его работы. Поэтому любое неясное состояние здесь кончается отказом с
понятным повару текстом и следом в журнале `sheet_writes`, а не догадкой.

Порядок :meth:`CardSheetWriter.append`:

1. **Значения.** Право записи каждого поля (Q и R — никогда, закрытая книга
   и живые боты — отказ), ровно 20 полей A–P и S–V, числа — в JSON без
   потери точности. Отказ здесь — до первого запроса к Google.
2. **Очередь писателей** в базе. Ждать дольше 30 с — «Таблица занята».
3. **Открытая запись журнала** с тем же ключом запроса: состоявшаяся —
   ответ без единого запроса к Google; незавершённая — прежняя попытка
   прервалась, её исход решает перечитывание строки.
4. **Лист целиком** (FORMATTED) и **шапка** — той же проверкой, что у
   импорта. Колонки съехали — отказ.
5. **Точный дубль имени.** С нашей ссылкой на этикетку в P — это наша
   прежняя попытка, строка уже лежит; с чужой — отказ.
6. **Свободная строка N** — первая после последней непустой B, пустая во
   всю ширину; сетка кончилась — дописать строки (``appendDimension``).
7. **Журнал** ``pending`` со снимком «до» — своим коммитом, до записи.
8. **Одна запись**: ``values.batchUpdate``, RAW, диапазоны A–P и S–V.
9. **Перечитать и сверить** — и когда запись упала: сначала перечитать.
   Раскладка — по строке N−1 и нашей P в N (FORMATTED), значения N — точно
   (UNFORMATTED). Итог — в журнал: ``verified``; ``rolled_back`` (очищены
   только наши ячейки); ``failed`` (не легла или раскладку не подтвердили —
   ничего не чистим, полный снимок листа в журнале, ERROR в лог).

Окна, в которых шеф правит лист одновременно с нами, и что с каждым из них
происходит, — в ADR-0003, раздел «Окно гонки».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, NoReturn, Protocol

from kitchen.db.journal import (
    FAILED,
    ROLLED_BACK,
    VERIFIED,
    NewWrite,
    OpenWrite,
    WritersBusyError,
)
from kitchen.domain.matching import normalise_name
from kitchen.sync import specs
from kitchen.sync.ownership import Kind
from kitchen.sync.reader import check_header, describe_error, row_hash

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Mapping, Sequence
    from contextlib import AbstractContextManager

    from kitchen.sync.client import Cells, SheetsClient, Spreadsheet
    from kitchen.sync.ownership import Column

log = logging.getLogger(__name__)

SHEET_WRITE_LOCK_KEY = 20260930
"""Ключ advisory-блокировки писателей — свой, не импортный.

Перенос листов в базу стоит в своей очереди (``IMPORT_LOCK_KEY``): запись в
лист его не ждёт, и он — её. Друг друга ждут только писатели."""

LOCK_TIMEOUT = timedelta(seconds=30)
"""Сколько ждать другого писателя. Запись со сверкой — секунды; дольше —
«Таблица занята, попробуйте ещё раз»."""

SPEC = specs.INGREDIENT_CARDS
"""Единственный лист, в который пишем: «Лист1» книги карточек."""

_P = SPEC.column("label_url")
_B = SPEC.column("name")
_LEFT: tuple[Column, ...] = tuple(c for c in SPEC.columns if c.index <= _P.index)
"""A–P."""
_RIGHT: tuple[Column, ...] = tuple(
    c for c in SPEC.columns if c.index >= SPEC.column("package_url").index
)
"""S–V. Между ними Q и R — колонки людей."""
_WRITTEN: tuple[Column, ...] = (*_LEFT, *_RIGHT)
FIELDS: tuple[str, ...] = tuple(c.field for c in _WRITTEN)
"""Поля строки карточки — ровно 20, в порядке колонок."""
_WIDTH = len(SPEC.columns)
"""A–V: столько колонок сверяется и хешируется, как у импорта."""
_LAST = SPEC.columns[-1].letter

_TITLE = "'" + SPEC.title.replace("'", "''") + "'"
FORMATTED = "FORMATTED_VALUE"
"""Как видит шеф: раскладка листа, снимки, хеш."""
UNFORMATTED = "UNFORMATTED_VALUE"
"""Числа числами: так сверяются записанные значения."""

PRIOR_ATTEMPT = "найдена прежняя попытка: строка уже в таблице"

SentValue = str | int | float
"""Значение ячейки в теле записи: текст или число JSON."""


# ---------------------------------------------------------------------------
# Ошибки. Текст каждой — для повара: его показывает экран отправки.
# ---------------------------------------------------------------------------
class WriteRefusedError(RuntimeError):
    """Запись не состоялась или не подтверждена. Черновик повара цел."""


class HeaderDriftError(WriteRefusedError):
    """Колонки листа съехали — писать по позиции нельзя."""

    def __init__(self, issues: Sequence[str]) -> None:
        super().__init__(
            "В таблице карточек сдвинулись колонки — запись остановлена, в таблице ничего "
            "не изменилось. Черновик сохранён; сообщите шефу."
        )
        self.issues = tuple(issues)


class DuplicateNameError(WriteRefusedError):
    """Карточка с таким именем уже есть в листе."""

    def __init__(self, row: int, name: str) -> None:
        super().__init__(f"«{name}» уже есть в таблице — строка {row}.")
        self.row = row


class WriteNotConfirmedError(WriteRefusedError):
    """Запрос записи ушёл, но записанное не подтверждено.

    ``layout_confirmed`` — лист на месте, как при чтении: строка N−1 та же,
    а наша строка либо не легла, либо её правили и наши ячейки убраны.
    Иначе (False) строки сдвигали или исход неизвестен — нужен разбор по
    журналу ``journal_id``.
    """

    def __init__(self, message: str, *, row: int, journal_id: int, layout_confirmed: bool) -> None:
        super().__init__(message)
        self.row = row
        self.journal_id = journal_id
        self.layout_confirmed = layout_confirmed


class SheetBusyError(WriteRefusedError):
    """Другой писатель держит очередь дольше предела."""

    def __init__(self) -> None:
        super().__init__("Таблица занята, попробуйте ещё раз.")


class SheetUnavailableError(WriteRefusedError):
    """Google не ответил до записи — в лист ничего не ушло."""

    def __init__(self) -> None:
        super().__init__(
            "Google-таблица не ответила — в таблице ничего не изменилось. Черновик "
            "сохранён, попробуйте ещё раз."
        )


def _unconfirmed_text(row: int, journal_id: int) -> str:
    return (
        f"Таблицу меняли в ту же секунду, запись не подтверждена. Покажите шефу строку "
        f"{row} (запись журнала №{journal_id}). Черновик сохранён."
    )


# ---------------------------------------------------------------------------
# Журнал, на который опирается писатель
# ---------------------------------------------------------------------------
class SheetJournal(Protocol):
    """Журнал записей и очередь писателей — ``kitchen.db.journal.DbJournal``.

    Протоколом, а не классом: писатель проверяется офлайн на журнале в
    памяти, а настоящий — на Postgres."""

    def writers_lock(self, key: int, timeout: timedelta) -> AbstractContextManager[None]: ...

    def find_open(self, request_key: str) -> OpenWrite | None: ...

    def start(self, write: NewWrite) -> int: ...

    def found(self, write: NewWrite, *, content_hash: str, note: str) -> int: ...

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


@dataclass(frozen=True, slots=True)
class AppendResult:
    row: int
    """Строка листа, где лежит карточка."""
    journal_id: int
    already_written: bool
    """Карточка легла раньше — повтором отправки или прерванной попыткой."""


# ---------------------------------------------------------------------------
# Значения строки
# ---------------------------------------------------------------------------
def sheet_number(value: Decimal) -> int | float:
    """Число для тела записи: JSON-число без потери точности.

    ``RAW`` кладёт JSON-число числом — шеф сортирует и считает формулами. Но
    ``json.dumps`` не знает ``Decimal``, а через ``float`` число может тихо
    стать другим. Поэтому ``float`` — только на этой границе и только если
    число возвращается из него тем же: ``Decimal(repr(float(d))) == d``.
    Целое уходит целым. Иначе — :class:`WriteRefusedError`.
    """
    if value.is_finite():
        number: int | float = int(value) if value == value.to_integral_value() else float(value)
        if Decimal(repr(float(number))) == value:
            return number
    shown = format(value, "f").replace(".", ",")
    raise WriteRefusedError(
        f"число {shown} не записать в таблицу точно — проверьте его. Черновик сохранён."
    )


def _serialise(values: Mapping[str, str | Decimal]) -> dict[str, SentValue]:
    """Проверить строку и перевести её в значения тела записи.

    Всё, что здесь не так, — отказ до первого запроса к Google.
    """
    # Право записи — первым: Q, R, закрытая книга и живые боты отказывают
    # по правилу владения, а не по виду значения.
    SPEC.check_writable(*values)
    missing = [field for field in FIELDS if field not in values]
    if missing:
        raise ValueError(
            f"Строка карточки — ровно {len(FIELDS)} полей A–P и S–V; нет: {', '.join(missing)}"
        )

    sent: dict[str, SentValue] = {}
    for column in _WRITTEN:
        value = values[column.field]
        where = f"колонка {column.letter} ({column.title})"
        if column.kind is Kind.DECIMAL:
            if isinstance(value, Decimal):
                try:
                    sent[column.field] = sheet_number(value)
                except WriteRefusedError as error:
                    raise WriteRefusedError(f"{column.title}: {error}") from None
            elif value == "":
                sent[column.field] = ""
            else:
                raise ValueError(f"{where} — число Decimal или пусто, а пришло {value!r}")
        elif isinstance(value, str):
            sent[column.field] = value
        else:
            raise ValueError(f"{where} — текст, а пришло {value!r}")

    if not str(sent[_B.field]).strip():
        raise WriteRefusedError(
            "Нет названия ингредиента — без него строку не записать. Черновик сохранён."
        )
    if not str(sent[_P.field]).strip():
        raise WriteRefusedError(
            "Нет ссылки на фото этикетки — по ней записанная строка опознаётся в таблице, "
            "без неё писать нельзя. Черновик сохранён."
        )
    return sent


def _body(sent: Mapping[str, SentValue], row: int) -> dict[str, object]:
    ranges = []
    for part in (_LEFT, _RIGHT):
        cells = f"{part[0].letter}{row}:{part[-1].letter}{row}"
        ranges.append({"range": f"{_TITLE}!{cells}", "values": [[sent[c.field] for c in part]]})
    return {"valueInputOption": "RAW", "data": ranges}


def row_payload(values: Mapping[str, str | Decimal], row: int) -> dict[str, object]:
    """Тело ``values.batchUpdate`` для строки ``row``.

    RAW: текст с этикетки, начинающийся с «=», остаётся текстом. Два
    диапазона, A–P и S–V: Q и R в теле нет даже пустыми — это колонки людей.
    """
    return _body(_serialise(values), row)


# ---------------------------------------------------------------------------
# Чтение листа
# ---------------------------------------------------------------------------
def _cell(line: Sequence[object], index: int) -> object:
    return line[index] if index < len(line) else ""


def find_free_row(raw: Cells) -> int:
    """Номер свободной строки (с единицы).

    Первая после последней непустой B — ключевой колонки, — и пустая во всю
    ширину: A–V и дальше. Не ``append_row`` и не «после последней
    заполненной»: заметка шефа ниже таблицы уводила бы запись вниз (разбор
    04.08.2026). Строка, где заполнена хоть одна ячейка, — чужая, даже если
    B пуста: мы её не трогаем и берём следующую.
    """
    last = SPEC.header_rows
    for index in range(SPEC.header_rows, len(raw)):
        if str(_cell(raw[index], _B.index)).strip():
            last = index + 1
    row = last + 1
    while row <= len(raw) and any(cell != "" for cell in raw[row - 1]):
        row += 1
    return row


def name_conflicts(raw: Cells, name: str) -> tuple[int, ...]:
    """Строки листа, где имя совпало с ``name`` после нормализации.

    Похожие не в счёт: это подсказка повару, а не отказ записи.
    """
    wanted = normalise_name(name)
    if not wanted:
        return ()
    return tuple(
        index + 1
        for index in range(SPEC.header_rows, len(raw))
        if normalise_name(str(_cell(raw[index], _B.index))) == wanted
    )


def _padded(line: Sequence[object], width: int = _WIDTH) -> list[str]:
    cells = [str(cell) for cell in line]
    return cells + [""] * (width - len(cells))


def _snapshot(raw: Cells, number: int) -> list[str]:
    """Строка листа целиком — с Q, R и всем, что правее V."""
    line = raw[number - 1] if 0 < number <= len(raw) else []
    return _padded(line, max(_WIDTH, len(line)))


def _matrix(block: object) -> list[list[object]]:
    if not isinstance(block, dict):
        return []
    values = block.get("values")
    if not isinstance(values, list):
        return []
    return [list(line) if isinstance(line, list) else [] for line in values]


def _read(book: Spreadsheet, cells: str, render: str) -> list[list[object]]:
    """Один диапазон листа. ``params`` — свежий словарь: gspread дописывает
    в него ``ranges``."""
    where = f"{_TITLE}!{cells}" if cells else _TITLE
    payload = book.values_batch_get([where], {"valueRenderOption": render})
    blocks = payload.get("valueRanges")
    if not isinstance(blocks, list) or not blocks:
        raise RuntimeError(f"Google не вернул диапазон {where}")
    return _matrix(blocks[0])


def _whole_sheet(book: Spreadsheet) -> Cells:
    return [[str(cell) for cell in line] for line in _read(book, "", FORMATTED)]


def _two_rows(book: Spreadsheet, row: int) -> tuple[list[str], list[str]]:
    """Строки N−1 и N, шириной A–V, как видит шеф."""
    lines = _read(book, f"A{row - 1}:{_LAST}{row}", FORMATTED)
    lines += [[]] * (2 - len(lines))
    return _padded(lines[0]), _padded(lines[1])


def _row_values(book: Spreadsheet, row: int) -> list[object]:
    """Строка N, шириной A–V, числа — числами."""
    lines = _read(book, f"A{row}:{_LAST}{row}", UNFORMATTED)
    line = lines[0] if lines else []
    return [*line, *[""] * (_WIDTH - len(line))]


def _same(sent: SentValue, got: object) -> bool:
    """Лежит ли в ячейке ровно отправленное.

    Текст — ``==``, без обрезки пробелов: ``convert_cell`` их срезал бы и
    спрятал правку. Число — ``Decimal(repr(x))``: так сравниваются значения,
    а не их запись.
    """
    if isinstance(sent, str):
        return isinstance(got, str) and got == sent
    if isinstance(got, bool) or not isinstance(got, int | float):
        return False
    return Decimal(repr(got)) == Decimal(repr(sent))


def _layout_holds(snapshot: Sequence[str], now: Sequence[str], number: int) -> bool:
    """Та ли это строка N−1, что при чтении листа.

    Карточка сверяется по B и P — имени и ссылке этикетки: шеф, дописавший
    в окне записи Q или R свежей карточке, сдвигом не считается. Строка без
    имени — вторая строка шапки или чужая строка, которую мы пропустили, —
    сверяется целиком: по пустым B и P сдвиг не виден.
    """
    before, after = _padded(snapshot)[:_WIDTH], _padded(now)[:_WIDTH]
    if number > SPEC.header_rows and before[_B.index].strip():
        return (after[_B.index], after[_P.index]) == (before[_B.index], before[_P.index])
    return after == before


# ---------------------------------------------------------------------------
# Писатель
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Attempt:
    """Запись в строку ``row``, заведённая в журнале."""

    row: int
    journal_id: int
    before: Mapping[str, object]
    """Снимок «до»: строки N−1 и N и весь лист."""
    sent: Mapping[str, SentValue]

    def snapshot(self, number: int) -> list[str]:
        rows = self.before.get("rows")
        line = rows.get(str(number)) if isinstance(rows, dict) else None
        if not isinstance(line, list):
            raise ValueError(f"в журнале №{self.journal_id} нет снимка строки {number}")
        return [str(cell) for cell in line]

    def rows_only(self) -> dict[str, object]:
        """Снимок без листа целиком — у записи, раскладка которой подтверждена."""
        return {"rows": self.before.get("rows")}


@dataclass(frozen=True, slots=True)
class _Reread:
    """Что увидели в листе после записи."""

    previous: list[str]
    formatted: list[str]
    values: list[object]

    def rows(self, row: int) -> dict[str, object]:
        return {str(row - 1): self.previous, str(row): self.formatted}


class CardSheetWriter:
    """Добавляет карточку строкой в «Лист1» книги карточек. Единственный путь
    записи в таблицу (правило 9 sheets-guard): очередь писателей в базе, сверка
    и журнал — у каждой записи."""

    def __init__(
        self,
        client: SheetsClient,
        spreadsheet_id: str,
        journal: SheetJournal,
        *,
        lock_timeout: timedelta = LOCK_TIMEOUT,
    ) -> None:
        self._client = client
        self._spreadsheet_id = spreadsheet_id
        self._journal = journal
        self._lock_timeout = lock_timeout

    def append(
        self,
        values: Mapping[str, str | Decimal],
        *,
        actor_id: uuid.UUID | None,
        request_key: str,
    ) -> AppendResult:
        """Добавить строку карточки. ``values`` — 20 полей A–P и S–V (``card_row``).

        ``request_key`` — ключ отправки, стабильный на черновик: повтор с тем
        же ключом второй строки не даёт. Ошибки — :class:`WriteRefusedError` и
        наследники, текст — для повара.
        """
        sent = _serialise(values)
        try:
            lock = self._journal.writers_lock(SHEET_WRITE_LOCK_KEY, self._lock_timeout)
            with lock:
                return self._append(sent, actor_id=actor_id, request_key=request_key)
        except WritersBusyError as error:
            raise SheetBusyError from error

    # --- по шагам -----------------------------------------------------------
    def _append(
        self, sent: Mapping[str, SentValue], *, actor_id: uuid.UUID | None, request_key: str
    ) -> AppendResult:
        prior = self._journal.find_open(request_key)
        if prior is not None and prior.status == VERIFIED:
            return AppendResult(prior.row, prior.id, already_written=True)

        book = _google("открыть таблицу", lambda: self._client.open(self._spreadsheet_id))
        if prior is not None:
            resumed = self._resume(book, prior)
            if resumed is not None:
                return resumed

        sheet = _google("открыть лист", lambda: book.worksheet(SPEC.title))
        raw = _google("прочитать лист", lambda: _whole_sheet(book))
        issues = check_header(SPEC, raw)
        if issues:
            log.warning("«%s»: запись остановлена, шапка — %s", SPEC.title, "; ".join(issues))
            raise HeaderDriftError(issues)

        name, label = str(sent[_B.field]), str(sent[_P.field])
        conflicts = name_conflicts(raw, name)
        ours = [row for row in conflicts if _cell(raw[row - 1], _P.index) == label]
        if ours:
            return self._found(raw, ours[0], sent, actor_id=actor_id, request_key=request_key)
        if conflicts:
            raise DuplicateNameError(conflicts[0], name)

        row = find_free_row(raw)
        if row > sheet.row_count:
            grow = {
                "requests": [
                    {
                        "appendDimension": {
                            "sheetId": sheet.id,
                            "dimension": "ROWS",
                            "length": row - sheet.row_count,
                        }
                    }
                ]
            }
            _google("дописать строки в сетку", lambda: book.batch_update(grow))

        before: dict[str, object] = {
            "rows": {str(row - 1): _snapshot(raw, row - 1), str(row): _snapshot(raw, row)},
            "sheet": raw,
        }
        journal_id = self._journal.start(
            NewWrite(
                book=SPEC.spreadsheet,
                sheet=SPEC.title,
                row=row,
                request_key=request_key,
                actor_id=actor_id,
                before=before,
                values=dict(sent),
            )
        )
        attempt = _Attempt(row=row, journal_id=journal_id, before=before, sent=sent)
        failure: Exception | None = None
        try:
            book.values_batch_update(_body(sent, row))
        except Exception as error:
            # Исход неясен: Google мог записать и не ответить. Решает
            # перечитывание, а не повтор записи.
            failure = error
        return self._settle(book, attempt, failure)

    def _found(
        self,
        raw: Cells,
        row: int,
        sent: Mapping[str, SentValue],
        *,
        actor_id: uuid.UUID | None,
        request_key: str,
    ) -> AppendResult:
        """Наша прежняя попытка: имя и ссылка этикетки уже в листе."""
        line = _snapshot(raw, row)
        journal_id = self._journal.found(
            NewWrite(
                book=SPEC.spreadsheet,
                sheet=SPEC.title,
                row=row,
                request_key=request_key,
                actor_id=actor_id,
                before={"rows": {str(row): line}},
                values=dict(sent),
            ),
            content_hash=row_hash(line[:_WIDTH]),
            note=PRIOR_ATTEMPT,
        )
        log.info(
            "«%s»: строка %s уже наша — прежняя попытка, журнал №%s", SPEC.title, row, journal_id
        )
        return AppendResult(row, journal_id, already_written=True)

    def _reread(self, book: Spreadsheet, attempt: _Attempt, cause: str) -> _Reread:
        """Перечитать строки N−1 и N. Не вышло — исход неизвестен: журнал
        остаётся ``pending``, повтор с тем же ключом перечитает снова."""
        row = attempt.row
        try:
            previous, formatted = _two_rows(book, row)
            values = _row_values(book, row)
        except Exception as error:
            reason = f"{cause}; перечитать строку не удалось: {describe_error(error)}"
            self._journal.annotate(attempt.journal_id, error=reason)
            log.error(
                "«%s»: исход записи в строку %s неизвестен, журнал №%s остаётся pending — %s",
                SPEC.title,
                row,
                attempt.journal_id,
                reason,
            )
            raise WriteNotConfirmedError(
                f"Google не ответил, и пока неизвестно, легла ли строка {row}. Черновик "
                "сохранён — отправьте ещё раз: повтор сначала проверит эту строку, второй "
                "не будет.",
                row=row,
                journal_id=attempt.journal_id,
                layout_confirmed=False,
            ) from error
        return _Reread(previous=previous, formatted=formatted, values=values)

    def _settle(
        self, book: Spreadsheet, attempt: _Attempt, failure: Exception | None
    ) -> AppendResult:
        cause = "запись ушла" if failure is None else f"запись упала: {describe_error(failure)}"
        seen = self._reread(book, attempt, cause)
        row = attempt.row
        previous_holds = _layout_holds(attempt.snapshot(row - 1), seen.previous, row - 1)
        if previous_holds and _same(attempt.sent[_P.field], seen.values[_P.index]):
            note = None if failure is None else f"{cause}, но строка перечитана — легла"
            return self._ours(book, attempt, seen, note=note, already_written=False)
        if (
            failure is not None
            and previous_holds
            and seen.formatted == attempt.snapshot(row)[:_WIDTH]
        ):
            self._journal.finish(
                attempt.journal_id,
                status=FAILED,
                error=f"запись не легла: {describe_error(failure)}",
                before=attempt.rows_only(),
                after={"rows": seen.rows(row)},
            )
            log.warning("«%s»: запись в строку %s не легла — %s", SPEC.title, row, cause)
            raise WriteNotConfirmedError(
                "Google не принял запись — в таблице ничего не изменилось. Черновик сохранён, "
                "отправьте ещё раз.",
                row=row,
                journal_id=attempt.journal_id,
                layout_confirmed=True,
            ) from failure
        raise self._unconfirmed(attempt, {"rows": seen.rows(row)}, _shift_reason(previous_holds))

    def _resume(self, book: Spreadsheet, prior: OpenWrite) -> AppendResult | None:
        """Прежняя попытка с этим ключом прервалась посреди записи.

        Её строку перечитываем и решаем тем же правилом, что после записи.
        Легла — повтор отвечает ею. Не легла и лист на месте — попытка
        отмечается ``failed``, и пишем заново (``None``). Иначе — отказ.
        """
        attempt = _Attempt(
            row=prior.row,
            journal_id=prior.id,
            before=prior.before,
            sent=_sent_from_journal(prior),
        )
        cause = "прежняя попытка прервалась, исход неизвестен"
        seen = self._reread(book, attempt, cause)
        row = attempt.row
        previous_holds = _layout_holds(attempt.snapshot(row - 1), seen.previous, row - 1)
        if previous_holds and _same(attempt.sent[_P.field], seen.values[_P.index]):
            note = f"{cause}; строка перечитана — легла"
            return self._ours(book, attempt, seen, note=note, already_written=True)
        if previous_holds and seen.formatted == attempt.snapshot(row)[:_WIDTH]:
            self._journal.finish(
                attempt.journal_id,
                status=FAILED,
                error="прежняя попытка не записала строку — пишем заново",
                before=attempt.rows_only(),
                after={"rows": seen.rows(row)},
            )
            return None
        raise self._unconfirmed(attempt, {"rows": seen.rows(row)}, _shift_reason(previous_holds))

    def _ours(
        self,
        book: Spreadsheet,
        attempt: _Attempt,
        seen: _Reread,
        *,
        note: str | None,
        already_written: bool,
    ) -> AppendResult:
        """Раскладка подтверждена: строка N−1 та же, в N наша ссылка этикетки.

        Все 20 ячеек совпали с отправленным — запись состоялась, в журнал
        ``verified`` и хеш перечитанной строки: ``row_hash`` строки A–V как её
        видит шеф, вместе с Q и R, — ровно как хеширует импорт. Чужое хоть в
        одной — откат наших ячеек."""
        strangers = [c for c in _WRITTEN if not _same(attempt.sent[c.field], seen.values[c.index])]
        if strangers:
            self._roll_back(book, attempt, seen, strangers)
        row = attempt.row
        self._journal.finish(
            attempt.journal_id,
            status=VERIFIED,
            content_hash=row_hash(seen.formatted),
            note=note,
            before=attempt.rows_only(),
            after={"rows": seen.rows(row)},
        )
        return AppendResult(row, attempt.journal_id, already_written=already_written)

    def _roll_back(
        self, book: Spreadsheet, attempt: _Attempt, seen: _Reread, strangers: Sequence[Column]
    ) -> NoReturn:
        """Строку правили одновременно с нами: очистить только наши ячейки.

        Только при подтверждённой раскладке — строка N наша. Очистка, а не
        удаление строки: удаление сдвинуло бы номера строк. После очистки
        строка N−1 перечитывается ещё раз: сдвинь шеф строки и в этом окне —
        чистили, возможно, не там, и это разбор по журналу.
        """
        row = attempt.row
        ours = [
            c
            for c in _WRITTEN
            if attempt.sent[c.field] != "" and _same(attempt.sent[c.field], seen.values[c.index])
        ]
        after: dict[str, object] = {"rows": seen.rows(row), "cleared": [c.letter for c in ours]}
        try:
            if ours:
                book.values_batch_clear(
                    body={"ranges": [f"{_TITLE}!{c.letter}{row}" for c in ours]}
                )
            previous, formatted = _two_rows(book, row)
        except Exception as error:
            raise self._unconfirmed(
                attempt, after, f"очистка своих ячеек не подтверждена: {describe_error(error)}"
            ) from error
        after["after_clear"] = {str(row - 1): previous, str(row): formatted}
        if not _layout_holds(attempt.snapshot(row - 1), previous, row - 1):
            raise self._unconfirmed(
                attempt, after, "после очистки своих ячеек строка выше не та — строки сдвигали"
            )
        letters = ", ".join(c.letter for c in strangers)
        self._journal.finish(
            attempt.journal_id,
            status=ROLLED_BACK,
            error=f"в строке {row} чужие значения в колонках {letters}; очищены только наши ячейки",
            before=attempt.rows_only(),
            after=after,
        )
        log.warning(
            "«%s»: строку %s правили одновременно с записью (%s) — наши ячейки очищены, журнал №%s",
            SPEC.title,
            row,
            letters,
            attempt.journal_id,
        )
        raise WriteNotConfirmedError(
            f"В строку {row} одновременно с нами вписали своё — наши ячейки убраны, чужие не "
            "тронуты. Черновик сохранён, отправьте ещё раз.",
            row=row,
            journal_id=attempt.journal_id,
            layout_confirmed=True,
        )

    def _unconfirmed(
        self, attempt: _Attempt, after: Mapping[str, object], reason: str
    ) -> WriteNotConfirmedError:
        """Раскладку не подтвердили — ``failed``; ошибка для повара — вызывающему.

        Чистить дальше нечего и нельзя: в строке N может лежать чужая
        карточка. В журнале — полный снимок листа: по нему возвращают
        карточку, затёртую сдвигом, даже на несколько строк. Экрана журнала
        пока нет — поэтому ERROR в лог с номером строки и журнала.
        """
        row = attempt.row
        self._journal.finish(
            attempt.journal_id, status=FAILED, error=reason, before=attempt.before, after=after
        )
        log.error(
            "«%s»: запись в строку %s не подтверждена — %s; журнал №%s (полный снимок листа)",
            SPEC.title,
            row,
            reason,
            attempt.journal_id,
        )
        return WriteNotConfirmedError(
            _unconfirmed_text(row, attempt.journal_id),
            row=row,
            journal_id=attempt.journal_id,
            layout_confirmed=False,
        )


def _shift_reason(previous_holds: bool) -> str:
    if previous_holds:
        return (
            "в строке нет нашей ссылки на этикетку, строка выше на месте — строку стёрли "
            "или сдвинули; ничего не очищено"
        )
    return "строка выше не та, что при чтении листа, — строки сдвигали; ничего не очищено"


def _sent_from_journal(prior: OpenWrite) -> dict[str, SentValue]:
    """Отправленное прежней попыткой — из журнала: сверять надо с ним, а не
    с черновиком, который могли поправить между попытками."""
    sent: dict[str, SentValue] = {}
    for field in FIELDS:
        value = prior.values.get(field)
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise ValueError(f"в журнале №{prior.id} у поля {field} не значение ячейки: {value!r}")
        sent[field] = value
    return sent


def _google[T](what: str, call: Callable[[], T]) -> T:
    """Запрос к Google до записи: отказ — в лист ничего не ушло."""
    try:
        return call()
    except Exception as error:
        log.warning("«%s»: не удалось %s — %s", SPEC.title, what, describe_error(error))
        raise SheetUnavailableError from error
