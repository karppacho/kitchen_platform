"""Строка справочника ING из карточки: форма переноса и поиск строки.

Запись в ING — вторая запись платформы в книгу кухни, и ошибка здесь тихая:
цена или id, вписанные не в ту строку, достаются чужому ингредиенту, и
себестоимость считается по чужим числам правдоподобно. Поэтому тесты в
первую очередь проверяют отказы: строку, которую нельзя найти однозначно,
код не угадывает.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest

from kitchen.domain.cards import TEXT_LIMITS
from kitchen.domain.reference_row import (
    ACTIVE_STATUS,
    ANCHOR_LETTER,
    ID_LETTER,
    LOSSES_TOTAL_LETTER,
    MANUAL_LETTERS,
    NAME_LETTER,
    SHORT_NAME_LIMIT,
    AlreadyFilled,
    Located,
    NotFound,
    ReferenceForm,
    ReferenceFormError,
    ReferenceLayoutError,
    find_query_anchor,
    formula_cells,
    locate_row,
    next_reference_id,
)
from kitchen.sync import specs
from kitchen.sync.ownership import WRITE_OPEN

_D = Decimal


# ---------------------------------------------------------------------------
# Форма
# ---------------------------------------------------------------------------
def _form(**changes: str | None) -> dict[str, str | None]:
    """Заполненная форма весового ингредиента; ``changes`` — поправки к ней."""
    form: dict[str, str | None] = {
        "short_name": "Кетчуп",
        "unit": "кг",
        "price_per_pack": "450",
        "price_per_kg": "90",
        "weight_per_piece_g": "",
        "losses_unpacking": "",
        "losses_cutting": "",
        "losses_thermal": "",
    }
    form.update(changes)
    return form


def _errors(**changes: str | None) -> dict[str, str]:
    with pytest.raises(ReferenceFormError) as caught:
        ReferenceForm.parse(_form(**changes))
    return caught.value.errors


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12,5", _D("12.5")),
        ("12.5", _D("12.5")),
        (" 1 030,50 ", _D("1030.50")),
        ("р.443,00", _D("443.00")),
        ("0", _D("0")),
    ],
)
def test_form_reads_numbers_as_people_write_them(raw: str, expected: Decimal) -> None:
    form = ReferenceForm.parse(_form(price_per_pack=raw, price_per_kg=raw))

    assert form.price_per_pack == expected
    assert form.price_per_kg == expected
    assert isinstance(form.price_per_pack, Decimal)


@pytest.mark.parametrize("raw", ["abc", "12,5,1", "NaN", "Infinity", "12%", "1-2"])
def test_form_refuses_what_is_not_a_number_at_the_field(raw: str) -> None:
    """«12%» в цене — не число, а догадка: процент отбрасывать молча нельзя."""
    errors = _errors(price_per_pack=raw)

    assert set(errors) == {"price_per_pack"}
    assert "не число" in errors["price_per_pack"]


def test_form_refuses_negative_price() -> None:
    errors = _errors(price_per_kg="-5")

    assert set(errors) == {"price_per_kg"}
    assert "меньше нуля" in errors["price_per_kg"]


def test_empty_price_stays_empty_not_zero() -> None:
    """Пустая цена — «не трогать ячейку», а не ноль: ноль — это утверждение."""
    form = ReferenceForm.parse(_form(price_per_pack="", price_per_kg=None))

    assert form.price_per_pack is None
    assert form.price_per_kg is None


@pytest.mark.parametrize(("raw", "unit"), [("кг", "кг"), ("л", "л"), (" Шт ", "шт"), ("КГ", "кг")])
def test_form_unit_is_kg_litre_or_piece(raw: str, unit: str) -> None:
    weight = "60" if unit == "шт" else ""
    form = ReferenceForm.parse(_form(unit=raw, weight_per_piece_g=weight))

    assert form.unit == unit


@pytest.mark.parametrize("raw", ["мл", "г", "штука", "", None])
def test_form_refuses_other_units(raw: str | None) -> None:
    errors = _errors(unit=raw)

    assert set(errors) == {"unit"}
    assert "кг, л или шт" in errors["unit"]


def test_piece_needs_its_weight() -> None:
    """Без веса штуки штучный ингредиент не посчитать: в ТТК граммы."""
    form = ReferenceForm.parse(_form(unit="шт", weight_per_piece_g="60,5"))
    assert form.weight_per_piece_g == _D("60.5")

    for missing in ("", None, "   "):
        errors = _errors(unit="шт", weight_per_piece_g=missing)
        assert set(errors) == {"weight_per_piece_g"}
        assert "обязателен" in errors["weight_per_piece_g"]


@pytest.mark.parametrize("raw", ["0", "-60", "0,0"])
def test_piece_weight_must_be_above_zero(raw: str) -> None:
    errors = _errors(unit="шт", weight_per_piece_g=raw)

    assert set(errors) == {"weight_per_piece_g"}
    assert "больше нуля" in errors["weight_per_piece_g"]


@pytest.mark.parametrize("unit", ["кг", "л"])
def test_weight_is_empty_for_kg_and_litre(unit: str) -> None:
    """Вес штуки у весового — не угадываем, что человек имел в виду."""
    form = ReferenceForm.parse(_form(unit=unit, weight_per_piece_g=""))
    assert form.weight_per_piece_g is None

    errors = _errors(unit=unit, weight_per_piece_g="60")
    assert set(errors) == {"weight_per_piece_g"}
    assert "только для единицы «шт»" in errors["weight_per_piece_g"]


@pytest.mark.parametrize(
    ("raw", "share"),
    [
        ("5", _D("0.05")),
        ("12,5", _D("0.125")),
        ("5%", _D("0.05")),
        ("0", _D("0")),
        ("99,9", _D("0.999")),
    ],
)
def test_losses_are_entered_in_percent_and_kept_as_share(raw: str, share: Decimal) -> None:
    """Человек пишет «5» — это 5 %; в лист уходит доля 0,05."""
    form = ReferenceForm.parse(_form(losses_unpacking=raw, losses_cutting=raw, losses_thermal=raw))

    assert form.losses_unpacking == share
    assert form.losses_cutting == share
    assert form.losses_thermal == share


def test_losses_default_to_zero() -> None:
    form = ReferenceForm.parse(_form(losses_unpacking="", losses_cutting=None))

    assert form.losses_unpacking == _D("0")
    assert form.losses_cutting == _D("0")
    assert form.losses_thermal == _D("0")


@pytest.mark.parametrize("field", ["losses_unpacking", "losses_cutting", "losses_thermal"])
@pytest.mark.parametrize("raw", ["-1", "100", "100%", "100,5", "500"])
def test_each_loss_is_from_0_to_below_100(field: str, raw: str) -> None:
    """100 % потерь — от продукта ничего не остаётся.

    Расчёт делит на (1 − потери): у калькулятора стоимость молча
    становится нулём, у формул листа — «#ДЕЛ/0!». Поэтому 100 — отказ.
    """
    errors = _errors(**{field: raw})

    assert set(errors) == {field}
    assert "от 0 до 100 %, меньше 100" in errors[field]


def test_loss_that_is_not_a_number_is_refused() -> None:
    errors = _errors(losses_cutting="много")

    assert set(errors) == {"losses_cutting"}
    assert "не число" in errors["losses_cutting"]


def test_short_name_is_trimmed() -> None:
    form = ReferenceForm.parse(_form(short_name="  Кетчуп   томатный \t"))

    assert form.short_name == "Кетчуп томатный"


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_short_name_is_required(raw: str | None) -> None:
    errors = _errors(short_name=raw)

    assert set(errors) == {"short_name"}
    assert "обязательно" in errors["short_name"]


def test_short_name_has_a_limit() -> None:
    at_limit = "к" * SHORT_NAME_LIMIT
    assert ReferenceForm.parse(_form(short_name=at_limit)).short_name == at_limit

    errors = _errors(short_name=at_limit + "к")
    assert set(errors) == {"short_name"}
    assert f"не длиннее {SHORT_NAME_LIMIT}" in errors["short_name"]


def test_short_name_limit_is_not_hidden_by_cleaning() -> None:
    """Чистка — та же, что у названия карточки, и обрезает по его пределу.

    Свой предел обязан быть короче: иначе длинное имя молча обрезалось бы
    чисткой, а не отказывало.
    """
    assert TEXT_LIMITS["name"] > SHORT_NAME_LIMIT
    errors = _errors(short_name="к" * (TEXT_LIMITS["name"] + 50))
    assert set(errors) == {"short_name"}


def test_status_is_active() -> None:
    form = ReferenceForm.parse(_form())

    assert form.status == ACTIVE_STATUS == "активный"


def test_form_reports_every_wrong_field_at_once() -> None:
    """Человек видит все ошибки сразу, а не по одной за попытку."""
    errors = _errors(short_name="", unit="мл", price_per_pack="abc", losses_thermal="200")

    assert set(errors) == {"short_name", "unit", "price_per_pack", "losses_thermal"}


def test_form_error_names_the_field_in_russian() -> None:
    errors = _errors(price_per_pack="abc")

    assert errors["price_per_pack"].startswith("Цена за упаковку")


def test_form_values_go_to_manual_columns_in_sheet_order() -> None:
    """Поля формы названы как поля ING: писатель берёт букву из раскладки.

    Всё, что даёт форма, — ручные колонки строки, кроме id (его выдаёт
    платформа): E, L, M, N, O, Q, R, S, T.
    """
    form = ReferenceForm.parse(_form(losses_cutting="5"))
    values = form.row_values()

    letters = [specs.INGREDIENTS.column(field).letter for field in values]
    assert letters == [*"ELMNOQRST"]
    assert values["losses_cutting"] == _D("0.05")
    assert values["status"] == "активный"
    assert values["weight_per_piece_g"] is None


# ---------------------------------------------------------------------------
# id
# ---------------------------------------------------------------------------
def test_next_id_is_numeric_max_plus_one() -> None:
    """По числу, а не по тексту: «99» > «130» как строки."""
    assert next_reference_id(["1", "130", "99", "", "x"]) == "131"


def test_next_id_of_empty_reference_is_one() -> None:
    assert next_reference_id([]) == "1"
    assert next_reference_id(["id", "", "x"]) == "1"


def test_next_id_reads_numbers_from_unformatted_reading() -> None:
    """В UNFORMATTED/FORMULA-чтении id приезжают числами."""
    assert next_reference_id([1, 130, 99, ""]) == "131"


def test_next_id_ignores_what_is_not_a_whole_number() -> None:
    assert next_reference_id(["5", "7,5", "-3", None]) == "6"


# ---------------------------------------------------------------------------
# Лист ING: имитация двух чтений
# ---------------------------------------------------------------------------
_QUERY_B = "=QUERY('Импорт карточек'!A3:V;\"select A, B, C where V = 'Да'\";0)"
_HEADER = ["id", "Категория", "Наименование ингредиента"]

type Rows = list[list[object]]


def _ing(
    zone: Sequence[tuple[str, str]],
    *,
    old: Sequence[tuple[str, str]] = (("1", "Лук"), ("2", "Морковь")),
    templates: int = 2,
) -> tuple[Rows, Rows, int]:
    """Лист ING двумя чтениями — FORMATTED и FORMULA — и строка якоря.

    Шапка; старые строки, вписанные руками целиком; зона QUERY — пары
    (id, название), пустой id — строка ждёт переноса; ниже — заготовки.
    Вывод QUERY виден в FORMATTED и пуст в FORMULA, как в настоящем листе;
    формула стоит только в B строки якоря. id в FORMULA — числа.
    """
    formatted: Rows = [list(_HEADER)]
    formula: Rows = [list(_HEADER)]
    for ref_id, name in old:
        formatted.append([ref_id, "Овощи", name])
        formula.append([int(ref_id) if ref_id else "", "Овощи", name])
    anchor = len(formatted) + 1
    for offset, (ref_id, name) in enumerate(zone):
        formatted.append([ref_id, "Соусы", name])
        formula.append([int(ref_id) if ref_id else "", _QUERY_B if offset == 0 else "", ""])
    for _ in range(templates):
        formatted.append(["", "", ""])
        formula.append(["", "", ""])
    return formatted, formula, anchor


# ---------------------------------------------------------------------------
# Якорь
# ---------------------------------------------------------------------------
def test_anchor_is_the_row_with_query_in_b() -> None:
    _formatted, formula, anchor = _ing([("3", "Кетчуп"), ("", "Майонез")])

    assert find_query_anchor(formula) == anchor == 4


@pytest.mark.parametrize(
    "cell",
    [_QUERY_B, "=query(A1:C)", "  =QUERY(A1:C)", '=IFERROR(QUERY(A1:C;"select *");"")'],
)
def test_anchor_survives_how_the_formula_is_written(cell: str) -> None:
    rows: Rows = [list(_HEADER), ["1", "Овощи", "Лук"], ["", cell, ""]]

    assert find_query_anchor(rows) == 3


@pytest.mark.parametrize(
    "rows",
    [
        # Без формулы вовсе.
        [list(_HEADER), ["1", "Овощи", "Лук"]],
        # Слово QUERY без «=» — текст, а не формула.
        [list(_HEADER), ["", "QUERY(A1:C)", ""]],
        # QUERY только в F — вторая формула, а якорь — в B.
        [list(_HEADER), ["", "", "", "", "", "=QUERY(A1:K)"]],
        # Значения FORMATTED-чтения формулу не показывают.
        [list(_HEADER), ["", "Соусы", "Кетчуп"]],
        [],
    ],
)
def test_no_anchor_is_refused(rows: Rows) -> None:
    with pytest.raises(ReferenceLayoutError, match="формула подтягивания карточек не найдена"):
        find_query_anchor(rows)


# ---------------------------------------------------------------------------
# Поиск строки по названию и месту
# ---------------------------------------------------------------------------
def test_name_and_place_agree_on_one_row() -> None:
    formatted, formula, anchor = _ing([("3", "Кетчуп"), ("", "Майонез"), ("", "Горчица")])

    found = locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез", "Горчица"], "Майонез")

    assert found == Located(row=5)


def test_row_not_there_yet_is_not_yet() -> None:
    """IMPORTRANGE подтягивает с задержкой: карточка уже «Да», строки ещё нет."""
    formatted, formula, anchor = _ing([("3", "Кетчуп"), ("", "Майонез")])

    found = locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез", "Горчица"], "Горчица")

    assert found is NotFound.NOT_YET


def test_name_and_place_disagree_is_shifted() -> None:
    """Шеф переключил «Да» в середине книги карточек: названия съехали.

    По месту (третья «Да») — строка «Майонез», по названию — четвёртая
    строка. Писать нельзя ни туда, ни туда.
    """
    formatted, formula, anchor = _ing(
        [("3", "Кетчуп"), ("", "Аджика"), ("", "Майонез"), ("", "Горчица")]
    )
    approved = ["Кетчуп", "Аджика", "Горчица", "Майонез"]

    assert locate_row(formatted, formula, anchor, approved, "Горчица") is NotFound.SHIFTED
    assert locate_row(formatted, formula, anchor, approved, "Майонез") is NotFound.SHIFTED


def test_shift_is_seen_even_if_the_named_row_has_an_id() -> None:
    """Название стоит в строке с id, но не на своём месте — это сдвиг."""
    formatted, formula, anchor = _ing([("3", "Майонез"), ("", "Кетчуп")])

    found = locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез"], "Майонез")

    assert found is NotFound.SHIFTED


def test_row_with_id_is_already_filled() -> None:
    formatted, formula, anchor = _ing([("3", "Кетчуп"), ("131", "Майонез")])

    found = locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез"], "Майонез")

    assert found == AlreadyFilled(row=5, ref_id="131")


def test_place_picks_one_of_two_rows_with_the_same_name() -> None:
    """В листе два «Кетчупа», среди «Да» — один: место выбирает строку."""
    formatted, formula, anchor = _ing([("", "Кетчуп"), ("", "Майонез"), ("", "Кетчуп")])

    found = locate_row(formatted, formula, anchor, ["Аджика", "Майонез", "Кетчуп"], "Кетчуп")
    assert found == Located(row=6)

    found = locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез", "Аджика"], "Кетчуп")
    assert found == Located(row=4)


def test_namesakes_among_approved_are_ambiguous() -> None:
    """Два «Да» с одним названием: по месту не понять, которое — наше."""
    formatted, formula, anchor = _ing([("", "Кетчуп"), ("", "Майонез"), ("", "Кетчуп")])
    approved = ["Кетчуп", "Майонез", "Кетчуп"]

    assert locate_row(formatted, formula, anchor, approved, "Кетчуп") is NotFound.AMBIGUOUS


def test_namesakes_stay_ambiguous_when_one_is_already_filled() -> None:
    """Пустой A у одного из тёзок — не повод решить, что карточка — его."""
    formatted, formula, anchor = _ing([("3", "Кетчуп"), ("", "Майонез"), ("", "Кетчуп")])
    approved = ["Кетчуп", "Майонез", "Кетчуп"]

    assert locate_row(formatted, formula, anchor, approved, "Кетчуп") is NotFound.AMBIGUOUS


def test_card_not_approved_is_refused() -> None:
    formatted, formula, anchor = _ing([("3", "Кетчуп"), ("", "Майонез")])

    found = locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез"], "Горчица")

    assert found is NotFound.NOT_APPROVED


def test_names_compare_like_card_keys() -> None:
    """Регистр, «ё» и лишние пробелы — как у ключа карточки при импорте."""
    formatted, formula, anchor = _ing([("3", "Кетчуп"), ("", "Сгущённое  молоко ")])

    found = locate_row(
        formatted, formula, anchor, ["Кетчуп", "сгущенное молоко"], "Сгущённое молоко"
    )

    assert found == Located(row=5)


def test_rows_above_anchor_do_not_count() -> None:
    """Старые строки, вписанные руками, — не зона QUERY."""
    formatted, formula, anchor = _ing([("3", "Кетчуп")], old=(("1", "Лук"), ("", "Майонез")))

    found = locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез"], "Майонез")

    assert found is NotFound.NOT_YET


def test_name_typed_by_hand_is_not_a_query_row() -> None:
    """Название, вписанное руками под выводом QUERY, — не строка формулы.

    Вывод QUERY в FORMULA-чтении пуст; если в C что-то есть, это ввод
    человека, и писать в такую строку нельзя, даже если место совпало.
    """
    formatted, formula, anchor = _ing([("3", "Кетчуп")], templates=0)
    formatted.append(["", "", "Майонез"])
    formula.append(["", "", "Майонез"])

    found = locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез"], "Майонез")

    assert found is NotFound.NOT_YET


def test_ragged_rows_are_read_as_empty_cells() -> None:
    """Пакетное чтение не возвращает хвостовые пустые ячейки и строки."""
    formatted: Rows = [list(_HEADER), ["1", "Овощи", "Лук"], ["", "Соусы", "Кетчуп"]]
    formula: Rows = [list(_HEADER), [1, "Овощи", "Лук"], ["", _QUERY_B]]

    anchor = find_query_anchor(formula)
    found = locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез"], "Кетчуп")

    assert found == Located(row=3)
    assert locate_row(formatted, formula, anchor, ["Кетчуп", "Майонез"], "Майонез") is (
        NotFound.NOT_YET
    )


def test_refusals_explain_themselves_in_spec_words() -> None:
    assert NotFound.NOT_APPROVED.message == (
        "Карточка не согласована — в справочник попадают только «Да»"
    )
    assert NotFound.NOT_YET.message == (
        "Строка ещё не появилась в справочнике — таблица подтягивает карточки "
        "с задержкой, попробуйте через несколько минут"
    )
    assert NotFound.SHIFTED.message == (
        "Строки справочника сдвинуты относительно карточек — запись не сделана, проверьте лист ING"
    )
    assert NotFound.AMBIGUOUS.message == (
        "Согласованных карточек с таким названием несколько — не понять, какая строка "
        "справочника относится к этой. Запись не сделана: переименуйте одну из карточек"
    )


# ---------------------------------------------------------------------------
# Какие ячейки строки — формулы
# ---------------------------------------------------------------------------
def _formula_row(**cells: object) -> list[object]:
    """Строка ING в FORMULA-чтении, A–T: заготовка с формулой P."""
    row: list[object] = [""] * 20
    row[specs.INGREDIENTS.column("losses_total").index] = "=SUM(Q31+R31+S31)"
    for letter, value in cells.items():
        row[ord(letter) - ord("A")] = value
    return row


def test_formula_in_l_is_listed() -> None:
    row = _formula_row(L="=M31/6", M=450, N="кг", Q=0, R=0.05, S=0, T="активный")

    assert formula_cells(row) == ("L", "P")


def test_price_in_l_is_not_a_formula() -> None:
    row = _formula_row(L=90, M=450)

    assert formula_cells(row) == ("P",)


def test_p_is_always_a_formula_cell() -> None:
    """P — формула шефа по устройству листа; её не пишут, что бы там ни было."""
    assert formula_cells(_formula_row(P="")) == ("P",)
    assert formula_cells(_formula_row(P=0.05)) == ("P",)
    assert formula_cells([]) == ("P",)


def test_other_manual_formulas_are_listed_too() -> None:
    """Формула в A или потерях — писатель откажет, а не перезапишет её."""
    row = _formula_row(A="=ROW()-28", L="=(1000*M31)/2200", R="=Q31")

    assert formula_cells(row) == ("A", "L", "P", "R")


def test_query_output_columns_are_not_listed() -> None:
    """B–D и F–K закрыты воротами, а не этим списком."""
    row = _formula_row(B=_QUERY_B, F="=QUERY(A1:K)", H="=1+1")

    assert formula_cells(row) == ("P",)


# ---------------------------------------------------------------------------
# Буквы — те же, что в раскладке
# ---------------------------------------------------------------------------
def test_letters_follow_the_sheet() -> None:
    """Домен держит буквы ING сам: слои не дают ему импортировать раскладку."""
    ing = specs.INGREDIENTS
    assert ing.column("id").letter == ID_LETTER
    assert ing.column("category").letter == ANCHOR_LETTER
    assert ing.column("name").letter == NAME_LETTER
    assert ing.column("losses_total").letter == LOSSES_TOTAL_LETTER
    assert set(MANUAL_LETTERS) == WRITE_OPEN["kitchen"]["ING"]
    assert list(MANUAL_LETTERS) == sorted(MANUAL_LETTERS)
