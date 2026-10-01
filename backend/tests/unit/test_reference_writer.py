"""Писатель строки справочника — без сети и без базы.

Вторая запись платформы в Google-таблицу и первая — в книгу кухни: ручные
ячейки строки ING, которую формула QUERY уже вывела для карточки «Да». По
этой строке калькулятор считает себестоимость, и ошибка здесь тихая: id или
цена в чужой строке дают правдоподобные чужие числа. Поэтому тесты в первую
очередь проверяют, чего писатель не пишет никогда, и каждый отказ — до
первой записи.

Лист ING по умолчанию (``tests.conftest.ing_sheet``): шапка; строки 2–3
вписаны руками (id 1 и 99, во второй — «Сахар»); с 4-й — зона QUERY:
«Кетчуп» (id 129), «Моцарелла» (id 130, L — формула), «Соус Барбекю»
(заготовка), «Соус Сырный» (заготовка, L — формула), «Сахар» (заготовка);
ниже — две заготовки без карточек. Следующий id — 131.

Храповик — фикстура :func:`_ratchet`: после каждого теста этого файла ни
один запрос записи не тронул B–D, F–K и P листа ING и ничего — в книге
карточек.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from kitchen.config import Settings
from kitchen.db.journal import (
    FAILED,
    LAYOUT_UNCONFIRMED,
    PENDING,
    ROLLED_BACK,
    VERIFIED,
    UnconfirmedWrite,
)
from kitchen.domain.cards import APPROVED
from kitchen.domain.reference_row import ANCHOR_MISSING, ReferenceForm, ReferenceFormError
from kitchen.sync import ownership, specs
from kitchen.sync.ownership import ForbiddenWriteError
from kitchen.sync.reader import SheetsReader
from kitchen.sync.reference_writer import (
    ACTION,
    HOLD_LIMIT,
    FillResult,
    ReferenceRowFiller,
    RowRefusedError,
    SheetLayoutError,
    fill_request_key,
    hold_limit,
)
from kitchen.sync.sheet_write import (
    SHEET_WRITE_LOCK_KEY,
    SheetBusyError,
    SheetUnavailableError,
    WriteNotConfirmedError,
)
from tests.conftest import (
    CARDS_ORDER,
    QUERY_B,
    FakeSheetsClient,
    FakeSpreadsheet,
    FakeWorksheet,
    Formula,
    cards_book_sheet,
    ing_sheet,
    reference_client,
)
from tests.fake_journal import FakeJournal
from tests.fake_sheets import IDS

if TYPE_CHECKING:
    from collections.abc import Iterator

SPEC = specs.INGREDIENTS
WIDTH = len(SPEC.columns)
KEY = "ing-fill:7"
LOG = "kitchen.sync"
WRITES = ("values_batch_update", "values_batch_clear", "batch_update")
NEVER = set("BCDFGHIJKP")
"""Колонки ING, которых запрос записи не касается никогда: вывод QUERY и
формула P."""

NOT_YET = (
    "Строка ещё не появилась в справочнике — таблица подтягивает карточки с задержкой, "
    "попробуйте через несколько минут"
)
SHIFTED = (
    "Строки справочника сдвинуты относительно карточек — запись не сделана, проверьте лист ING"
)
NOT_APPROVED = "Карточка не согласована — в справочник попадают только «Да»"
HEADER_DRIFT = "Колонки справочника изменились — сообщите разработчику"
ANCHOR_BROKEN = (
    "Формула, которая подтягивает карточки в справочник, сломана — сообщите разработчику"
)
BUSY = "Таблица занята — попробуйте ещё раз"
NOT_CONFIRMED = "Не удалось подтвердить запись — нажмите ещё раз: второй записи не будет"


def _index(letter: str) -> int:
    return ord(letter) - ord("A")


def _form(**changes: str | None) -> ReferenceForm:
    """Форма, как её заполнит шеф: потери — в процентах."""
    raw: dict[str, str | None] = {
        "short_name": "Барбекю",
        "unit": "кг",
        "price_per_kg": "250",
        "price_per_pack": "1250,5",
        "weight_per_piece_g": "",
        "losses_unpacking": "12,5",
        "losses_cutting": "5",
        "losses_thermal": "",
    }
    raw.update(changes)
    return ReferenceForm.parse(raw)


def _cards(
    *extra: tuple[str, str], order: tuple[tuple[str, str], ...] = CARDS_ORDER
) -> FakeWorksheet:
    return cards_book_sheet((*order, *extra))


# ---------------------------------------------------------------------------
# Стенд
# ---------------------------------------------------------------------------
_BOOKS: list[FakeSpreadsheet] = []
"""Таблицы всех стендов теста — их проверяет храповик."""


@dataclass
class Rig:
    """Писатель со своими фальшивыми книгами и журналом."""

    filler: ReferenceRowFiller
    client: FakeSheetsClient
    journal: FakeJournal

    @property
    def kitchen(self) -> FakeSpreadsheet:
        return self.client._spreadsheets[IDS["kitchen"]]

    @property
    def cards(self) -> FakeSpreadsheet:
        return self.client._spreadsheets[IDS["ingredient_cards"]]

    @property
    def ing(self) -> FakeWorksheet:
        return self.kitchen._sheets["ING"]

    def fill(
        self, name: str = "Соус Барбекю", form: ReferenceForm | None = None, *, key: str = KEY
    ) -> FillResult:
        return self.filler.fill(
            name, form if form is not None else _form(), actor_id=None, request_key=key
        )

    def line(self, number: int) -> list[str]:
        """Строка ING, как её видит шеф, A–T. Мимо таблицы — запросов не добавляет."""
        cells = self.ing.get_all_values()
        line = cells[number - 1][:WIDTH] if number <= len(cells) else []
        return line + [""] * (WIDTH - len(line))

    def writes(self) -> list[str]:
        return [name for name, _ in self.kitchen.calls if name in WRITES]

    def sent(self, method: str) -> list[dict[str, object]]:
        return [payload for name, payload in self.kitchen.calls if name == method]

    def requests(self) -> int:
        return self.kitchen.requests + self.cards.requests


def _rig(
    *,
    ing: FakeWorksheet | None = None,
    cards: FakeWorksheet | None = None,
    lock_timeout: timedelta = timedelta(seconds=30),
) -> Rig:
    client = reference_client(ing=ing, cards=cards)
    journal = FakeJournal()
    filler = ReferenceRowFiller(
        client,
        journal,
        kitchen_id=IDS["kitchen"],
        cards_id=IDS["ingredient_cards"],
        lock_timeout=lock_timeout,
    )
    rig = Rig(filler, client, journal)
    _BOOKS.extend((rig.kitchen, rig.cards))
    return rig


def _cells(ranges: list[str]) -> set[str]:
    """Адреса «L6» всех ячеек диапазонов «'ING'!L6:O6»."""
    found: set[str] = set()
    for text in ranges:
        assert text.startswith("'ING'!"), f"запись не в лист ING: {text}"
        start, _, end = text.removeprefix("'ING'!").partition(":")
        end = end or start
        first, last = ord(start[0]), ord(end[0])
        top, bottom = int(start[1:]), int(end[1:])
        found |= {f"{chr(c)}{r}" for c in range(first, last + 1) for r in range(top, bottom + 1)}
    return found


def _touched(book: FakeSpreadsheet) -> set[str]:
    """Ячейки, которых касались запросы записи и очистки этой таблицы."""
    ranges: list[str] = []
    for name, payload in book.calls:
        assert name != "batch_update", "писатель справочника не меняет устройство таблицы"
        if name == "values_batch_update":
            assert isinstance(payload, dict)
            ranges += [item["range"] for item in payload["data"]]
        elif name == "values_batch_clear":
            assert isinstance(payload, dict)
            ranges += payload["ranges"]
    return _cells(ranges)


@pytest.fixture(autouse=True)
def _ratchet() -> Iterator[None]:
    """Храповик по телу запроса: что бы ни случилось в тесте, запись не
    коснулась B–D, F–K (вывод QUERY) и P (формула шефа), а в книге карточек
    не было ни одного запроса записи."""
    _BOOKS.clear()
    yield
    for book in _BOOKS:
        touched = _touched(book)
        if "ING" not in book._sheets:
            assert touched == set(), "в книгу карточек писатель справочника не пишет"
        assert {cell[0] for cell in touched} & NEVER == set(), sorted(touched)


def _nothing_written(rig: Rig) -> None:
    """Отказ без единой записи и без следа в журнале."""
    assert rig.writes() == []
    assert rig.journal.records == []


def _nothing_asked(rig: Rig) -> None:
    """Отказ до первого запроса к Google и до очереди писателей."""
    assert rig.requests() == 0
    assert rig.client.opened == []
    assert rig.journal.records == []
    assert rig.journal.lock_keys == []


# ---------------------------------------------------------------------------
# Тело записи
# ---------------------------------------------------------------------------
def test_body_holds_only_manual_cells_of_the_row() -> None:
    """Одна запись: values.batchUpdate, RAW, только ручные ячейки этой строки
    — A, E, L–O, Q–T. B–D и F–K — вывод QUERY, P — формула шефа: их нет в
    теле даже пустыми."""
    rig = _rig()

    result = rig.fill()

    assert result == FillResult(row=6, ref_id="131", journal_id=1, already=False)
    [body] = rig.sent("values_batch_update")
    assert body["valueInputOption"] == "RAW"
    data = body["data"]
    assert isinstance(data, list)
    assert [item["range"] for item in data] == [
        "'ING'!A6:A6",
        "'ING'!E6:E6",
        "'ING'!L6:O6",
        "'ING'!Q6:T6",
    ]
    assert _cells([item["range"] for item in data]) == {f"{letter}6" for letter in "AELMNOQRST"}
    assert rig.writes() == ["values_batch_update"]


def test_numbers_go_as_json_numbers_and_text_as_text() -> None:
    """id — целым JSON, цены, вес и потери — числами JSON, короткое имя,
    единица и статус — текстом. Строка «0.05» при RAW легла бы текстом, и
    формула P её не сложила бы."""
    rig = _rig()

    rig.fill()

    [body] = rig.sent("values_batch_update")
    data = body["data"]
    assert isinstance(data, list)
    values = [value for item in data for value in item["values"][0]]
    assert values == [131, "Барбекю", 250, 1250.5, "кг", None, 0.125, 0.05, 0, "активный"]
    assert [type(value) for value in values] == [
        int,
        str,
        int,
        float,
        str,
        type(None),
        float,
        float,
        int,
        str,
    ]


def test_losses_go_as_shares_into_percent_cells() -> None:
    """Потери вводятся в процентах, в лист уходят долей: «12,5» — это
    0,125, и шеф видит «12,50%». Формула P «Общие потери» на месте."""
    rig = _rig()

    rig.fill()

    line = rig.line(6)
    assert [line[_index(letter)] for letter in "AELMNOQRST"] == [
        "131",
        "Барбекю",
        "250",
        "1250,5",
        "кг",
        "",
        "12,50%",
        "5%",
        "0%",
        "активный",
    ]
    assert line[_index("C")] == "Соус Барбекю", "вывод QUERY на месте"
    assert rig.ing.cell("P6") == Formula("=SUM(Q6+R6+S6)", 0)


def test_empty_field_is_null_and_leaves_the_cell_alone() -> None:
    """Пустое поле формы — null: ячейку не трогать. Умолчание заготовки шефа
    в L остаётся, а не стирается и не становится нулём от нас."""
    rig = _rig()
    rig.ing.put("L6", 99)

    rig.fill(form=_form(price_per_kg=""))

    [body] = rig.sent("values_batch_update")
    data = body["data"]
    assert isinstance(data, list)
    assert data[2] == {"range": "'ING'!L6:O6", "values": [[None, 1250.5, "кг", None]]}
    assert rig.line(6)[_index("L")] == "99"
    assert rig.journal.only().status == VERIFIED


def test_piece_weight_goes_to_o() -> None:
    rig = _rig()

    rig.fill(form=_form(unit="шт", weight_per_piece_g="35,5"))

    assert rig.line(6)[_index("N") : _index("P")] == ["шт", "35,5"]


def test_formula_in_l_is_skipped() -> None:
    """L в этой строке — формула от M: цена за единицу считается в таблице.
    L нет в теле записи вовсе, формула цела, M записана."""
    rig = _rig()

    result = rig.fill("Соус Сырный")

    assert (result.row, result.ref_id) == (7, "131")
    [body] = rig.sent("values_batch_update")
    data = body["data"]
    assert isinstance(data, list)
    assert [item["range"] for item in data] == [
        "'ING'!A7:A7",
        "'ING'!E7:E7",
        "'ING'!M7:O7",
        "'ING'!Q7:T7",
    ]
    assert rig.ing.cell("L7") == Formula("=M7/5", 0.0)
    assert rig.line(7)[_index("M")] == "1250,5"
    assert "price_per_kg" not in rig.journal.only().values


# ---------------------------------------------------------------------------
# Какая строка и какой id
# ---------------------------------------------------------------------------
def test_id_is_the_numeric_max_plus_one_from_the_formula_reading() -> None:
    """id — наибольший числовой id листа плюс один. Берётся из FORMULA-чтения:
    id, спрятанный оформлением «;;;», в FORMATTED пуст, но в счёт идёт."""
    ing = ing_sheet()
    ing.put("A2", 1030)
    ing.set_format("A2", ";;;")
    rig = _rig(ing=ing)

    result = rig.fill()

    assert result.ref_id == "1031"
    assert rig.line(6)[0] == "1031"


def test_namesake_typed_by_hand_above_the_anchor_does_not_count() -> None:
    """«Сахар» есть и в строке 3, вписанной руками, и в зоне QUERY. Строка
    карточки — та, что вывела формула, на своём месте среди «Да»."""
    rig = _rig()

    result = rig.fill("Сахар", _form(short_name="Сахар"))

    assert (result.row, result.ref_id) == (8, "131")
    assert rig.line(3)[0] == "99"


def test_fills_one_after_another_get_ids_in_a_row() -> None:
    rig = _rig()

    first = rig.fill()
    second = rig.fill("Соус Сырный", key="ing-fill:8")

    assert (first.row, first.ref_id) == (6, "131")
    assert (second.row, second.ref_id) == (7, "132")


def test_request_key_is_stable_per_card() -> None:
    assert fill_request_key(7) == "ing-fill:7"


# ---------------------------------------------------------------------------
# Отказы поиска — ни одной записи
# ---------------------------------------------------------------------------
def test_row_not_there_yet_writes_nothing() -> None:
    """Карточка «Да», но IMPORTRANGE ещё не подтянул её в ING — строки нет.
    Честный отказ с текстом спеки, ни одной записи."""
    rig = _rig(cards=_cards(("Соус Чесночный", APPROVED)))

    with pytest.raises(RowRefusedError) as caught:
        rig.fill("Соус Чесночный")

    assert str(caught.value) == NOT_YET
    assert caught.value.reason == "not_yet"
    _nothing_written(rig)


def test_shifted_rows_write_nothing() -> None:
    """Шеф переключил «Майонез» на «Да» в середине книги карточек, а QUERY
    ещё не пересчитался: место «Соуса Барбекю» среди «Да» — строка 7, а по
    названию он в строке 6. Запись в любую из них отдала бы id чужому."""
    order = tuple((name, APPROVED if name == "Майонез" else ok) for name, ok in CARDS_ORDER)
    rig = _rig(cards=_cards(order=order))

    with pytest.raises(RowRefusedError) as caught:
        rig.fill()

    assert str(caught.value) == SHIFTED
    assert caught.value.reason == "shifted"
    _nothing_written(rig)


def test_card_not_approved_is_refused() -> None:
    rig = _rig()

    with pytest.raises(RowRefusedError) as caught:
        rig.fill("Майонез")

    assert str(caught.value) == NOT_APPROVED
    _nothing_written(rig)


def test_namesakes_among_approved_are_refused() -> None:
    rig = _rig(cards=_cards(("Соус Барбекю", APPROVED)))

    with pytest.raises(RowRefusedError) as caught:
        rig.fill()

    assert caught.value.reason == "ambiguous"
    assert "несколько" in str(caught.value)
    _nothing_written(rig)


def test_row_with_an_id_is_already_in_the_reference() -> None:
    """В строке карточки уже стоит id — ингредиент уже в справочнике. Ответ
    без записи и без следа в журнале."""
    rig = _rig()

    result = rig.fill("Кетчуп")

    assert result == FillResult(row=4, ref_id="129", journal_id=None, already=True)
    assert result.message == "Ингредиент уже в справочнике — id 129"
    _nothing_written(rig)


def test_id_hidden_by_format_is_not_an_empty_a() -> None:
    """A под оформлением «;;;» в FORMATTED выглядит пустым, но в FORMULA там
    id. Строка не ждёт переноса — это сдвиг, а не место для записи."""
    ing = ing_sheet()
    ing.put("A6", 131)
    ing.set_format("A6", ";;;")
    rig = _rig(ing=ing)

    with pytest.raises(RowRefusedError) as caught:
        rig.fill()

    assert str(caught.value) == SHIFTED
    _nothing_written(rig)


# ---------------------------------------------------------------------------
# Отказы по устройству строки и листа — ни одной записи
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("letter", ["E", "M", "N", "O", "S", "T"])
def test_formula_in_a_manual_cell_is_refused(letter: str) -> None:
    """Формула в ручной ячейке, кроме L, — не то, что ждали: платформа её не
    перезаписывает и не пропускает молча."""
    ing = ing_sheet()
    ing.put(f"{letter}6", Formula(f"={letter}5", 0))
    rig = _rig(ing=ing)

    with pytest.raises(RowRefusedError) as caught:
        rig.fill()

    assert caught.value.reason == "formula"
    assert f"ячейке {letter}" in str(caught.value)
    assert "строке 6" in str(caught.value)
    _nothing_written(rig)


@pytest.mark.parametrize("letter", ["Q", "R", "S"])
def test_loss_cell_not_in_percent_is_refused(letter: str) -> None:
    """Потери пишутся долей в расчёте на процентное оформление ячейки. Ячейка
    без него показала бы 0,05 вместо 5% — запись отказывает, а не угадывает."""
    ing = ing_sheet()
    ing.set_format(f"{letter}6", None)
    rig = _rig(ing=ing)

    with pytest.raises(RowRefusedError) as caught:
        rig.fill()

    assert str(caught.value) == (
        "Ячейки потерь в строке 6 оформлены не процентами — запись не сделана, проверьте лист ING"
    )
    assert caught.value.reason == "percent"
    _nothing_written(rig)


def test_header_drift_writes_nothing() -> None:
    rig = _rig()
    rig.kitchen.chef_edits_cell("'ING'!L1", "Цена", moment="now")

    with pytest.raises(SheetLayoutError) as caught:
        rig.fill()

    assert str(caught.value) == HEADER_DRIFT
    assert any("колонка L" in issue for issue in caught.value.issues)
    _nothing_written(rig)


def test_cards_header_drift_writes_nothing() -> None:
    """Порядок «Да» читается из книги карточек по колонкам B и V: съехали
    они — место строки посчиталось бы не то."""
    rig = _rig()
    rig.cards.chef_edits_cell("'Лист1'!V1", "Статус", moment="now")

    with pytest.raises(SheetLayoutError) as caught:
        rig.fill()

    assert str(caught.value) == "Колонки таблицы карточек изменились — сообщите разработчику"
    _nothing_written(rig)


@pytest.mark.parametrize(
    ("where", "cell", "text"),
    [
        ("B4", Formula(QUERY_B, "#REF!"), ANCHOR_BROKEN),
        ("B9", Formula(QUERY_B, ""), ANCHOR_BROKEN),
        ("B4", "Соусы", ANCHOR_MISSING),
    ],
    ids=["ref-error", "second-query", "no-query"],
)
def test_broken_anchor_is_refused(where: str, cell: object, text: str) -> None:
    """IMPORTRANGE потерял доступ, и QUERY показывает «#REF!»; в B вторая
    формула QUERY; формулы нет вовсе. Это поломка листа, а не «строка ещё не
    появилась» — иначе человек ждал бы вечно."""
    ing = ing_sheet()
    ing.put(where, cell)
    rig = _rig(ing=ing)

    with pytest.raises(SheetLayoutError) as caught:
        rig.fill()

    assert str(caught.value) == text
    _nothing_written(rig)


# ---------------------------------------------------------------------------
# Отказы до первого запроса
# ---------------------------------------------------------------------------
def test_bots_alive_refuses_before_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = _rig()
    monkeypatch.setattr(ownership, "BOTS_ALIVE", True)

    with pytest.raises(ForbiddenWriteError):
        rig.fill()

    _nothing_asked(rig)


def test_inexact_number_is_a_field_error_before_first_request() -> None:
    """Число, которое по дороге через float стало бы другим, — ошибка поля
    формы, а не тихо округлённая цена."""
    rig = _rig()

    with pytest.raises(ReferenceFormError) as caught:
        rig.fill(form=_form(price_per_pack="0,12345678901234567890"))

    assert set(caught.value.errors) == {"price_per_pack"}
    assert "Цена за упаковку" in caught.value.errors["price_per_pack"]
    _nothing_asked(rig)


def test_busy_writers_queue() -> None:
    rig = _rig(lock_timeout=timedelta(seconds=0.1))
    rig.journal.lock.acquire()
    try:
        with pytest.raises(SheetBusyError) as caught:
            rig.fill()
    finally:
        rig.journal.lock.release()

    assert str(caught.value) == BUSY
    assert rig.journal.lock_keys == [SHEET_WRITE_LOCK_KEY]
    assert rig.requests() == 0


def test_lost_lock_refuses_before_the_write(caplog: pytest.LogCaptureFixture) -> None:
    """База оборвала очередь писателей, пока писатель читал лист: второй мог
    уже выдать тот же id. Перед записью — проверка, и отказ без записи."""
    rig = _rig()
    rig.journal.lock_alive = False

    with caplog.at_level(logging.ERROR, logger=LOG), pytest.raises(SheetBusyError) as caught:
        rig.fill()

    assert "в справочнике ничего не изменилось" in str(caught.value)
    assert rig.writes() == []
    record = rig.journal.only()
    assert record.status == FAILED
    assert record.error is not None and "очередь писателей" in record.error
    assert "очередь писателей потеряна" in caplog.text


def test_google_failure_before_write_changes_nothing() -> None:
    rig = _rig()
    rig.kitchen.fail_next("values_batch_get", applied=False)

    with pytest.raises(SheetUnavailableError) as caught:
        rig.fill()

    assert str(caught.value) == (
        "Google-таблица не ответила — в справочнике ничего не изменилось, нажмите ещё раз"
    )
    _nothing_written(rig)


def test_hold_limit_outlasts_the_worst_fill() -> None:
    """Худшее заполнение — около тринадцати запросов к Google: открыть две
    книги, перечитать прерванную попытку, прочитать лист дважды и книгу
    карточек, записать, перечитать, вернуть свои ячейки, перечитать ещё раз.
    Предел — четырнадцать таких и минута сверху."""
    settings = Settings(google_connect_timeout=10, google_read_timeout=60)

    assert hold_limit(settings) == timedelta(minutes=17, seconds=20)
    assert hold_limit(Settings()) == HOLD_LIMIT


# ---------------------------------------------------------------------------
# Журнал и повтор
# ---------------------------------------------------------------------------
def test_journal_keeps_the_fill() -> None:
    """След в журнале: действие fill, книга кухни, лист ING, строка, что
    отправлено, снимок строки до записи (как видит шеф и формулами) и якорь.
    Полный снимок листа после подтверждения больше не нужен."""
    rig = _rig()
    before = rig.line(6)

    rig.fill()

    record = rig.journal.only()
    assert (record.action, record.book, record.sheet, record.row) == (ACTION, "kitchen", "ING", 6)
    assert ACTION == "fill"
    assert (record.status, record.request_key) == (VERIFIED, KEY)
    assert record.values["id"] == 131
    assert record.values["losses_unpacking"] == 0.125
    assert record.before["rows"] == {"6": before}
    assert record.before["anchor"] == 4
    assert record.before["name"] == "Соус Барбекю"
    assert "sheet" not in record.before
    assert record.content_hash is not None and len(record.content_hash) == 64


def test_verified_fill_leaves_the_hash_the_import_will_see() -> None:
    """Хеш в журнале — хеш строки A–T, как её посчитает импорт книги кухни."""
    rig = _rig()

    result = rig.fill()

    [imported] = [
        row for row in SheetsReader(rig.client, IDS).read(SPEC).rows if row.number == result.row
    ]
    assert rig.journal.only().content_hash == imported.content_hash
    assert imported["id"] == "131"


def test_repeated_request_key_makes_no_requests() -> None:
    """Повтор с тем же ключом — ответ по журналу, без единого запроса к
    Google: второй записи и второго id нет."""
    rig = _rig()
    first = rig.fill()
    asked, opened = rig.requests(), list(rig.client.opened)

    again = rig.fill()

    assert again == FillResult(row=6, ref_id="131", journal_id=first.journal_id, already=True)
    assert again.message == "Ингредиент уже в справочнике — id 131"
    assert (rig.requests(), rig.client.opened) == (asked, opened)
    assert rig.writes() == ["values_batch_update"]


def test_success_message_names_row_and_id() -> None:
    assert FillResult(row=6, ref_id="131", journal_id=1, already=False).message == (
        "Записано в справочник: строка 6, id 131"
    )


# ---------------------------------------------------------------------------
# Исход записи решает перечитывание
# ---------------------------------------------------------------------------
def test_cell_changed_after_write_returns_only_ours() -> None:
    """Шеф вписал своё в M той же строки в ту же секунду. Наши ячейки
    возвращаются к тому, что было до записи (A и E — пусты, умолчания
    заготовки — на месте), чужая M не тронута; журнал rolled_back."""
    rig = _rig()
    before = rig.line(6)
    rig.kitchen.tamper_after_write("'ING'!M6", "999")

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.fill()

    assert caught.value.layout_confirmed
    assert str(caught.value) == (
        "В строку 6 листа ING одновременно с нами вписали своё — наши ячейки вернули как было, "
        "чужие не тронуты. Нажмите ещё раз"
    )
    expected = list(before)
    expected[_index("M")] = "999"
    assert rig.line(6) == expected
    assert rig.writes() == ["values_batch_update", "values_batch_update"]
    restore = rig.sent("values_batch_update")[1]
    data = restore["data"]
    assert isinstance(data, list)
    assert {item["range"]: item["values"] for item in data} == {
        "'ING'!A6:A6": [[""]],
        "'ING'!E6:E6": [[""]],
        "'ING'!L6:L6": [[0]],
        "'ING'!Q6:Q6": [[0]],
        "'ING'!R6:R6": [[0]],
    }
    record = rig.journal.only()
    assert record.status == ROLLED_BACK
    assert record.error is not None and "M" in record.error
    assert record.after is not None and record.after["restored"] == ["A", "E", "L", "Q", "R"]


def test_applied_then_timeout_is_success() -> None:
    """Google записал, но ответ потерялся. Перечитывание видит наш id —
    запись состоялась."""
    rig = _rig()
    rig.kitchen.fail_next("values_batch_update", applied=True)

    result = rig.fill()

    assert result == FillResult(row=6, ref_id="131", journal_id=1, already=False)
    record = rig.journal.only()
    assert record.status == VERIFIED
    assert record.note is not None and "легла" in record.note


def test_not_applied_write_is_failed_and_changes_nothing() -> None:
    rig = _rig()
    before = rig.line(6)
    rig.kitchen.fail_next("values_batch_update", applied=False)

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.fill()

    assert caught.value.layout_confirmed
    assert str(caught.value) == (
        "Google не принял запись — в справочнике ничего не изменилось, нажмите ещё раз"
    )
    assert rig.line(6) == before
    assert rig.journal.only().status == FAILED
    assert rig.writes() == ["values_batch_update"]


def _reread_fails_after_write(rig: Rig, monkeypatch: pytest.MonkeyPatch, *, applied: bool) -> None:
    """Запись ушла (легла или нет), а перечитать строку не удалось."""
    book = rig.kitchen
    original = book.values_batch_update

    def write(body: dict[str, object]) -> dict[str, object]:
        book.fail_next("values_batch_update", applied=applied)
        book.fail_next("values_batch_get", applied=False)
        return original(body)

    monkeypatch.setattr(book, "values_batch_update", write)


def test_unknown_outcome_stays_pending_and_retry_settles_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Исход неизвестен: записали без ответа, перечитать не удалось. Журнал
    остаётся pending. Повтор сначала перечитывает строку, находит наш id и
    второй записи не делает."""
    rig = _rig()
    _reread_fails_after_write(rig, monkeypatch, applied=True)

    with pytest.raises(WriteNotConfirmedError) as caught:
        rig.fill()

    assert str(caught.value) == f"Google-таблица не ответила. {NOT_CONFIRMED}"
    assert not caught.value.layout_confirmed
    record = rig.journal.only()
    assert record.status == PENDING
    assert record.error is not None and "ReadTimeout" in record.error
    monkeypatch.undo()

    result = rig.fill()

    assert result == FillResult(row=6, ref_id="131", journal_id=1, already=True)
    assert rig.journal.only().status == VERIFIED
    assert rig.writes() == ["values_batch_update"], "запись — только первой попытки"
    assert rig.line(6)[0] == "131"


def test_retry_writes_again_when_the_interrupted_attempt_did_not_land(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Прерванная попытка не легла: строка такая же, как до неё. Повтор
    закрывает её failed и пишет заново — тем же id."""
    rig = _rig()
    _reread_fails_after_write(rig, monkeypatch, applied=False)
    with pytest.raises(WriteNotConfirmedError):
        rig.fill()
    monkeypatch.undo()

    result = rig.fill()

    assert result == FillResult(row=6, ref_id="131", journal_id=2, already=False)
    assert [r.status for r in rig.journal.records] == [FAILED, VERIFIED]
    assert rig.line(6)[0] == "131"


# ---------------------------------------------------------------------------
# Шеф сдвинул строки в окне записи
# ---------------------------------------------------------------------------
def test_rows_inserted_before_write_are_caught_and_not_cleaned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Шеф вставил строку над старыми ингредиентами между нашим чтением и
    записью: в строке 6 теперь «Моцарелла». Перечитывание видит в C не то
    название — раскладка не подтверждена: ничего не возвращаем (можно
    испортить ещё больше), в журнале — полный снимок листа, ERROR в лог.
    Следующий удачный перенос этой карточки несёт номер этой записи."""
    rig = _rig()
    rig.kitchen.chef_inserts_rows("ING", above=2, moment="before_write")

    with (
        caplog.at_level(logging.ERROR, logger=LOG),
        pytest.raises(WriteNotConfirmedError) as caught,
    ):
        rig.fill()

    assert not caught.value.layout_confirmed
    assert str(caught.value) == (
        "Лист ING меняли в ту же секунду — запись не подтверждена. Покажите шефу строку 6 "
        "листа ING (запись журнала №1)"
    )
    assert rig.writes() == ["values_batch_update"], "ничего не возвращали"
    record = rig.journal.only()
    assert (record.status, record.note) == (FAILED, LAYOUT_UNCONFIRMED)
    assert "sheet" in record.before
    assert "не подтверждена" in caplog.text

    retry = rig.fill()

    assert (retry.row, retry.ref_id) == (7, "132")
    assert retry.shifted == UnconfirmedWrite(id=1, row=6)


# ---------------------------------------------------------------------------
# Ворота записи
# ---------------------------------------------------------------------------
def test_filler_writes_only_columns_open_in_the_gates() -> None:
    """Всё, что писатель может записать, открыто воротами ING — и ничего
    сверх: те же десять ручных колонок."""
    rig = _rig()

    rig.fill()

    written = {cell[0] for cell in _touched(rig.kitchen)}
    assert written == {c.letter for c in SPEC.writable()}
