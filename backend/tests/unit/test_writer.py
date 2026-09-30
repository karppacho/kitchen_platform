"""Писатель строки карточки — без сети и без базы.

Первая запись платформы в рабочую таблицу шефа. Ошибка здесь сервис не
роняет — она молча портит единственную копию его работы. Поэтому тесты
проверяют не только удачную запись, но и каждое окно, в котором шеф правит
лист одновременно с нами: до записи, после неё, после отката.

Лист по умолчанию — двустрочная шапка и три карточки в строках 3–5, так что
свободная строка — 6. У «Майонеза» в строке 5 заполнена декларация (Q), а
категория, поставщик и «Да» совпадают с нашей карточкой: так видно, что
пропало бы при неосторожном откате.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import requests

from kitchen.config import Settings
from kitchen.db.journal import FAILED, PENDING, ROLLED_BACK, VERIFIED, UnconfirmedWrite
from kitchen.domain.cards import APPROVED, CardDraftData, card_row, drive_view_url
from kitchen.sync import ownership, specs
from kitchen.sync.ownership import ForbiddenWriteError
from kitchen.sync.reader import SheetsReader
from kitchen.sync.writer import (
    HOLD_LIMIT,
    SHEET_WRITE_LOCK_KEY,
    AppendResult,
    CardSheetWriter,
    DuplicateNameError,
    HeaderDriftError,
    LockLostError,
    SheetBusyError,
    SheetUnavailableError,
    WriteNotConfirmedError,
    WriteRefusedError,
    find_free_row,
    hold_limit,
    name_conflicts,
    row_payload,
    sheet_number,
)
from tests.conftest import FakeSheetsClient, FakeSpreadsheet, FakeWorksheet, api_error
from tests.fake_journal import FakeJournal
from tests.fake_sheets import header, row

if TYPE_CHECKING:
    from collections.abc import Mapping

SPEC = specs.INGREDIENT_CARDS
WIDTH = len(SPEC.columns)
KEY = "card-draft:1"
LOG = "kitchen.sync"
WRITES = ("values_batch_update", "values_batch_clear", "batch_update")


def _index(field: str) -> int:
    return SPEC.column(field).index


EXISTING = (
    row(
        SPEC,
        category="Соусы",
        name="Кетчуп",
        supplier="Север",
        label_url=drive_view_url("k1"),
        approval_status=APPROVED,
    ),
    row(
        SPEC,
        category="Сыры",
        name="Моцарелла",
        supplier="Юг",
        label_url=drive_view_url("m1"),
        approval_status=APPROVED,
    ),
    row(
        SPEC,
        category="Соусы",
        name="Майонез",
        supplier="Север",
        label_url=drive_view_url("y1"),
        declaration="есть",
        approval_status=APPROVED,
    ),
)
"""Три карточки: строки 3, 4 и 5."""


def _cards(*lines: list[str]) -> list[list[str]]:
    """Лист карточек: шапка в две строки (вторая пуста), затем строки данных."""
    return [header(SPEC), [], *lines]


def _values(name: str = "Соус Барбекю", label: str = "lbl1") -> dict[str, str | Decimal]:
    """Строка карточки так, как её соберёт отправка: доменным ``card_row``."""
    return card_row(
        CardDraftData(
            supplier="Север",
            category="Соусы",
            name=name,
            manufacturer="Завод",
            composition="томаты, сахар",
            protein=Decimal("0.1"),
            fat=Decimal("12.5"),
            carbs=Decimal("12.000"),
            kcal=None,
            approval=APPROVED,
            label_file_id=label,
        )
    )


def _pad(line: list[str]) -> list[str]:
    return line + [""] * (WIDTH - len(line))


@dataclass
class Rig:
    """Писатель со своей фальшивой таблицей и журналом."""

    writer: CardSheetWriter
    book: FakeSpreadsheet
    sheet: FakeWorksheet
    journal: FakeJournal
    client: FakeSheetsClient

    def append(
        self, values: Mapping[str, str | Decimal] | None = None, *, key: str = KEY
    ) -> AppendResult:
        return self.writer.append(
            values if values is not None else _values(), actor_id=None, request_key=key
        )

    def line(self, number: int) -> list[str]:
        """Строка листа, как её видит шеф (FORMATTED), шириной A–V.

        Читается мимо таблицы — не добавляет запросов, которые считают тесты."""
        cells = self.sheet.get_all_values()
        return _pad(cells[number - 1][:WIDTH]) if number <= len(cells) else [""] * WIDTH

    def writes(self) -> list[str]:
        """Запросы, меняющие таблицу, — по порядку."""
        return [name for name, _ in self.book.calls if name in WRITES]

    def sent(self, method: str) -> list[dict[str, object]]:
        return [payload for name, payload in self.book.calls if name == method]


def _rig(
    lines: tuple[list[str], ...] = EXISTING,
    *,
    cells: list[list[str]] | None = None,
    rows: int | None = None,
    lock_timeout: timedelta = timedelta(seconds=30),
) -> Rig:
    sheet = FakeWorksheet(cells if cells is not None else _cards(*lines), "Лист1", row_count=rows)
    book = FakeSpreadsheet({"Лист1": sheet})
    client = FakeSheetsClient({"cards-id": book})
    journal = FakeJournal()
    writer = CardSheetWriter(client, "cards-id", journal, lock_timeout=lock_timeout)
    return Rig(writer, book, sheet, journal, client)


def _nothing_asked(rig: Rig) -> None:
    """Отказ случился до первого запроса к Google и до блокировки писателей."""
    assert rig.book.requests == 0
    assert rig.client.opened == []
    assert rig.journal.records == []
    assert rig.journal.lock_keys == []


# ---------------------------------------------------------------------------
# Куда ложится строка
# ---------------------------------------------------------------------------
def test_row_goes_right_after_last_name() -> None:
    """Свободная строка — первая после последней непустой B и пустая во всю
    ширину. Дыра между карточками — не место для новой, заметка шефа в W ниже
    таблицы — не повод уводить карточку вниз (разбор 04.08.2026)."""
    note = [""] * WIDTH + ["заметка на полях"]
    cells = [*_cards(EXISTING[0], [], EXISTING[1]), [], note]
    rig = _rig(cells=cells)

    result = rig.append()

    assert result == AppendResult(row=6, journal_id=1, already_written=False)
    assert rig.line(6)[_index("name")] == "Соус Барбекю"
    assert rig.line(4) == [""] * WIDTH, "дыра между карточками осталась дырой"
    assert rig.sheet.get_all_values()[6] == note


@pytest.mark.parametrize("column", [16, 22], ids=["Q", "W"])
def test_row_with_only_q_or_w_is_skipped(column: int) -> None:
    """Строка, где заполнена только Q (декларация) или только W (за краем
    карточки), — чужая, хоть B в ней и пуста: писатель её пропускает и не
    трогает."""
    only = [""] * column + ["чужое"]
    rig = _rig((*EXISTING, only))

    assert rig.append().row == 7
    assert rig.sheet.get_all_values()[5] == only


@pytest.mark.parametrize(
    ("cells", "expected"),
    [
        ([], 3),
        ([header(SPEC)], 3),
        (_cards(), 3),
        (_cards(*EXISTING), 6),
        (_cards(EXISTING[0], [], EXISTING[1]), 6),
        (_cards(*EXISTING, [""] * 16 + ["Q"], [], [""] * 22 + ["W"]), 7),
        (_cards(*EXISTING, ["", "   ", "пробелы в имени — не имя, но строка не пуста"]), 7),
    ],
    ids=["empty", "one-header-row", "header-only", "three", "gap", "q-then-gap", "blank-name"],
)
def test_find_free_row(cells: list[list[str]], expected: int) -> None:
    assert find_free_row(cells) == expected


# ---------------------------------------------------------------------------
# Тело записи
# ---------------------------------------------------------------------------
def test_body_is_two_raw_ranges_without_q_and_r() -> None:
    """Одна запись: values.batchUpdate, RAW, два диапазона — A–P и S–V. Q и R
    — колонки людей: их нет в теле даже пустыми, и декларацию, вписанную
    шефом в нашу строку до записи, запись не трогает."""
    rig = _rig()
    rig.book.chef_edits_cell("'Лист1'!Q6", "декларация", moment="before_write")

    rig.append()

    [body] = rig.sent("values_batch_update")
    assert body["valueInputOption"] == "RAW"
    data = body["data"]
    assert isinstance(data, list)
    assert [item["range"] for item in data] == ["'Лист1'!A6:P6", "'Лист1'!S6:V6"]
    left, right = (item["values"] for item in data)
    assert [len(line) for line in left] == [16]
    assert right == [[None, None, None, APPROVED]], "пустое — null: ячейку не трогать"
    assert rig.writes() == ["values_batch_update"]
    assert rig.line(6)[_index("declaration")] == "декларация"
    assert body == row_payload(_values(), 6)


def test_numbers_go_as_json_numbers() -> None:
    """Белки, жиры, углеводы, ккал уходят числами JSON, а не текстом «12,5»:
    текст ломает шефу сортировку и формулы. Decimal("0.1") → 0.1, целое —
    целым, неизвестное — null (ячейка не трогается), а не нулём."""
    rig = _rig()

    rig.append()

    [body] = rig.sent("values_batch_update")
    data = body["data"]
    assert isinstance(data, list)
    numbers = data[0]["values"][0][_index("protein") : _index("kcal") + 1]
    assert numbers == [0.1, 12.5, 12, None]
    assert [type(number) for number in numbers] == [float, float, int, type(None)]
    assert rig.line(6)[_index("protein") : _index("kcal") + 1] == ["0,1", "12,5", "12", ""]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("12.5"), 12.5),
        (Decimal("12.000"), 12),
        (Decimal("12"), 12),
        (Decimal("0.1"), 0.1),
        (Decimal("0"), 0),
        (Decimal("1E+2"), 100),
    ],
)
def test_sheet_number_is_exact(value: Decimal, expected: float) -> None:
    number = sheet_number(value)
    assert number == expected
    assert type(number) is type(expected)


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0.12345678901234567890"),
        Decimal("9007199254740993"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
    ids=["too-precise", "past-2**53", "nan", "infinity"],
)
def test_sheet_number_refuses_what_float_would_change(value: Decimal) -> None:
    """Число, которое по дороге через float стало бы другим, — отказ, а не
    тихо округлённое значение в листе."""
    with pytest.raises(WriteRefusedError):
        sheet_number(value)


def test_inexact_number_is_refused_before_any_request() -> None:
    rig = _rig()
    values = _values()
    values["fat"] = Decimal("0.12345678901234567890")

    with pytest.raises(WriteRefusedError, match="Жиры"):
        rig.append(values)

    _nothing_asked(rig)


# ---------------------------------------------------------------------------
# Отказы до записи
# ---------------------------------------------------------------------------
def test_bots_alive_refuses_before_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Аварийный рычаг: ожил бот — отказ раньше, чем что-то уйдёт в Google."""
    monkeypatch.setattr(ownership, "BOTS_ALIVE", True)
    rig = _rig()

    with pytest.raises(ForbiddenWriteError):
        rig.append()

    _nothing_asked(rig)


@pytest.mark.parametrize("field", ["declaration", "halal_certificate"])
def test_q_or_r_in_values_is_refused(field: str) -> None:
    rig = _rig()
    values: dict[str, str | Decimal] = {**_values(), field: "x"}

    with pytest.raises(ForbiddenWriteError, match="принадлежит людям"):
        rig.append(values)

    _nothing_asked(rig)


def test_values_are_exactly_twenty_fields() -> None:
    """Недостающее поле — дыра в строке, которую никто не заметит."""
    rig = _rig()
    values = _values()
    del values["description"]

    with pytest.raises(ValueError, match="description"):
        rig.append(values)

    _nothing_asked(rig)


@pytest.mark.parametrize(
    ("field", "value"),
    [("fat", "12,5"), ("name", Decimal("1"))],
    ids=["text-in-number", "number-in-text"],
)
def test_value_kind_must_match_column(field: str, value: str | Decimal) -> None:
    rig = _rig()
    values = {**_values(), field: value}

    with pytest.raises(ValueError, match=rf"колонка {SPEC.column(field).letter}\b"):
        rig.append(values)

    _nothing_asked(rig)


@pytest.mark.parametrize(
    ("field", "phrase"), [("label_url", "этикетк"), ("name", "названи")], ids=["P", "B"]
)
def test_card_without_label_link_or_name_is_refused(field: str, phrase: str) -> None:
    """Свою строку писатель опознаёт по ссылке этикетки в P, дубль — по имени в
    B: без них записанное не проверить."""
    rig = _rig()
    values = {**_values(), field: "  "}

    with pytest.raises(WriteRefusedError, match=phrase):
        rig.append(values)

    _nothing_asked(rig)


def test_header_drift_writes_nothing() -> None:
    """Колонки съехали — отказ той же проверкой шапки, что у импорта; ни
    одной записанной ячейки, журнал пуст."""
    cells = _cards(*EXISTING)
    cells[0] = list(cells[0])
    cells[0][2] = "Совсем другая колонка"
    rig = _rig(cells=cells)

    with pytest.raises(HeaderDriftError) as caught:
        rig.append()

    assert rig.writes() == []
    assert rig.journal.records == []
    assert any("колонка C" in issue for issue in caught.value.issues)
    assert str(caught.value) == (
        "В таблице карточек сдвинулись колонки — запись остановлена, в таблице ничего "
        "не изменилось. Черновик сохранён; сообщите шефу."
    )


@pytest.mark.parametrize("existing", ["Соус  Барбекю", "соус барбекю", "Соус барбекю!"])
def test_exact_duplicate_name_is_refused(existing: str) -> None:
    """Точный дубль после нормализации имени — отказ с номером строки."""
    rig = _rig((*EXISTING, row(SPEC, name=existing, label_url=drive_view_url("other1"))))

    with pytest.raises(DuplicateNameError) as caught:
        rig.append(_values("Соус Барбекю"))

    assert caught.value.row == 6
    assert str(caught.value) == "«Соус Барбекю» уже есть в таблице — строка 6."
    assert rig.writes() == []
    assert rig.journal.records == []


def test_similar_name_is_not_an_error() -> None:
    """Похожее — подсказка на экране повара (задача 7), а не отказ писателя."""
    rig = _rig((*EXISTING, row(SPEC, name="Соус Барбекю острый")))

    assert rig.append(_values("Соус Барбекю")).row == 7


def test_name_conflicts_are_exact_after_normalising() -> None:
    cells = _cards(*EXISTING, row(SPEC, name="кетчуп "), row(SPEC, name="Кетчуп острый"))

    assert name_conflicts(cells, "КЕТЧУП") == (3, 6)
    assert name_conflicts(cells, "  ") == ()


@pytest.mark.parametrize(
    ("method", "error", "reason"),
    [
        ("worksheet", None, "Google-таблица не ответила"),
        ("values_batch_get", None, "Google-таблица не ответила"),
        (
            "worksheet",
            api_error(403, "The caller does not have permission", "PERMISSION_DENIED"),
            "Доступ платформы к таблице закрыт",
        ),
        (
            "values_batch_get",
            api_error(400, "Unable to parse range: 'Лист1'"),
            "Лист «Лист1» не найден — возможно, его переименовали",
        ),
    ],
    ids=["open-503", "read-503", "open-403", "read-400"],
)
def test_google_failure_before_write_changes_nothing(
    method: str, error: Exception | None, reason: str
) -> None:
    """Google отказал до записи — в таблице ничего не изменилось, журнал
    пуст, повару понятная причина, а не имя исключения: «не ответил» и
    «доступ закрыт» чинятся по-разному."""
    rig = _rig()
    rig.book.fail_next(method, applied=False, error=error)

    with pytest.raises(SheetUnavailableError) as caught:
        rig.append()

    assert rig.writes() == []
    assert rig.journal.records == []
    assert str(caught.value) == (
        f"{reason} — в таблице ничего не изменилось. Черновик сохранён, попробуйте ещё раз."
    )


@pytest.mark.parametrize("applied", [False, True], ids=["refused", "timeout"])
def test_grid_growth_failure_does_not_claim_nothing_changed(applied: bool) -> None:
    """Дописать строки в конец сетки Google мог и успеть, не ответив: говорить
    «ничего не изменилось» нельзя. Внизу могли появиться пустые строки —
    вреда в них нет, но и молчать о них незачем. Карточка не записана."""
    cells = _cards(*EXISTING)
    rig = _rig(cells=cells, rows=len(cells))
    rig.book.fail_next("batch_update", applied=applied)

    with pytest.raises(SheetUnavailableError) as caught:
        rig.append()

    assert rig.writes() == ["batch_update"]
    assert rig.journal.records == []
    assert str(caught.value) == (
        "Google-таблица не ответила, когда в конец таблицы добавлялись пустые строки: они "
        "могли добавиться, а карточка не записана. Черновик сохранён, попробуйте ещё раз."
    )


def test_full_grid_grows_before_the_write() -> None:
    """Свободная строка за краем сетки: сначала appendDimension, потом запись,
    иначе Google отказал бы «exceeds grid limits»."""
    cells = _cards(*EXISTING)
    rig = _rig(cells=cells, rows=len(cells))

    assert rig.append().row == 6

    assert rig.writes() == ["batch_update", "values_batch_update"]
    assert rig.sent("batch_update") == [
        {
            "requests": [
                {"appendDimension": {"sheetId": rig.sheet.id, "dimension": "ROWS", "length": 1}}
            ]
        }
    ]
    assert rig.journal.only().status == VERIFIED


def test_busy_writers_lock_is_sheet_busy() -> None:
    """Другой писатель держит очередь дольше предела — «таблица занята», и ни
    одного запроса к Google."""
    rig = _rig(lock_timeout=timedelta(milliseconds=50))
    rig.journal.lock.acquire()
    try:
        with pytest.raises(SheetBusyError, match=r"^Таблица занята, попробуйте ещё раз\.$"):
            rig.append()
    finally:
        rig.journal.lock.release()

    assert rig.book.requests == 0
    assert rig.journal.lock_keys == [SHEET_WRITE_LOCK_KEY]


def test_lost_lock_refuses_before_the_write(caplog: pytest.LogCaptureFixture) -> None:
    """База оборвала сессию, державшую очередь писателей, пока мы читали лист:
    второй писатель уже мог выбрать ту же строку. Прямо перед записью
    писатель проверяет, что очередь всё ещё его, — и отказывает до записи."""
    rig = _rig()
    rig.journal.lock_alive = False

    with caplog.at_level(logging.ERROR, logger=LOG), pytest.raises(LockLostError) as caught:
        rig.append()

    assert isinstance(caught.value, SheetBusyError), "для отправки — та же «попробуйте ещё раз»"
    assert str(caught.value) == (
        "Связь с базой платформы прервалась до записи — в таблице ничего не изменилось. "
        "Черновик сохранён, попробуйте ещё раз."
    )
    assert rig.writes() == []
    record = rig.journal.only()
    assert record.status == FAILED
    assert record.error is not None and "очередь писателей" in record.error
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_lock_is_checked_right_before_the_write() -> None:
    rig = _rig()

    rig.append()

    assert rig.journal.alive_checks == 1
    assert rig.journal.holds == [HOLD_LIMIT]


def test_hold_limit_outlasts_the_worst_write() -> None:
    """Сколько очередь может держаться одной записью: худший случай — около
    одиннадцати запросов к Google, каждый до (подключение + ответ) секунд.
    Предел — двенадцать таких и минута сверху; дольше — процесс завис."""
    settings = Settings(google_connect_timeout=10, google_read_timeout=60)

    assert hold_limit(settings) == timedelta(minutes=15)
    assert hold_limit(Settings()) == HOLD_LIMIT
    assert hold_limit(Settings(google_connect_timeout=5, google_read_timeout=25)) == timedelta(
        minutes=7
    )


# ---------------------------------------------------------------------------
# Удачная запись и повтор
# ---------------------------------------------------------------------------
def test_verified_write_leaves_hash_the_import_will_see() -> None:
    """Хеш в журнале — от перечитанной строки целиком, как её хеширует импорт:
    по нему перенос в базу узнает свою строку."""
    rig = _rig()

    result = rig.append()

    record = rig.journal.only()
    assert (record.status, record.row, record.request_key) == (VERIFIED, 6, KEY)
    assert record.values["name"] == "Соус Барбекю"
    assert record.values["fat"] == 12.5
    assert record.before == {"rows": {"5": _pad(EXISTING[2]), "6": [""] * WIDTH}}, (
        "обычный путь — снимок двух строк; полный снимок листа только при сбое раскладки"
    )
    reader = SheetsReader(rig.client, {"ingredient_cards": "cards-id"})
    [imported] = [line for line in reader.read(SPEC).rows if line.number == result.row]
    assert record.content_hash == imported.content_hash
    assert rig.journal.lock_keys == [SHEET_WRITE_LOCK_KEY]


def test_repeated_request_key_makes_no_requests() -> None:
    """Повтор отправки с тем же ключом — ответ из журнала, без единого
    запроса к Google и без второй строки."""
    rig = _rig()
    first = rig.append()
    asked, opened = rig.book.requests, list(rig.client.opened)

    again = rig.append()

    assert again == AppendResult(row=first.row, journal_id=first.journal_id, already_written=True)
    assert (rig.book.requests, rig.client.opened) == (asked, opened)
    assert len(rig.journal.records) == 1


def test_repeat_after_success_names_edits_that_were_not_written() -> None:
    """Карточка уже записана, а повтор с тем же ключом пришёл с правкой
    жиров: ответ — та же строка, без запросов к Google, и правка названа —
    в таблицу она не попала."""
    rig = _rig()
    first = rig.append()
    asked = rig.book.requests
    edited = _values()
    edited["fat"] = Decimal("13.5")

    again = rig.append(edited)

    assert again == AppendResult(
        row=first.row, journal_id=first.journal_id, already_written=True, not_written=("fat",)
    )
    assert rig.book.requests == asked


# ---------------------------------------------------------------------------
# Исход записи решает перечитывание
# ---------------------------------------------------------------------------
def test_applied_then_timeout_is_success() -> None:
    """Google записал, а ответ потерялся: исключение есть, строка лежит.
    Перечитывание это видит — запись состоялась, повтора записи нет."""
    rig = _rig()
    rig.book.fail_next("values_batch_update", applied=True)

    result = rig.append()

    assert result == AppendResult(row=6, journal_id=1, already_written=False)
    record = rig.journal.only()
    assert record.status == VERIFIED
    assert record.note is not None and "ReadTimeout" in record.note
    assert rig.writes() == ["values_batch_update"]


def test_not_applied_is_failed_and_nothing_cleared() -> None:
    """Google отказал, ничего не записав. Строка 6 пуста, строка выше на
    месте: запись не легла — failed, чистить нечего."""
    rig = _rig()
    rig.book.fail_next("values_batch_update", applied=False)

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.append()

    assert (caught.value.row, caught.value.journal_id, caught.value.layout_confirmed) == (
        6,
        1,
        True,
    )
    assert str(caught.value) == (
        "Google не принял запись — в таблице ничего не изменилось. Черновик сохранён, "
        "отправьте ещё раз."
    )
    record = rig.journal.only()
    assert record.status == FAILED
    assert record.error is not None and "503" in record.error
    assert set(record.before) == {"rows"}
    assert rig.writes() == ["values_batch_update"]
    assert rig.line(6) == [""] * WIDTH


def test_cell_changed_after_write_clears_only_ours() -> None:
    """Шеф вписал своё в B нашей строки сразу после записи. Раскладка на месте
    (строка выше та же, в P наша ссылка), но строка не наша целиком: очищаются
    только ячейки, где лежит наше. Его B и его Q остаются."""
    rig = _rig()
    rig.book.chef_edits_cell("'Лист1'!Q6", "декларация", moment="before_write")
    rig.book.tamper_after_write("'Лист1'!B6", "Соус шефа")

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.append()

    assert caught.value.layout_confirmed
    assert str(caught.value) == (
        "В строку 6 одновременно с нами вписали своё — наши ячейки убраны, чужие не "
        "тронуты. Черновик сохранён, отправьте ещё раз."
    )
    line = rig.line(6)
    assert line[_index("name")] == "Соус шефа"
    assert line[_index("declaration")] == "декларация"
    assert [cell for i, cell in enumerate(line) if i not in (1, 16)] == [""] * (WIDTH - 2)
    [clear] = rig.sent("values_batch_clear")
    ranges = clear["ranges"]
    assert isinstance(ranges, list)
    assert "'Лист1'!B6" not in ranges
    assert all(re.fullmatch(r"'Лист1'![A-PS-V]6", item) for item in ranges), ranges
    record = rig.journal.only()
    assert record.status == ROLLED_BACK
    assert record.error is not None and "B" in record.error


def test_empty_fields_leave_chef_cells_alone() -> None:
    """Описание у нашей карточки пустое и уходит null — ячейка не трогается.
    Шеф вписал описание в нашу строку за миг до записи: оно цело. Строка
    при этом не наша целиком — его D чужая, — поэтому наши ячейки убраны,
    а его описание осталось. Пустая строка вместо null стёрла бы его."""
    rig = _rig()
    rig.book.chef_edits_cell("'Лист1'!D6", "описание шефа", moment="before_write")

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.append()

    assert caught.value.layout_confirmed
    line = rig.line(6)
    assert line[_index("description")] == "описание шефа"
    assert [cell for i, cell in enumerate(line) if i != _index("description")] == [""] * (WIDTH - 1)
    record = rig.journal.only()
    assert record.status == ROLLED_BACK
    assert record.error is not None and "D" in record.error
    assert record.values["description"] is None


# ---------------------------------------------------------------------------
# Шеф сдвинул строки в окне записи
# ---------------------------------------------------------------------------
UNCONFIRMED = (
    "Таблицу меняли в ту же секунду, запись не подтверждена. Покажите шефу строку 6 "
    "(запись журнала №1). Черновик сохранён."
)


def test_row_inserted_above_before_write_is_caught(caplog: pytest.LogCaptureFixture) -> None:
    """Шеф вставил строку над «Майонезом» между нашим чтением и записью:
    «Майонез» съехал в строку 6, и запись легла поверх него. В строке 6 наше,
    но строка 5 уже не «Майонез» — раскладка не подтверждена. Ничего не
    чистим, журнал failed с полным снимком листа, в лог — ERROR."""
    rig = _rig()
    rig.book.chef_inserts_rows("Лист1", above=5, moment="before_write")

    with (
        caplog.at_level(logging.ERROR, logger=LOG),
        pytest.raises(WriteNotConfirmedError) as caught,
    ):
        rig.append()

    assert not caught.value.layout_confirmed
    assert str(caught.value) == UNCONFIRMED
    record = rig.journal.only()
    assert record.status == FAILED
    assert record.before["rows"]["5"] == _pad(EXISTING[2])
    sheet = record.before["sheet"]
    assert isinstance(sheet, list)
    assert sheet[4][_index("name")] == "Майонез", "затёртая карточка — в полном снимке листа"
    assert rig.writes() == ["values_batch_update"], "ничего не очищено"
    assert rig.line(6)[_index("declaration")] == "есть", "Q «Майонеза» прилипла к нашей строке"
    [message] = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert "в строку 6" in message
    assert "журнал №1" in message


def test_two_rows_inserted_keep_overwritten_card_in_full_snapshot() -> None:
    """Вставка двух строк над «Моцареллой»: она съехала в строку 6 и затёрта
    нашей записью. В снимок двух строк (5 и 6) она не попала — её строки
    там не было, — а в полном снимке листа есть."""
    rig = _rig()
    rig.book.chef_inserts_rows("Лист1", above=4, count=2, moment="before_write")

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.append()

    assert str(caught.value) == UNCONFIRMED
    record = rig.journal.only()
    assert record.status == FAILED
    rows = record.before["rows"]
    assert isinstance(rows, dict)
    assert "Моцарелла" not in {line[_index("name")] for line in rows.values()}
    sheet = record.before["sheet"]
    assert isinstance(sheet, list)
    assert sheet[3][_index("name")] == "Моцарелла"
    assert rig.line(6)[_index("name")] == "Соус Барбекю"


def test_row_inserted_above_after_write_spares_the_moved_card() -> None:
    """Вставка строки выше сразу после записи: наша строка уехала в 7, в 6
    оказался «Майонез». Сверка строки 6 увидела бы чужое, а откат «очистить
    совпавшие с нашими» стёр бы у «Майонеза» категорию, поставщика и «Да».
    Раскладка не подтверждена — не очищается ничего."""
    rig = _rig()
    rig.book.chef_inserts_rows("Лист1", above=5, moment="after_write")

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.append()

    assert not caught.value.layout_confirmed
    assert rig.line(6) == _pad(EXISTING[2]), "съехавшая карточка цела"
    assert rig.line(7)[_index("name")] == "Соус Барбекю"
    assert rig.writes() == ["values_batch_update"]
    assert rig.journal.only().status == FAILED


@pytest.mark.parametrize("moment", ["before_write", "after_write"])
def test_chef_filling_q_of_previous_card_keeps_confirmation(moment: str) -> None:
    """Шеф дописал декларацию свежей карточке в строке 5 ровно в окне записи.
    Строку выше подтверждают B и P, а не вся строка: его правка — не сдвиг."""
    rig = _rig()
    rig.book.chef_edits_cell("'Лист1'!Q5", "новая декларация", moment=moment)

    result = rig.append()

    assert result == AppendResult(row=6, journal_id=1, already_written=False)
    assert rig.journal.only().status == VERIFIED
    assert rig.book.chef_waiting() == []


def _two_row_header() -> list[list[str]]:
    """Шапка как в живом листе: над КБЖУ — общая подпись, под ней «белки»,
    «жиры»… во второй строке."""
    top = header(SPEC)
    nutrients = [_index(field) for field in ("protein", "fat", "carbs", "kcal")]
    second = [""] * WIDTH
    for index in nutrients:
        second[index], top[index] = top[index].lower(), ""
    top[nutrients[0]] = "Пищевая и энергетическая ценность"
    return [top, second]


def test_first_card_under_two_row_header_is_confirmed() -> None:
    """Первая карточка в пустом листе: строка выше — вторая строка шапки, и
    она сверяется целиком."""
    rig = _rig(cells=_two_row_header())

    result = rig.append()

    assert result.row == 3
    record = rig.journal.only()
    assert record.status == VERIFIED
    assert set(record.before["rows"]) == {"2", "3"}


def test_first_card_over_moved_subheaders_is_caught() -> None:
    """Шеф вставил строку над второй строкой шапки: подзаголовки «белки»,
    «жиры» съехали в строку 3, и запись легла поверх них. У второй строки
    шапки пусты B и P — сверка по ним сдвига не заметила бы; сверка целиком
    замечает."""
    rig = _rig(cells=_two_row_header())
    rig.book.chef_inserts_rows("Лист1", above=2, moment="before_write")

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.append()

    assert not caught.value.layout_confirmed
    assert rig.journal.only().status == FAILED


def test_shift_right_after_clearing_ours_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    """Очистка своих ячеек — своё окно: шеф вставил строку выше сразу после
    неё. Строка выше уже не та — чистили, возможно, не там: failed с полным
    снимком листа и ERROR в лог."""
    rig = _rig()
    rig.book.tamper_after_write("'Лист1'!B6", "Соус шефа")
    rig.book.chef_inserts_rows("Лист1", above=5, moment="after_clear")

    with (
        caplog.at_level(logging.ERROR, logger=LOG),
        pytest.raises(WriteNotConfirmedError) as caught,
    ):
        rig.append()

    assert not caught.value.layout_confirmed
    assert str(caught.value) == UNCONFIRMED
    record = rig.journal.only()
    assert record.status == FAILED
    assert "sheet" in record.before
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


def test_failed_clear_is_not_confirmed(caplog: pytest.LogCaptureFixture) -> None:
    """Очистка своих ячеек упала — неизвестно, что из нашего осталось в
    строке: failed с полным снимком листа, ERROR в лог, повару — строка и
    номер журнала."""
    rig = _rig()
    rig.book.tamper_after_write("'Лист1'!B6", "Соус шефа")
    rig.book.fail_next("values_batch_clear", applied=False)

    with (
        caplog.at_level(logging.ERROR, logger=LOG),
        pytest.raises(WriteNotConfirmedError) as caught,
    ):
        rig.append()

    assert str(caught.value) == (
        "Google-таблица не ответила, когда из строки 6 убирались наши ячейки, — что в ней "
        "осталось, неизвестно. Покажите шефу строку 6 (запись журнала №1). Черновик сохранён."
    )
    assert not caught.value.layout_confirmed
    record = rig.journal.only()
    assert record.status == FAILED
    assert record.error is not None and "очистка" in record.error
    assert "sheet" in record.before
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


# ---------------------------------------------------------------------------
# Повторная отправка после сбоя
# ---------------------------------------------------------------------------
def test_retry_after_unconfirmed_finds_our_row_but_keeps_the_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Раскладка не подтвердилась, а наша строка легла. Повтор находит её по
    своей ссылке этикетки в P: строка та же, ноль запросов записи. Но первая
    попытка, возможно, затёрла чужую карточку: повар, нажавший «Отправить»
    ещё раз, не должен увидеть голое «Записано» — в ответе прежняя попытка с
    номером строки и журнала, в лог — ERROR ещё раз."""
    rig = _rig()
    rig.book.chef_inserts_rows("Лист1", above=5, moment="before_write")
    with pytest.raises(WriteNotConfirmedError):
        rig.append()
    writes = rig.writes()
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger=LOG):
        result = rig.append()

    assert result == AppendResult(
        row=6, journal_id=2, already_written=True, shifted=UnconfirmedWrite(id=1, row=6)
    )
    assert rig.writes() == writes
    first, second = rig.journal.records
    assert (first.status, second.status) == (FAILED, VERIFIED)
    assert second.row == 6
    assert second.note is not None
    assert second.note.startswith("найдена прежняя попытка: строка уже в таблице")
    assert "журнал №1" in second.note
    [message] = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert "журнал №1" in message and "строк" in message


def test_prior_attempt_names_edits_that_were_not_written() -> None:
    """После сбоя черновик снова открыт: повар поправил жиры и отправил ещё
    раз. Строка прежней попытки уже лежит — писатель отвечает ею, но сверяет
    с отправляемым: правка жиров в таблицу не попала, и это названо."""
    rig = _rig()
    rig.book.chef_inserts_rows("Лист1", above=5, moment="before_write")
    with pytest.raises(WriteNotConfirmedError):
        rig.append()
    edited = _values()
    edited["fat"] = Decimal("13.5")

    result = rig.append(edited)

    assert (result.row, result.already_written, result.not_written) == (6, True, ("fat",))
    assert rig.line(6)[_index("fat")] == "12,5", "в листе — первая версия"
    found = rig.journal.records[-1]
    assert found.values["fat"] == 13.5, "в журнале — отправленное в этот раз"
    assert found.note is not None and "не записаны: I" in found.note
    assert found.after is not None
    assert found.after["rows"]["6"][_index("fat")] == "12,5"


def test_prior_attempt_is_found_by_label_link_even_after_rename() -> None:
    """Шеф поправил имя в строке прежней попытки. Свою строку писатель ищет по
    ссылке этикетки во всём листе, а не только среди тёзок: второй строки нет,
    а правка имени названа."""
    rig = _rig()
    rig.book.chef_inserts_rows("Лист1", above=5, moment="before_write")
    with pytest.raises(WriteNotConfirmedError):
        rig.append()
    rig.book.chef_edits_cell("'Лист1'!B6", "Соус Барбекю домашний", moment="now")
    writes = rig.writes()

    result = rig.append()

    assert (result.row, result.already_written, result.not_written) == (6, True, ("name",))
    assert rig.writes() == writes


def test_duplicate_with_foreign_label_link_is_refused() -> None:
    """Тот же ингредиент, но чужая ссылка этикетки — чужая карточка, не наша
    прежняя попытка."""
    other = row(SPEC, name="Соус Барбекю", label_url=drive_view_url("other1"))
    rig = _rig((*EXISTING, other))

    with pytest.raises(DuplicateNameError) as caught:
        rig.append()

    assert caught.value.row == 6
    assert rig.writes() == []


def _google_down_after_write(rig: Rig, monkeypatch: pytest.MonkeyPatch, *, applied: bool) -> None:
    """Запрос записи уходит и остаётся без ответа (``applied`` — легла ли
    строка), и сразу за ним Google перестаёт отвечать: перечитать тоже не
    выходит."""
    book = rig.book
    book.fail_next(
        "values_batch_update",
        applied=applied,
        error=requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)"),
    )
    original = book.values_batch_update

    def write(body: Mapping[str, object] | None = None) -> dict[str, object]:
        book.fail_next("values_batch_get", applied=False)
        return original(body)

    monkeypatch.setattr(book, "values_batch_update", write)


def test_unknown_outcome_stays_pending_and_retry_settles_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Исход неизвестен: записали без ответа, перечитать не удалось. Журнал
    остаётся pending — повтор с тем же ключом сначала перечитывает строку и
    находит её: вторую строку он не пишет."""
    rig = _rig()
    _google_down_after_write(rig, monkeypatch, applied=True)

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.append()

    assert str(caught.value) == (
        "Google-таблица не ответила: не удалось перечитать строку 6, и пока неизвестно, "
        "легла ли она. Черновик сохранён — отправьте ещё раз: повтор сначала проверит эту "
        "строку, второй не будет."
    )
    record = rig.journal.only()
    assert record.status == PENDING
    assert record.error is not None and "ReadTimeout" in record.error

    result = rig.append()

    assert result == AppendResult(row=6, journal_id=1, already_written=True)
    assert rig.journal.only().status == VERIFIED
    assert rig.writes() == ["values_batch_update"]


def test_resumed_attempt_never_clears_a_landed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Строка легла, но перечитать не удалось — запись висит pending. Прошли
    часы: шеф поправил состав (G), согласование (V) и вписал декларацию (Q).
    Повар отправляет ещё раз. Правило «чужая ячейка — убрать наши» — для окна
    в секунду в том же вызове, что и запись; к строке, которой шеф с тех пор
    пользуется, оно не применяется никогда: verified, строка та же, ни одной
    очистки, отличия — в журнале и в ответе."""
    rig = _rig()
    _google_down_after_write(rig, monkeypatch, applied=True)
    with pytest.raises(WriteNotConfirmedError):
        rig.append()
    monkeypatch.undo()
    rig.book.chef_edits_cell("'Лист1'!G6", "томаты, сахар, соль", moment="now")
    rig.book.chef_edits_cell("'Лист1'!V6", "Отбракован", moment="now")
    rig.book.chef_edits_cell("'Лист1'!Q6", "декларация", moment="now")

    result = rig.append()

    assert result == AppendResult(
        row=6,
        journal_id=1,
        already_written=True,
        not_written=("composition", "approval_status"),
    )
    assert "values_batch_clear" not in rig.writes()
    line = rig.line(6)
    assert (line[_index("composition")], line[_index("approval_status")]) == (
        "томаты, сахар, соль",
        "Отбракован",
    )
    assert line[_index("name")] == "Соус Барбекю"
    record = rig.journal.only()
    assert record.status == VERIFIED
    assert record.note is not None and "G, V" in record.note
    reader = SheetsReader(rig.client, {"ingredient_cards": "cards-id"})
    [imported] = [item for item in reader.read(SPEC).rows if item.number == 6]
    assert record.content_hash == imported.content_hash, "хеш — строки, какая она сейчас"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (None, "Google-таблица не ответила"),
        (
            api_error(403, "The caller does not have permission", "PERMISSION_DENIED"),
            "Доступ платформы к таблице закрыт",
        ),
        (
            api_error(400, "Unable to parse range: 'Лист1'!A5:V6"),
            "Лист «Лист1» не найден — возможно, его переименовали",
        ),
        (api_error(404, "Requested entity was not found.", "NOT_FOUND"), "Таблица не найдена"),
    ],
    ids=["503", "403", "400", "404"],
)
def test_failed_reread_names_the_reason(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None, reason: str
) -> None:
    """Перечитать после записи не удалось. Исход неизвестен — pending, но
    причина названа: «доступ закрыт» и «лист переименован» чинятся не
    повтором, и «Google не ответил» увёл бы искать не там."""
    rig = _rig()
    original = rig.book.values_batch_update

    def write(body: Mapping[str, object] | None = None) -> dict[str, object]:
        reply = original(body)
        rig.book.fail_next("values_batch_get", applied=False, error=error)
        return reply

    monkeypatch.setattr(rig.book, "values_batch_update", write)

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.append()

    assert str(caught.value).startswith(f"{reason}: не удалось перечитать строку 6")
    assert rig.journal.only().status == PENDING


def test_retry_writes_when_interrupted_attempt_did_not_land(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Прежняя попытка не записала строку: повтор отмечает её failed и пишет
    заново — в ту же свободную строку."""
    rig = _rig()
    _google_down_after_write(rig, monkeypatch, applied=False)
    with pytest.raises(WriteNotConfirmedError):
        rig.append()
    monkeypatch.undo()

    result = rig.append()

    assert result == AppendResult(row=6, journal_id=2, already_written=False)
    first, second = rig.journal.records
    assert first.status == FAILED
    assert first.error is not None and "прежняя попытка" in first.error
    assert second.status == VERIFIED
    assert rig.line(6)[_index("name")] == "Соус Барбекю"


def test_retry_refuses_when_interrupted_row_holds_someone_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Прежняя попытка прервалась, а в её строку тем временем вписали чужое:
    строка не наша и не пустая. Писать поверх нельзя, чистить нечего — отказ
    с номером строки и журнала, failed с полным снимком."""
    rig = _rig()
    rig.book.chef_edits_cell("'Лист1'!B6", "Соус шефа", moment="before_write")
    _google_down_after_write(rig, monkeypatch, applied=False)
    with pytest.raises(WriteNotConfirmedError):
        rig.append()
    monkeypatch.undo()

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.append()

    assert str(caught.value) == (
        "Таблицу меняли, пока прежняя попытка записи оставалась незавершённой, — запись не "
        "подтверждена. Покажите шефу строку 6 (запись журнала №1). Черновик сохранён."
    )
    record = rig.journal.only()
    assert record.status == FAILED
    assert "sheet" in record.before
    assert rig.writes() == ["values_batch_update"], "вторая попытка ничего не писала"
    assert rig.line(6)[_index("name")] == "Соус шефа"


# ---------------------------------------------------------------------------
# Правило 9 sheets-guard: единственный писатель
# ---------------------------------------------------------------------------
SRC = Path(__file__).resolve().parents[2] / "src"
_WRITE_METHODS = re.compile(
    r"\b(?:update_cells?|append_rows?|batch_clear|insert_rows?|delete_rows|values_update"
    r"|values_append|values_clear|values_batch_update|values_batch_clear|batch_update)\b"
)
_PROTOCOL = {"values_batch_update", "values_batch_clear", "batch_update"}
"""Методы записи, которые объявляет протокол таблицы в ``sync/client.py``."""


def test_only_the_writer_writes_to_sheets() -> None:
    """Запись в таблицу — один путь: ``CardSheetWriter`` под блокировкой
    писателей. Метод записи где-то ещё — второй писатель без сверки и
    журнала, то, от чего правило 9 и уводит.

    Ловится и упоминание без вызова: ссылка на метод (``write =
    book.values_update``) — та же запись. Список — однозначные методы записи
    gspread 6.2. ``.update(`` и ``.clear(`` не ловятся: так же зовутся методы
    словаря и множества, и храповик краснел бы на каждом ``dict.update``.
    """
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        names = set(_WRITE_METHODS.findall(path.read_text(encoding="utf-8")))
        if names:
            found[path.relative_to(SRC).as_posix()] = names

    assert set(found) <= {"kitchen/sync/writer.py", "kitchen/sync/client.py"}, found
    assert found["kitchen/sync/client.py"] == _PROTOCOL, "протокол объявляет только эти"
    assert found["kitchen/sync/writer.py"] >= _PROTOCOL
