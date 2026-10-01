"""Писатель строки карточки: новая карточка — одной безопасной записью.

Первая запись платформы в Google-таблицу (docs/adr/0003): новая строка в
«Лист1» книги карточек. Лист — рабочая площадка шефа и единственная копия
его работы. Поэтому любое неясное состояние здесь кончается отказом с
понятным повару текстом и следом в журнале `sheet_writes`, а не догадкой.

Порядок :meth:`CardSheetWriter.append`:

1. **Значения.** Право записи каждого поля (Q и R — никогда, закрытая книга
   и живые боты — отказ), ровно 20 полей A–P и S–V, числа — в JSON без
   потери точности, пустое — ``null`` (ячейка не трогается). Отказ здесь —
   до первого запроса к Google.
2. **Очередь писателей** в базе. Ждать дольше 30 с — «Таблица занята».
3. **Журнал по ключу запроса.** Попытка с неподтверждённой раскладкой —
   запомнить: удачный ответ её не спрячет. Состоявшаяся запись — ответ без
   единого запроса к Google; незавершённая — прежняя попытка прервалась, её
   строку перечитать: легла — ответ ею (и ничего в ней не трогать: шеф мог
   давно ею пользоваться), не легла — писать заново.
4. **Лист целиком** (FORMATTED) и **шапка** — той же проверкой, что у
   импорта. Колонки съехали — отказ.
5. **Своя прежняя попытка** — строка с нашей ссылкой этикетки в P, где бы
   она ни была: ответ ею, без записи, с тем, что в ней иначе, чем в этой
   отправке. Точный дубль имени с чужой ссылкой — отказ.
6. **Свободная строка N** — первая после последней непустой B, пустая во
   всю ширину; сетка кончилась — дописать строки (``appendDimension``).
7. **Журнал** ``pending`` со снимком «до» — своим коммитом, до записи.
8. **Очередь ещё наша?** Её могла оборвать база — тогда отказ до записи.
9. **Одна запись**: ``values.batchUpdate``, RAW, диапазоны A–P и S–V.
10. **Перечитать и сверить** — и когда запись упала: сначала перечитать.
    Раскладка — по строке N−1 и нашей P в N (FORMATTED), значения N — точно
    (UNFORMATTED). Итог — в журнал: ``verified``; ``rolled_back`` (очищены
    только наши ячейки — только в этом же вызове, в окне в секунду);
    ``failed`` (не легла или раскладку не подтвердили — ничего не чистим,
    полный снимок листа в журнале, ERROR в лог).

Окна, в которых шеф правит лист одновременно с нами, и что с каждым из них
происходит, — в ADR-0003, раздел «Окно гонки».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, NoReturn

from kitchen.db.journal import (
    FAILED,
    LAYOUT_UNCONFIRMED,
    ROLLED_BACK,
    VERIFIED,
    NewWrite,
    OpenWrite,
    SentWrite,
    UnconfirmedWrite,
    WritersBusyError,
)
from kitchen.domain.matching import normalise_name
from kitchen.sync import specs
from kitchen.sync.ownership import Kind
from kitchen.sync.reader import check_header, describe_error, row_hash

# Общая механика писателей (очередь, ошибки, числа, сверка ячейки) живёт в
# sheet_write; здесь она видна под прежними именами — ими пользуются слой
# карточек и тесты писателя.
from kitchen.sync.sheet_write import FORMATTED as FORMATTED
from kitchen.sync.sheet_write import LOCK_TIMEOUT as LOCK_TIMEOUT
from kitchen.sync.sheet_write import SHEET_WRITE_LOCK_KEY as SHEET_WRITE_LOCK_KEY
from kitchen.sync.sheet_write import UNFORMATTED as UNFORMATTED
from kitchen.sync.sheet_write import SentValue as SentValue
from kitchen.sync.sheet_write import SheetBusyError as SheetBusyError
from kitchen.sync.sheet_write import SheetJournal as SheetJournal
from kitchen.sync.sheet_write import SheetUnavailableError as SheetUnavailableError
from kitchen.sync.sheet_write import WriteNotConfirmedError as WriteNotConfirmedError
from kitchen.sync.sheet_write import WriteRefusedError as WriteRefusedError
from kitchen.sync.sheet_write import WritersLock as WritersLock
from kitchen.sync.sheet_write import (
    hold_limit_for,
    joined,
    quoted,
    read_ranges,
    same_cell,
    trouble,
)
from kitchen.sync.sheet_write import sheet_number as sheet_number

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Mapping, Sequence
    from datetime import timedelta

    from kitchen.config import Settings
    from kitchen.sync.client import Cells, SheetsClient, Spreadsheet
    from kitchen.sync.ownership import Column

log = logging.getLogger(__name__)

_WORST_REQUESTS = 12
"""Запросов к Google в худшей записи — с запасом: открыть таблицу и лист,
прочитать, дописать сетку, записать, перечитать дважды, очистить, перечитать
ещё раз — около одиннадцати."""


def hold_limit(settings: Settings) -> timedelta:
    """Сколько одна запись может держать очередь писателей.

    Каждый запрос к Google ждёт не дольше «подключение + ответ» секунд из
    настроек; худшая запись — :data:`_WORST_REQUESTS` таких и минута сверху.
    При (10, 60) — 15 минут. Дольше — процесс завис, и база вправе оборвать
    его транзакцию, отпустив очередь.
    """
    return hold_limit_for(_WORST_REQUESTS, settings)


HOLD_LIMIT = hold_limit_for(_WORST_REQUESTS)
""":func:`hold_limit` для таймаутов Google по умолчанию (10 и 60 с)."""

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

_TITLE = quoted(SPEC.title)

PRIOR_ATTEMPT = "найдена прежняя попытка: строка уже в таблице"


# ---------------------------------------------------------------------------
# Ошибки. Текст каждой — для повара: его показывает экран отправки. Общие
# (WriteRefusedError, WriteNotConfirmedError, SheetBusyError,
# SheetUnavailableError) — в sheet_write.
# ---------------------------------------------------------------------------
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


class LockLostError(SheetBusyError):
    """Базу, державшую очередь писателей, оборвали до записи в лист."""

    def __init__(self) -> None:
        super().__init__(
            "Связь с базой платформы прервалась до записи — в таблице ничего не изменилось. "
            "Черновик сохранён, попробуйте ещё раз."
        )


_NOTHING_CHANGED = (
    "{reason} — в таблице ничего не изменилось. Черновик сохранён, попробуйте ещё раз."
)
_GRID_UNKNOWN = (
    "{reason}, когда в конец таблицы добавлялись пустые строки: они могли добавиться, а "
    "карточка не записана. Черновик сохранён, попробуйте ещё раз."
)


def _unconfirmed_text(row: int, journal_id: int) -> str:
    return (
        f"Таблицу меняли в ту же секунду, запись не подтверждена. Покажите шефу строку "
        f"{row} (запись журнала №{journal_id}). Черновик сохранён."
    )


def _resumed_unconfirmed_text(row: int, journal_id: int) -> str:
    return (
        f"Таблицу меняли, пока прежняя попытка записи оставалась незавершённой, — запись не "
        f"подтверждена. Покажите шефу строку {row} (запись журнала №{journal_id}). Черновик "
        "сохранён."
    )


def _trouble(error: Exception) -> str:
    """Что ответил Google — словами для повара (:func:`~kitchen.sync.sheet_write.trouble`)."""
    return trouble(error, SPEC.title)


@dataclass(frozen=True, slots=True)
class AppendResult:
    """Карточка в листе. Для экрана отправки — строка и две оговорки."""

    row: int
    """Строка листа, где лежит карточка."""
    journal_id: int
    already_written: bool
    """Карточка легла раньше — повтором отправки или прерванной попыткой."""
    not_written: tuple[str, ...] = ()
    """Правки повара, не попавшие в лист, — имена полей, как в ``card_row``.

    Бывает только при ``already_written``: карточка легла раньше, в листе —
    та версия. Поле здесь, если в этой отправке оно не такое, как в легшей
    попытке (что на самом деле записано), **и** не такое, как сейчас в
    листе. Правки шефа в строке после записи сюда не попадают — они только
    в журнале: повару о них говорить незачем."""
    shifted: UnconfirmedWrite | None = None
    """Прежняя попытка этой же отправки, раскладку которой не подтвердили:
    таблицу меняли в окне записи, и чужая карточка могла пострадать. Строку
    и номер журнала повар показывает шефу и при удачном ответе."""


# ---------------------------------------------------------------------------
# Значения строки
# ---------------------------------------------------------------------------
def _serialise(values: Mapping[str, str | Decimal]) -> dict[str, SentValue]:
    """Проверить строку и перевести её в значения тела записи.

    Пустое — ``None`` (null): Google пропускает такую ячейку. Пустая строка
    при RAW стёрла бы то, что шеф успел вписать в нашу строку в окне записи,
    а при сдвиге — лишние ячейки съехавшей карточки. Всё, что здесь не так, —
    отказ до первого запроса к Google.
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
                sent[column.field] = None
            else:
                raise ValueError(f"{where} — число Decimal или пусто, а пришло {value!r}")
        elif isinstance(value, str):
            sent[column.field] = value or None
        else:
            raise ValueError(f"{where} — текст, а пришло {value!r}")

    if not str(sent[_B.field] or "").strip():
        raise WriteRefusedError(
            "Нет названия ингредиента — без него строку не записать. Черновик сохранён."
        )
    if not str(sent[_P.field] or "").strip():
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
    Пустые поля — null: ячейка не трогается.
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


def _read(book: Spreadsheet, cells: str, render: str) -> list[list[object]]:
    """Один диапазон листа (:func:`~kitchen.sync.sheet_write.read_ranges`)."""
    where = f"{_TITLE}!{cells}" if cells else _TITLE
    return read_ranges(book, [where], render)[0]


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


def _differing(sent: Mapping[str, SentValue], cells: Sequence[object]) -> tuple[Column, ...]:
    """Колонки, где в строке листа не то, что в ``sent``."""
    return tuple(c for c in _WRITTEN if not same_cell(sent[c.field], cells[c.index]))


def _letters(columns: Sequence[Column]) -> str:
    return ", ".join(c.letter for c in columns)


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
    previous_holds: bool
    """Строка N−1 та же, что при чтении листа."""
    layout_confirmed: bool
    """Строка N−1 та же, что при чтении, и в N наша ссылка этикетки."""
    untouched: bool
    """Строка N−1 та же, а строка N — как при чтении листа: пуста."""

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
        hold: timedelta = HOLD_LIMIT,
    ) -> None:
        """``hold`` — сколько запись может держать очередь писателей:
        :func:`hold_limit` от настроек таймаутов Google."""
        self._client = client
        self._spreadsheet_id = spreadsheet_id
        self._journal = journal
        self._lock_timeout = lock_timeout
        self._hold = hold

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
            with self._journal.writers_lock(
                SHEET_WRITE_LOCK_KEY, self._lock_timeout, self._hold
            ) as lock:
                result = self._append(lock, sent, actor_id=actor_id, request_key=request_key)
        except WritersBusyError as error:
            raise SheetBusyError from error
        return _announced(result)

    # --- по шагам -----------------------------------------------------------
    def _append(
        self,
        lock: WritersLock,
        sent: Mapping[str, SentValue],
        *,
        actor_id: uuid.UUID | None,
        request_key: str,
    ) -> AppendResult:
        attempts = self._journal.unconfirmed_attempts(request_key)
        shifted = UnconfirmedWrite(attempts[0].id, attempts[0].row) if attempts else None
        prior = self._journal.find_open(request_key)
        if prior is not None and prior.status == VERIFIED:
            # Уже записано: ответ без запросов к Google — тот же, что дал
            # первый повтор. В values записи — что на самом деле записано, в
            # after — строка, какой её видели тогда.
            landed = _sent_from(prior.values, prior.id)
            cooks = _cook_edits(sent, landed, _after_cells(prior, landed))
            return AppendResult(prior.row, prior.id, True, cooks, shifted)

        book = _google("открыть таблицу", lambda: self._client.open(self._spreadsheet_id))
        if prior is not None:
            resumed = self._resume(book, prior, sent, shifted)
            if resumed is not None:
                return resumed

        sheet = _google("открыть лист", lambda: book.worksheet(SPEC.title))
        raw = _google("прочитать лист", lambda: _whole_sheet(book))
        issues = check_header(SPEC, raw)
        if issues:
            log.warning("«%s»: запись остановлена, шапка — %s", SPEC.title, "; ".join(issues))
            raise HeaderDriftError(issues)

        label = sent[_P.field]
        ours = [
            index + 1
            for index in range(SPEC.header_rows, len(raw))
            if _cell(raw[index], _P.index) == label
        ]
        if ours:
            write = NewWrite(
                book=SPEC.spreadsheet,
                sheet=SPEC.title,
                row=ours[0],
                request_key=request_key,
                actor_id=actor_id,
                before={"rows": {str(ours[0]): _snapshot(raw, ours[0])}},
                values=dict(sent),
            )
            return self._found(book, raw, write, sent, attempts, shifted, ours)
        name = str(sent[_B.field])
        conflicts = name_conflicts(raw, name)
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
            _google("дописать строки в сетку", lambda: book.batch_update(grow), _GRID_UNKNOWN)

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
        if not lock.alive():
            self._journal.finish(
                journal_id,
                status=FAILED,
                error="очередь писателей потеряна до записи: транзакцию блокировки оборвала "
                "база — в лист ничего не ушло",
                before=attempt.rows_only(),
            )
            log.error(
                "«%s»: очередь писателей потеряна до записи в строку %s — отказ, журнал №%s",
                SPEC.title,
                row,
                journal_id,
            )
            raise LockLostError
        failure: Exception | None = None
        try:
            book.values_batch_update(_body(sent, row))
        except Exception as error:
            # Исход неясен: Google мог записать и не ответить. Решает
            # перечитывание, а не повтор записи.
            failure = error
        return self._settle(book, attempt, failure, shifted)

    def _found(
        self,
        book: Spreadsheet,
        raw: Cells,
        write: NewWrite,
        sent: Mapping[str, SentValue],
        attempts: Sequence[SentWrite],
        shifted: UnconfirmedWrite | None,
        rows: Sequence[int],
    ) -> AppendResult:
        """Строка с нашей ссылкой этикетки уже в листе — прежняя попытка.

        Писать не надо. Но строку сверяем: после сбоя черновик снова открыт,
        повар мог его поправить, а шеф — строку. Что записано на самом деле —
        отправка легшей попытки (:func:`_landed_attempt`). Правки повара, не
        попавшие в лист, — в ответе; правки шефа — только в журнале. Запись
        журнала хранит в ``values`` записанное, а не нынешнюю отправку: иначе
        следующий повтор коротким путём сверял бы с тем, чего в листе нет.

        ``rows`` — все строки листа с этой ссылкой этикетки, сверху вниз;
        сверяется верхняя. Больше одной — дубль карточки у шефа: номера всех
        — в пометке журнала и в ERROR лога, молча это не проходит.
        """
        row = write.row
        cells = _google("перечитать строку прежней попытки", lambda: _row_values(book, row))
        label = sent[_P.field]
        landing = _landed_attempt([a for a in attempts if a.values.get(_P.field) == label], cells)
        if landing is not None:
            landed = _sent_from(landing.values, landing.id)
            chefs = _differing(landed, cells)
        else:
            # Отправки легшей попытки в журнале нет (журнал чистили, строку
            # писали с другим ключом): за записанное принята строка, какая она
            # сейчас. Правку шефа тогда от записанного не отличить — повару
            # называется всё, чем эта отправка отличается от листа.
            landed = _cells_as_sent(cells)
            chefs = ()
        cooks = _cook_edits(sent, landed, cells)
        line = _snapshot(raw, row)
        twins = ", ".join(str(number) for number in rows) if len(rows) > 1 else ""
        note = joined(
            PRIOR_ATTEMPT,
            f"ссылка этикетки этой отправки в нескольких строках листа: {twins} — сверена "
            f"строка {row}; остальные — дубль карточки, покажите шефу"
            if twins
            else "",
            _chef_note(chefs),
            f"в лист не записаны правки повара: {_letters(_columns(cooks))}" if cooks else "",
            "за записанное принята строка листа: отправки попытки в журнале нет"
            if landing is None
            else "",
            _shifted_note(shifted),
        )
        journal_id = self._journal.found(
            replace(write, values=dict(landed)),
            content_hash=row_hash(line[:_WIDTH]),
            note=note,
            after={
                "rows": {str(row): line[:_WIDTH]},
                "values": cells,
                "not_written": {field: sent[field] for field in cooks},
            },
        )
        log.info("«%s»: строка %s — прежняя попытка, журнал №%s", SPEC.title, row, journal_id)
        if twins:
            log.error(
                "«%s»: ссылка этикетки этой отправки в нескольких строках листа: %s — сверена "
                "строка %s, журнал №%s; остальные — дубль карточки, покажите шефу",
                SPEC.title,
                twins,
                row,
                journal_id,
            )
        return AppendResult(row, journal_id, True, cooks, shifted)

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
                f"{_trouble(error)}: не удалось перечитать строку {row}, и пока неизвестно, "
                "легла ли она. Черновик сохранён — отправьте ещё раз: повтор сначала проверит "
                "эту строку, второй не будет.",
                row=row,
                journal_id=attempt.journal_id,
                layout_confirmed=False,
            ) from error
        previous_holds = _layout_holds(attempt.snapshot(row - 1), previous, row - 1)
        return _Reread(
            previous=previous,
            formatted=formatted,
            values=values,
            previous_holds=previous_holds,
            layout_confirmed=previous_holds and same_cell(attempt.sent[_P.field], values[_P.index]),
            untouched=previous_holds and formatted == attempt.snapshot(row)[:_WIDTH],
        )

    def _settle(
        self,
        book: Spreadsheet,
        attempt: _Attempt,
        failure: Exception | None,
        shifted: UnconfirmedWrite | None,
    ) -> AppendResult:
        """Исход записи в этом же вызове — в окне в секунду после неё."""
        cause = "запись ушла" if failure is None else f"запись упала: {describe_error(failure)}"
        seen = self._reread(book, attempt, cause)
        row = attempt.row
        if seen.layout_confirmed:
            strangers = _differing(attempt.sent, seen.values)
            if strangers:
                self._roll_back(book, attempt, seen, strangers)
            note = joined(
                "" if failure is None else f"{cause}, но строка перечитана — легла",
                _shifted_note(shifted),
            )
            self._finish_verified(attempt, seen, note or None, {})
            return AppendResult(row, attempt.journal_id, False, (), shifted)
        if failure is not None and seen.untouched:
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
        raise self._unconfirmed(
            attempt,
            {"rows": seen.rows(row)},
            _shift_reason(seen),
            _unconfirmed_text(row, attempt.journal_id),
        )

    def _resume(
        self,
        book: Spreadsheet,
        prior: OpenWrite,
        sent: Mapping[str, SentValue],
        shifted: UnconfirmedWrite | None,
    ) -> AppendResult | None:
        """Прежняя попытка с этим ключом прервалась посреди записи.

        Её строку перечитываем. Легла — ответ ею, и **ничего в ней не
        трогаем**: прошли, может быть, часы, и шеф ею уже пользуется —
        правило «чужая ячейка — убрать наши» годится только для окна в
        секунду в том же вызове, что и запись. Что в строке иначе, чем
        отправляла та попытка (правки шефа), — в журнал; правки повара, не
        попавшие в лист, — в ответ. Не легла и лист на месте — попытка
        ``failed``, пишем заново (``None``). Иначе — отказ.
        """
        attempt = _Attempt(
            row=prior.row,
            journal_id=prior.id,
            before=prior.before,
            sent=_sent_from(prior.values, prior.id),
        )
        cause = "прежняя попытка прервалась, исход неизвестен"
        seen = self._reread(book, attempt, cause)
        row = attempt.row
        if seen.layout_confirmed:
            cooks = _cook_edits(sent, attempt.sent, seen.values)
            note = joined(
                f"{cause}; строка перечитана — легла",
                _chef_note(_differing(attempt.sent, seen.values)),
                f"в лист не записаны правки повара: {_letters(_columns(cooks))}" if cooks else "",
                _shifted_note(shifted),
            )
            self._finish_verified(attempt, seen, note, {field: sent[field] for field in cooks})
            return AppendResult(row, attempt.journal_id, True, cooks, shifted)
        if seen.untouched:
            self._journal.finish(
                attempt.journal_id,
                status=FAILED,
                error="прежняя попытка не записала строку — пишем заново",
                before=attempt.rows_only(),
                after={"rows": seen.rows(row)},
            )
            return None
        raise self._unconfirmed(
            attempt,
            {"rows": seen.rows(row)},
            _shift_reason(seen),
            _resumed_unconfirmed_text(row, attempt.journal_id),
        )

    def _finish_verified(
        self,
        attempt: _Attempt,
        seen: _Reread,
        note: str | None,
        not_written: Mapping[str, SentValue],
    ) -> None:
        """Запись состоялась: хеш — ``row_hash`` строки A–V, как её видит шеф,
        вместе с Q и R, — ровно как хеширует импорт. ``values`` записи — то,
        что эта попытка отправила, то есть записанное; в ``after`` —
        перечитанная строка (и числами), по ней короткий путь по ключу
        отвечает на повтор, не спрашивая Google."""
        self._journal.finish(
            attempt.journal_id,
            status=VERIFIED,
            content_hash=row_hash(seen.formatted),
            note=note,
            before=attempt.rows_only(),
            after={
                "rows": seen.rows(attempt.row),
                "values": seen.values,
                "not_written": dict(not_written),
            },
        )

    def _roll_back(
        self, book: Spreadsheet, attempt: _Attempt, seen: _Reread, strangers: Sequence[Column]
    ) -> NoReturn:
        """Строку правили одновременно с нами: очистить только наши ячейки.

        Только в том же вызове, что и запись, и только при подтверждённой
        раскладке — строка N наша. Очистка, а не удаление строки: удаление
        сдвинуло бы номера строк. После очистки строка N−1 перечитывается ещё
        раз: сдвинь шеф строки и в этом окне — чистили, возможно, не там, и
        это разбор по журналу.
        """
        row = attempt.row
        ours = [
            c
            for c in _WRITTEN
            if attempt.sent[c.field] is not None
            and same_cell(attempt.sent[c.field], seen.values[c.index])
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
                attempt,
                after,
                f"очистка своих ячеек не подтверждена: {describe_error(error)}",
                f"{_trouble(error)}, когда из строки {row} убирались наши ячейки, — что в ней "
                f"осталось, неизвестно. Покажите шефу строку {row} (запись журнала "
                f"№{attempt.journal_id}). Черновик сохранён.",
            ) from error
        after["after_clear"] = {str(row - 1): previous, str(row): formatted}
        if not _layout_holds(attempt.snapshot(row - 1), previous, row - 1):
            raise self._unconfirmed(
                attempt,
                after,
                "после очистки своих ячеек строка выше не та — строки сдвигали",
                _unconfirmed_text(row, attempt.journal_id),
            )
        letters = _letters(strangers)
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
        self, attempt: _Attempt, after: Mapping[str, object], reason: str, message: str
    ) -> WriteNotConfirmedError:
        """Раскладку не подтвердили — ``failed``; ошибка для повара — вызывающему.

        Чистить дальше нечего и нельзя: в строке N может лежать чужая
        карточка. В журнале — полный снимок листа: по нему возвращают
        карточку, затёртую сдвигом, даже на несколько строк, — и пометка
        :data:`LAYOUT_UNCONFIRMED`: по ней повтор той же отправки не спрячет
        эту попытку за удачным ответом. Экрана журнала пока нет — поэтому
        ERROR в лог с номером строки и журнала.
        """
        row = attempt.row
        self._journal.finish(
            attempt.journal_id,
            status=FAILED,
            error=reason,
            note=LAYOUT_UNCONFIRMED,
            before=attempt.before,
            after=after,
        )
        log.error(
            "«%s»: запись в строку %s не подтверждена — %s; журнал №%s (полный снимок листа)",
            SPEC.title,
            row,
            reason,
            attempt.journal_id,
        )
        return WriteNotConfirmedError(
            message, row=row, journal_id=attempt.journal_id, layout_confirmed=False
        )


def _announced(result: AppendResult) -> AppendResult:
    """Удачный ответ с оговорками — ещё и в лог: экрана журнала пока нет."""
    if result.shifted is not None:
        log.error(
            "«%s»: карточка в строке %s, но прежняя попытка этой отправки не подтверждена — "
            "покажите шефу строку %s (журнал №%s)",
            SPEC.title,
            result.row,
            result.shifted.row,
            result.shifted.id,
        )
    if result.not_written:
        log.warning(
            "«%s»: карточка уже в строке %s; в лист не записаны правки полей: %s",
            SPEC.title,
            result.row,
            ", ".join(result.not_written),
        )
    return result


def _shift_reason(seen: _Reread) -> str:
    if seen.previous_holds:
        return (
            "в строке нет нашей ссылки на этикетку, строка выше на месте — строку стёрли, "
            "сдвинули или вписали в неё своё; ничего не очищено"
        )
    return "строка выше не та, что при чтении листа, — строки сдвигали; ничего не очищено"


def _as_cell(value: object) -> object:
    """Значение из журнала как ячейка листа: null — пустая ячейка."""
    return "" if value is None else value


def _sent_from(values: Mapping[str, object], journal_id: int) -> dict[str, SentValue]:
    """Отправленное попыткой — из журнала: сверять надо с ним, а не с
    черновиком, который могли поправить между попытками."""
    sent: dict[str, SentValue] = {}
    for field in FIELDS:
        value = values.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, str | int | float)
        ):
            raise ValueError(
                f"в журнале №{journal_id} у поля {field} не значение ячейки: {value!r}"
            )
        sent[field] = value
    return sent


def _landed_attempt(attempts: Sequence[SentWrite], cells: Sequence[object]) -> SentWrite | None:
    """Какая из неподтверждённых попыток с этой ссылкой этикетки легла в строку.

    Та, чья отправка меньше всех расходится со строкой листа: каждая
    неподтверждённая попытка могла лечь, а лежит в строке одна. Новейшая без
    разбора назвала бы правку повара записанной, а записанное — правкой шефа.
    При равном расхождении — новейшая (``attempts`` — новые первыми, ``min``
    берёт первую из равных). Попыток нет — ``None``.
    """
    return min(
        attempts,
        key=lambda attempt: len(_differing(_sent_from(attempt.values, attempt.id), cells)),
        default=None,
    )


def _cells_as_sent(cells: Sequence[object]) -> dict[str, SentValue]:
    """Строка листа как отправка: пустая ячейка — null."""
    sent: dict[str, SentValue] = {}
    for column in _WRITTEN:
        cell = cells[column.index]
        if cell == "" or cell is None:
            sent[column.field] = None
        elif isinstance(cell, str | int | float) and not isinstance(cell, bool):
            sent[column.field] = cell
        else:
            sent[column.field] = str(cell)
    return sent


def _after_cells(prior: OpenWrite, landed: Mapping[str, SentValue]) -> list[object]:
    """Строка, какой её перечитали при завершении записи (числами).

    Нет её в журнале — строкой считается записанное."""
    after = prior.after or {}
    cells = after.get("values")
    if isinstance(cells, list) and len(cells) >= _WIDTH:
        return list(cells)
    line: list[object] = [""] * _WIDTH
    for column in _WRITTEN:
        line[column.index] = _as_cell(landed[column.field])
    return line


def _cook_edits(
    sent: Mapping[str, SentValue], landed: Mapping[str, SentValue], cells: Sequence[object]
) -> tuple[str, ...]:
    """Правки повара, не попавшие в лист: поле этой отправки не такое, как в
    записанном (``landed``), и не такое, как сейчас в листе (``cells``).

    Второе условие — чтобы не звать правкой то, что уже в листе: повар и шеф
    могли поправить поле одинаково. Отличие листа от записанного при
    совпадении с отправкой — правка шефа, повару о ней не говорят.
    """
    return tuple(
        c.field
        for c in _WRITTEN
        if not same_cell(sent[c.field], _as_cell(landed[c.field]))
        and not same_cell(sent[c.field], cells[c.index])
    )


def _columns(fields: Sequence[str]) -> tuple[Column, ...]:
    return tuple(SPEC.column(field) for field in fields)


def _chef_note(changed: Sequence[Column]) -> str:
    return f"с тех пор в листе иначе: {_letters(changed)}" if changed else ""


def _shifted_note(shifted: UnconfirmedWrite | None) -> str:
    if shifted is None:
        return ""
    return f"раскладка прежней попытки не подтверждена — журнал №{shifted.id}, строка {shifted.row}"


def _google[T](what: str, call: Callable[[], T], template: str = _NOTHING_CHANGED) -> T:
    """Запрос к Google до записи карточки: отказ — повару причина словами."""
    try:
        return call()
    except Exception as error:
        log.warning("«%s»: не удалось %s — %s", SPEC.title, what, describe_error(error))
        raise SheetUnavailableError(template.format(reason=_trouble(error))) from error
