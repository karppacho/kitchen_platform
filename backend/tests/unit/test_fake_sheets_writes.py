"""Фальшивая таблица умеет писать — и отказывает там же, где Google.

На этой фальшивке проверяется писатель строки карточки, поэтому она обязана
вести себя как Sheets API в том, на что писатель опирается: диапазон в
кавычках, RAW против USER_ENTERED, FORMATTED против UNFORMATTED, край сетки и
appendDimension, Decimal в теле запроса. Фальшивка, принимающая всё, дала бы
зелёные тесты писателю, который падает на первой настоящей записи.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING

import gspread
import pytest
import requests
from gspread.exceptions import APIError

from kitchen.sync import client as protocol
from tests.conftest import FakeSpreadsheet, FakeWorksheet

if TYPE_CHECKING:
    from collections.abc import Callable

HEAD = ["Категория", "Наименование", "Поставщик"]
ROW_2 = ["Соусы", "Кетчуп", "Север"]


def _book(
    values: list[list[str]] | None = None, *, rows: int | None = None
) -> tuple[FakeSpreadsheet, FakeWorksheet]:
    sheet = FakeWorksheet(values if values is not None else [HEAD, ROW_2], "Лист1", row_count=rows)
    return FakeSpreadsheet({"Лист1": sheet}), sheet


def _write(
    book: FakeSpreadsheet, *data: tuple[str, list[object]], option: str = "RAW"
) -> dict[str, object]:
    """Одна запись несколькими диапазонами, по строке в каждом."""
    return book.values_batch_update(
        {
            "valueInputOption": option,
            "data": [{"range": where, "values": [values]} for where, values in data],
        }
    )


def _read(book: FakeSpreadsheet, where: str, render: str | None = None) -> list[list[object]]:
    params = {"valueRenderOption": render} if render else None
    block = book.values_batch_get([where], params)["valueRanges"][0]
    return block.get("values", [])


def _grow(book: FakeSpreadsheet, sheet_id: int, length: int) -> None:
    book.batch_update(
        {
            "requests": [
                {"appendDimension": {"sheetId": sheet_id, "dimension": "ROWS", "length": length}}
            ]
        }
    )


# ---------------------------------------------------------------------------
# Диапазоны A1
# ---------------------------------------------------------------------------
def test_card_row_lands_in_two_ranges_and_spares_q_and_r() -> None:
    """Строка карточки уходит двумя диапазонами — A–P и S–V. Q и R между
    ними вписаны людьми и остаются как были."""
    people = [""] * 16 + ["декларация", "халяль"]
    book, _ = _book([HEAD, [], [], [], [], people])
    ours = [f"a{i}" for i in range(16)]

    reply = _write(book, ("'Лист1'!A6:P6", ours), ("'Лист1'!S6:V6", ["s", "t", "u", "Да"]))

    row = _read(book, "'Лист1'!A6:V6")[0]
    assert row[:16] == ours
    assert row[16:18] == ["декларация", "халяль"]
    assert row[18:] == ["s", "t", "u", "Да"]
    assert reply["totalUpdatedCells"] == 20


def test_range_needs_quotes_around_sheet_name() -> None:
    """Фальшивка строже Google: без кавычек отказывает всегда.

    Google простое имя поймёт, а «Расчётка меню» с пробелом — уже нет.
    Писатель, забывший кавычки, должен упасть здесь, а не на первом листе
    с пробелом в имени.
    """
    book, _ = _book()

    with pytest.raises(APIError, match="Unable to parse range") as caught:
        _write(book, ("Лист1!A2:B2", ["x", "y"]))

    assert caught.value.code == 400
    assert _read(book, "'Лист1'!A2:B2") == [["Соусы", "Кетчуп"]]


def test_quote_inside_sheet_name_is_doubled() -> None:
    book = FakeSpreadsheet({"Шеф's": FakeWorksheet([["a"]], "Шеф's")})

    _write(book, ("'Шеф''s'!B1", ["b"]))

    assert _read(book, "'Шеф''s'!A1:B1") == [["a", "b"]]


def test_unknown_sheet_is_refused() -> None:
    book, _ = _book()
    with pytest.raises(APIError, match="Unable to parse range"):
        _read(book, "'Лист2'!A1:B1")


def test_values_wider_than_range_refuse_the_whole_request() -> None:
    """17 значений в A–P: Google отказывает, а не дописывает Q. Запрос
    атомарен — соседний верный диапазон тоже не ложится."""
    book, _ = _book()

    with pytest.raises(APIError, match=r"tried writing to column \[Q\]"):
        _write(book, ("'Лист1'!S3:V3", ["s", "t", "u", "v"]), ("'Лист1'!A3:P3", ["x"] * 17))

    assert _read(book, "'Лист1'!A3:V3") == []


def test_single_cell_range_is_a_starting_point() -> None:
    book, _ = _book()

    _write(book, ("'Лист1'!A3", ["a", "b", "c"]))

    assert _read(book, "'Лист1'!A3:D3") == [["a", "b", "c"]]


# ---------------------------------------------------------------------------
# RAW против USER_ENTERED, FORMATTED против UNFORMATTED
# ---------------------------------------------------------------------------
def test_raw_keeps_text_as_text() -> None:
    """RAW — значения ложатся как есть. «=IMPORTXML(…)» с этикетки остаётся
    текстом, «12,5» — строкой, апостроф — частью текста."""
    book, _ = _book()

    _write(book, ("'Лист1'!A3:C3", ["=1+1", "12,5", "'текст"]))

    assert _read(book, "'Лист1'!A3:C3", "UNFORMATTED_VALUE") == [["=1+1", "12,5", "'текст"]]


def test_user_entered_reads_input_like_a_person() -> None:
    """USER_ENTERED — как будто набрал человек: «=…» становится формулой,
    «12,5» — числом, апостроф впереди оставляет текст. Формулы фальшивка не
    вычисляет и читает как «#ERROR!» — записанная формула видна при сверке."""
    book, _ = _book()

    _write(book, ("'Лист1'!A3:D3", ["=1+1", "12,5", "'=1+1", "Соус"]), option="USER_ENTERED")

    assert _read(book, "'Лист1'!A3:D3", "UNFORMATTED_VALUE") == [["#ERROR!", 12.5, "=1+1", "Соус"]]


def test_raw_numbers_read_back_by_render_option() -> None:
    """Число, записанное RAW, остаётся числом. FORMATTED_VALUE (по умолчанию)
    отдаёт его так, как видит шеф, — с запятой; UNFORMATTED_VALUE — само
    число."""
    book, sheet = _book()

    _write(book, ("'Лист1'!A3:C3", [12.5, 130, 0.1]))

    unformatted = _read(book, "'Лист1'!A3:C3", "UNFORMATTED_VALUE")
    assert unformatted == [[12.5, 130, 0.1]]
    assert [type(value) for value in unformatted[0]] == [float, int, float]
    assert _read(book, "'Лист1'!A3:C3") == [["12,5", "130", "0,1"]]
    assert _read(book, "'Лист1'!A3:C3", "FORMATTED_VALUE") == [["12,5", "130", "0,1"]]
    assert sheet.get_all_values()[2] == ["12,5", "130", "0,1"]


@pytest.mark.parametrize("text", ["12.5", "5%", "01.02.2026", "1 030"])
def test_user_entered_refuses_what_google_would_guess(text: str) -> None:
    """Русский Google сделал бы из «12.5» дату, из «5%» — долю, из «1 030» —
    число. Фальшивка таких догадок не моделирует и честно падает, а не
    оставляет текст, которого в жизни не будет. Запрос не ложится целиком."""
    book, _ = _book()

    with pytest.raises(AssertionError, match="USER_ENTERED"):
        _write(book, ("'Лист1'!A3:B3", ["Соус", text]), option="USER_ENTERED")

    assert _read(book, "'Лист1'!A3:B3") == []


def test_batch_get_writes_ranges_into_callers_params() -> None:
    """gspread 6.2 дописывает ranges прямо в переданный словарь. Словарь-
    константа испортится после первого вызова, неизменяемое отображение
    упадёт TypeError — фальшивка ведёт себя так же."""
    book, _ = _book()
    params = {"valueRenderOption": "UNFORMATTED_VALUE"}

    book.values_batch_get(["'Лист1'!A2:B2"], params)

    assert params["ranges"] == ["'Лист1'!A2:B2"]
    # Протокол и не обещает не трогать словарь: в нём dict, а не Mapping.
    annotation = inspect.signature(protocol.Spreadsheet.values_batch_get).parameters["params"]
    assert str(annotation.annotation) == "dict[str, str] | None"
    with pytest.raises(TypeError):
        book.values_batch_get(
            ["'Лист1'!A2"], MappingProxyType({"valueRenderOption": "FORMATTED_VALUE"})
        )


def test_decimal_never_reaches_google() -> None:
    """requests кодирует тело через json.dumps, а тот Decimal не знает: запрос
    падает до отправки. Превратить Decimal в число JSON писатель обязан сам."""
    book, _ = _book()
    sent = book.requests

    with pytest.raises(TypeError, match="Decimal"):
        _write(book, ("'Лист1'!A3", [Decimal("0.1")]))

    assert book.requests == sent
    assert _read(book, "'Лист1'!A3:A3") == []


def test_value_input_option_is_required() -> None:
    book, _ = _book()
    with pytest.raises(APIError, match="valueInputOption"):
        book.values_batch_update({"data": [{"range": "'Лист1'!A3", "values": [["x"]]}]})


def test_null_leaves_the_cell_as_is() -> None:
    """null в теле — «не трогать ячейку», а не «очистить»; очищает пустая
    строка."""
    book, _ = _book()

    _write(book, ("'Лист1'!A2:C2", [None, "", "Юг"]))

    assert _read(book, "'Лист1'!A2:C2") == [["Соусы", "", "Юг"]]


# ---------------------------------------------------------------------------
# Сетка листа
# ---------------------------------------------------------------------------
def test_row_past_the_grid_exceeds_limits() -> None:
    """Сетка листа конечна: за её краем Google не пишет и не читает."""
    book, _ = _book(rows=2)

    with pytest.raises(APIError, match="exceeds grid limits") as caught:
        _write(book, ("'Лист1'!A3:B3", ["x", "y"]))
    assert caught.value.code == 400

    with pytest.raises(APIError, match="exceeds grid limits"):
        _read(book, "'Лист1'!A3:B3")


def test_append_dimension_makes_room() -> None:
    book, sheet = _book(rows=2)

    _grow(book, sheet.id, 1)
    _write(book, ("'Лист1'!A3:B3", ["x", "y"]))

    assert _read(book, "'Лист1'!A3:B3") == [["x", "y"]]


def test_row_count_is_a_snapshot_like_gspread() -> None:
    """gspread берёт row_count из свойств листа при открытии и после
    appendDimension его не обновляет: свежее число — только у заново
    открытого листа."""
    book, _ = _book(rows=2)
    opened = book.worksheet("Лист1")

    _grow(book, opened.id, 3)
    reopened = book.worksheet("Лист1")
    _write(book, ("'Лист1'!A5", ["x"]))

    assert reopened.row_count == 5
    assert opened.row_count == 2, "старый объект-лист держит прежнее число и после нового открытия"
    assert opened.get_all_values()[4] == ["x"], "ячейки у открытых листов общие"


def test_append_dimension_to_unknown_sheet_is_refused() -> None:
    book, sheet = _book()
    with pytest.raises(APIError, match="No grid with id"):
        _grow(book, sheet.id + 1, 1)


# ---------------------------------------------------------------------------
# Очистка
# ---------------------------------------------------------------------------
def test_batch_clear_empties_only_the_given_cells() -> None:
    """Откат писателя — очистка своих ячеек, а не удаление строки: строка
    остаётся на месте, соседние ячейки целы."""
    book, _ = _book()

    book.values_batch_clear(body={"ranges": ["'Лист1'!A2", "'Лист1'!C2"]})

    assert _read(book, "'Лист1'!A2:C2") == [["", "Кетчуп"]]


# ---------------------------------------------------------------------------
# Отказы и чужая правка
# ---------------------------------------------------------------------------
def test_failure_before_apply_changes_nothing() -> None:
    """Google отказал, ничего не записав. Отказ одноразовый: следующая
    попытка проходит."""
    book, _ = _book()
    book.fail_next("values_batch_update", applied=False)

    with pytest.raises(APIError) as caught:
        _write(book, ("'Лист1'!A3", ["x"]))
    assert caught.value.code == 503
    assert _read(book, "'Лист1'!A3:A3") == []

    _write(book, ("'Лист1'!A3", ["x"]))
    assert _read(book, "'Лист1'!A3:A3") == [["x"]]


def test_failure_after_apply_keeps_the_write() -> None:
    """Самый коварный исход: Google записал, а ответ потерялся по дороге.
    Код видит исключение, а в листе уже наша строка."""
    book, _ = _book()
    book.fail_next("values_batch_update", applied=True)

    with pytest.raises(requests.exceptions.ReadTimeout):
        _write(book, ("'Лист1'!A3", ["x"]))

    assert _read(book, "'Лист1'!A3:A3") == [["x"]]


@pytest.mark.parametrize("method", ["worksheet", "worksheets"])
def test_opening_a_sheet_can_fail_too(method: str) -> None:
    """Открыть лист — первый запрос писателя, и Google может отказать уже на
    нём. Отказ одноразовый."""
    book, _ = _book()
    book.fail_next(method, applied=False)
    open_sheet = (lambda: book.worksheet("Лист1")) if method == "worksheet" else book.worksheets

    with pytest.raises(APIError) as caught:
        open_sheet()
    assert caught.value.code == 503

    open_sheet()


def test_failure_carries_the_given_error() -> None:
    book, _ = _book()
    book.fail_next("values_batch_clear", applied=False, error=RuntimeError("сеть"))

    with pytest.raises(RuntimeError, match="сеть"):
        book.values_batch_clear(body={"ranges": ["'Лист1'!A2"]})

    assert _read(book, "'Лист1'!A2:A2") == [["Соусы"]]


def test_cell_changed_between_write_and_check() -> None:
    """Шеф вписал своё в нашу строку сразу после записи — раньше, чем
    писатель перечитал её для сверки. Запись прошла без ошибки, а при
    перечитывании одна ячейка чужая."""
    book, _ = _book()
    book.tamper_after_write("'Лист1'!B3", "шеф")

    _write(book, ("'Лист1'!A3:C3", ["a", "b", "c"]))

    assert _read(book, "'Лист1'!A3:C3") == [["a", "шеф", "c"]]


def test_chef_inserts_row_before_our_write() -> None:
    """Шеф вставил строку над строкой 2 между нашим чтением и записью:
    «Соусы» съехали в строку 3, и запись в строку 3 ложится поверх них —
    то, что писатель обязан заметить по строке выше."""
    book, _ = _book(rows=3)
    book.chef_inserts_rows("Лист1", above=2, moment="before_write")

    _write(book, ("'Лист1'!A3:B3", ["x", "y"]))

    assert _read(book, "'Лист1'!A1:C3") == [HEAD, [], ["x", "y", "Север"]]
    assert book.worksheet("Лист1").row_count == 4, "сетка растёт вместе со вставкой"
    assert book.chef_waiting() == []


def test_chef_inserts_rows_after_our_write() -> None:
    """Две строки вставлены над нашей сразу после записи: наша уехала на две
    вниз, а по её прежнему номеру — чужое."""
    book, _ = _book()
    book.chef_inserts_rows("Лист1", above=2, count=2, moment="after_write")

    _write(book, ("'Лист1'!A3", ["наша"]))

    assert _read(book, "'Лист1'!A2:A5") == [[], [], ["Соусы"], ["наша"]]


def test_chef_cell_before_our_write_is_overwritten() -> None:
    """Правка шефа до нашей записи в тех же ячейках затирается ею, в
    остальных — остаётся."""
    book, _ = _book()
    book.chef_edits_cell("'Лист1'!B3", "шеф", moment="before_write")
    book.chef_edits_cell("'Лист1'!D3", "заметка", moment="before_write")

    _write(book, ("'Лист1'!A3:C3", ["a", "b", "c"]))

    assert _read(book, "'Лист1'!A3:D3") == [["a", "b", "c", "заметка"]]


def test_chef_acts_right_after_clear() -> None:
    book, _ = _book()
    book.chef_inserts_rows("Лист1", above=1, moment="after_clear")

    book.values_batch_clear(body={"ranges": ["'Лист1'!C2"]})

    assert _read(book, "'Лист1'!A2:C3") == [HEAD, ["Соусы", "Кетчуп"]]


def test_chef_waits_for_his_moment() -> None:
    """Правка на окно «после записи» ждёт записи, которая легла: чтение её
    не будит, отказ до применения — тоже."""
    book, _ = _book()
    book.chef_edits_cell("'Лист1'!A3", "шеф", moment="after_write")
    book.fail_next("values_batch_update", applied=False)

    _read(book, "'Лист1'!A2:B2")
    with pytest.raises(APIError):
        _write(book, ("'Лист1'!B3", ["x"]))
    assert book.chef_waiting() == ["after_write"]

    _write(book, ("'Лист1'!B3", ["x"]))
    assert _read(book, "'Лист1'!A3:B3") == [["шеф", "x"]]
    assert book.chef_waiting() == []


@pytest.mark.parametrize(
    "order",
    [
        lambda book: book.chef_edits_cell("'Лист1'!A3", "x", moment="потом"),
        lambda book: book.chef_edits_cell("'Лист1'!A3:B3", "x", moment="after_write"),
        lambda book: book.chef_inserts_rows("Лист2", above=2, moment="after_write"),
        lambda book: book.chef_inserts_rows("Лист1", above=0, moment="after_write"),
        lambda book: book.chef_inserts_rows("Лист1", above=2, count=0, moment="after_write"),
        lambda book: book.chef_inserts_rows("Лист1", above=5, moment="after_write"),
    ],
    ids=["moment", "range", "sheet", "row-zero", "count-zero", "past-grid"],
)
def test_chef_orders_are_checked(order: Callable[[FakeSpreadsheet], None]) -> None:
    """Непонятный заказ — отказ сразу, а не правка, которая никогда не
    сработает."""
    book, _ = _book(rows=4)
    with pytest.raises(AssertionError):
        order(book)
    assert book.chef_waiting() == []


def test_requests_are_logged_as_sent() -> None:
    """Журнал запросов — то, что ушло бы в Google после JSON. По нему тесты
    писателя проверяют RAW, диапазоны и порядок «сначала сетка, потом
    запись»."""
    book, sheet = _book(rows=2)

    _grow(book, sheet.id, 1)
    _write(book, ("'Лист1'!A3", [0.1]))

    assert [name for name, _ in book.calls] == ["batch_update", "values_batch_update"]
    assert book.calls[-1][1] == {
        "valueInputOption": "RAW",
        "data": [{"range": "'Лист1'!A3", "values": [[0.1]]}],
    }


# ---------------------------------------------------------------------------
# Протокол
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "method", ["values_batch_get", "values_batch_update", "values_batch_clear", "batch_update"]
)
def test_fake_speaks_gspread_signatures(method: str) -> None:
    """Протокол, фальшивка и gspread 6.2 называют параметры одинаково: вызов
    ``values_batch_clear(body=…)`` обязан работать и в тесте, и в жизни."""

    def names(function: Callable[..., object]) -> list[str]:
        return list(inspect.signature(function).parameters)

    expected = names(getattr(gspread.Spreadsheet, method))
    assert names(getattr(protocol.Spreadsheet, method)) == expected
    assert names(getattr(FakeSpreadsheet, method)) == expected


def test_worksheet_exposes_id_and_row_count() -> None:
    for name in ("id", "row_count", "title"):
        assert isinstance(getattr(gspread.Worksheet, name), property)

    sheet = FakeWorksheet([["a"]], "Лист1", row_count=7)

    assert isinstance(sheet, protocol.Worksheet)
    assert sheet.row_count == 7
    assert isinstance(sheet.id, int)

    # Писатель работает с листом, который вернуло открытие, а не с исходным.
    opened = FakeSpreadsheet({"Лист1": sheet}).worksheet("Лист1")
    assert isinstance(opened, protocol.Worksheet)
    assert (opened.id, opened.row_count, opened.title) == (sheet.id, 7, "Лист1")
