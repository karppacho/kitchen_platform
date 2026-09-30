"""Домен карточки ингредиента: КБЖУ, проверки, строка для таблицы.

Карточка — первая строка, которую платформа сама допишет в общий лист.
Поэтому здесь проверяется не только разбор чисел, но и то, что строка
ровно такой ширины, как надо: Q и R (декларацию и халяль) заполняют люди,
и запись туда стёрла бы их работу.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from kitchen.domain.cards import (
    APPROVED,
    CARD_FIELDS,
    DEFAULT_CATEGORIES,
    LABEL_EXTRACTION_KEYS,
    LABEL_FIELDS,
    REJECTED,
    TEXT_LIMITS,
    CardDraftData,
    NutrientUnclearError,
    card_row,
    check_nutrients,
    clean_text,
    drive_view_url,
    label_fields_from_extraction,
    missing_for_submit,
    parse_nutrient,
    photo_file_name,
)
from kitchen.sync import specs
from kitchen.sync.ownership import Owner

_D = Decimal
# Невидимые символы — именами: в исходнике их не видно глазами, а смена
# направления письма в коде — известный способ спрятать подмену.
_ZWSP = "\N{ZERO WIDTH SPACE}"
_RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"


# ---------------------------------------------------------------------------
# КБЖУ: разбор строки с этикетки
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12,5 г", _D("12.5")),
        ("12,5", _D("12.5")),
        ("12.5", _D("12.5")),
        ("12,5г", _D("12.5")),
        ("12,5 гр.", _D("12.5")),
        ("7 g", _D("7")),
        # 0,1 не представимо в двоичной дроби: float здесь дал бы другое число.
        ("0,1", _D("0.1")),
        (" 3,3 ", _D("3.3")),
        ("0", _D("0")),
        ("250 ккал", _D("250")),
        ("250 ккал / 1046 кДж", _D("250")),
        ("1046 кДж / 250 ккал", _D("250")),
        ("1 046 кДж (250 ккал)", _D("250")),
        ("250 kcal", _D("250")),
        ("ккал 250", _D("250")),
        (_D("12.5"), _D("12.5")),
        (7, _D("7")),
    ],
)
def test_parse_nutrient(raw: object, expected: Decimal) -> None:
    value = parse_nutrient(raw)
    assert value == expected
    assert type(value) is Decimal


@pytest.mark.parametrize(
    "raw",
    [None, "", "  ", "н/д", "Н/Д", "нд", "нет", "нет данных", "не указано", "—", "-"],
)
def test_no_data_is_none(raw: object) -> None:
    """Нет данных — пусто, а не ноль: «0 г жиров» — это утверждение."""
    assert parse_nutrient(raw) is None


@pytest.mark.parametrize(
    ("raw", "hint"),
    [
        ("<0,5", "не точное"),
        ("менее 0,5", "не точное"),
        ("≤ 0,5", "не точное"),
        ("не более 2", "не точное"),
        # Граница рядом с ккал не теряется, пока из строки вынимается число.
        ("до 250 ккал", "не точное"),
        ("<250 ккал / 1046 кДж", "не точное"),
        ("10-12", "диапазон"),
        ("10–12 г", "диапазон"),
        ("10...12", "диапазон"),
        ("abc", "не число"),
        ("nan", "не число"),
        ("1e5", "не число"),
        ("1046 кДж", "кДж"),
        ("5 %", "процент"),
    ],
)
def test_unclear_nutrient_is_refused(raw: str, hint: str) -> None:
    """Неточное число не угадываем: ни «0,5» из «<0,5», ни середину диапазона."""
    with pytest.raises(NutrientUnclearError, match=hint) as error:
        parse_nutrient(raw)
    # Повар должен узнать свою запись в сообщении.
    assert raw.strip() in str(error.value)


# ---------------------------------------------------------------------------
# КБЖУ: правдоподобие
# ---------------------------------------------------------------------------
def test_plausible_nutrients_are_quiet() -> None:
    # 4·10 + 9·5 + 4·20 = 165
    assert check_nutrients(_D("10"), _D("5"), _D("20"), _D("165")) == ()


def test_protein_over_hundred_is_a_warning() -> None:
    warnings = check_nutrients(_D("120"), None, None, None)
    assert len(warnings) == 1
    assert "Белки" in warnings[0]
    assert "120" in warnings[0]


def test_fat_and_carbs_over_hundred() -> None:
    fat_warnings = check_nutrients(None, _D("101"), None, None)
    carbs_warnings = check_nutrients(None, None, _D("100.5"), None)
    assert len(fat_warnings) == 1
    assert "Жиры" in fat_warnings[0]
    assert len(carbs_warnings) == 1
    assert "Углеводы" in carbs_warnings[0]


def test_kcal_over_nine_hundred_hints_at_kilojoules() -> None:
    warnings = check_nutrients(None, None, None, _D("1046"))
    assert len(warnings) == 1
    assert "кДж" in warnings[0]


def test_sum_over_hundred_is_a_warning() -> None:
    warnings = check_nutrients(_D("40"), _D("30"), _D("40"), None)
    assert len(warnings) == 1
    assert "вместе" in warnings[0]
    assert "110" in warnings[0]


def test_edge_values_are_allowed() -> None:
    """Масло: 100 г жира на 100 г и 900 ккал — так бывает."""
    assert check_nutrients(_D("0"), _D("100"), _D("0"), _D("900")) == ()


@pytest.mark.parametrize(
    ("protein", "fat", "carbs", "kcal", "warned"),
    [
        # Допуск — max(20 ккал, 20 % от указанного на этикетке).
        ("10", "0", "20", "100", False),  # 120 против 100: ровно 20
        ("10", "0", "20.25", "100", True),  # 121 против 100
        ("0", "20", "55", "500", False),  # 400 против 500: ровно 20 %
        ("0", "20", "54.75", "500", True),  # 399 против 500
        ("0", "0", "0", "15", False),  # 0 против 15: в пределах 20 ккал
    ],
)
def test_energy_against_macros(protein: str, fat: str, carbs: str, kcal: str, warned: bool) -> None:
    warnings = check_nutrients(_D(protein), _D(fat), _D(carbs), _D(kcal))
    assert bool(warnings) is warned
    if warned:
        assert len(warnings) == 1
        assert "выходит" in warnings[0]


def test_energy_warning_names_both_numbers() -> None:
    warnings = check_nutrients(_D("10"), _D("0"), _D("20.25"), _D("100"))
    assert "121" in warnings[0]
    assert "100" in warnings[0]


def test_energy_is_not_checked_without_all_numbers() -> None:
    assert check_nutrients(_D("10"), None, _D("20"), _D("500")) == ()
    assert check_nutrients(_D("10"), _D("5"), _D("20"), None) == ()


# ---------------------------------------------------------------------------
# Чистка текста
# ---------------------------------------------------------------------------
def test_clean_text_squashes_spaces_and_invisible_characters() -> None:
    assert clean_text("name", f"  Соус{_ZWSP}  Барбекю\t ") == "Соус Барбекю"
    assert clean_text("name", f"Соус{_RLO}Барбекю\x00") == "СоусБарбекю"
    assert clean_text("name", None) == ""


def test_clean_text_keeps_lines_only_in_description() -> None:
    assert clean_text("composition", "томаты,\nсахар") == "томаты, сахар"
    assert clean_text("description", "Густой\r\n\r\n  сладковатый  ") == "Густой\nсладковатый"


def test_clean_text_cuts_to_the_limit() -> None:
    cleaned = clean_text("name", "а" * 1000)
    assert len(cleaned) == TEXT_LIMITS["name"]


def test_clean_text_knows_only_card_fields() -> None:
    with pytest.raises(KeyError):
        clean_text("price", "1")


# ---------------------------------------------------------------------------
# Строка для таблицы
# ---------------------------------------------------------------------------
def _draft() -> CardDraftData:
    return CardDraftData(
        supplier="Поставщик «Север»",
        category="Соусы",
        name="Соус Барбекю",
        label_name="Соус барбекю томатный",
        manufacturer="Завод соусов",
        composition="томаты, сахар, уксус",
        protein=_D("1.2"),
        fat=_D("0.5"),
        carbs=_D("30"),
        kcal=_D("130"),
        shelf_life_sealed="12 месяцев (с 15.06.2025 до 15.06.2026)",
        shelf_life_after="72 часа при t +2..+6°C",
        description="Густой, сладковатый",
        approval=APPROVED,
        label_file_id="lbl123",
        package_file_id="pkg-123_x",
    )


def _sheet_fields() -> list[str]:
    return [c.field for c in specs.INGREDIENT_CARDS.columns if c.letter not in ("Q", "R")]


def test_card_row_has_exactly_twenty_fields_without_q_and_r() -> None:
    row = card_row(_draft())
    assert len(row) == 20
    assert "declaration" not in row
    assert "halal_certificate" not in row


def test_card_fields_follow_the_sheet() -> None:
    """Имена полей домен держит строками — сверяем их с раскладкой листа."""
    columns = specs.INGREDIENT_CARDS.columns
    assert list(CARD_FIELDS) == _sheet_fields()
    assert list(card_row(_draft())) == _sheet_fields()
    assert [c.letter for c in columns if c.field in CARD_FIELDS] == [
        *"ABCDEFGHIJKLMNOP",
        *"STUV",
    ]
    # Q и R — ровно те колонки, что принадлежат людям.
    assert {c.field for c in columns if c.owner is Owner.HUMAN} == {
        "declaration",
        "halal_certificate",
    }


def test_card_row_values() -> None:
    row = card_row(_draft())
    assert row["category"] == "Соусы"
    assert row["name"] == "Соус Барбекю"
    assert row["supplier"] == "Поставщик «Север»"
    assert row["protein"] == _D("1.2")
    assert type(row["protein"]) is Decimal
    assert row["kcal"] == _D("130")
    assert row["shelf_life_sealed"] == "12 месяцев (с 15.06.2025 до 15.06.2026)"
    assert row["shelf_life_defrost"] == ""
    assert row["label_url"] == "https://drive.google.com/file/d/lbl123/view"
    assert row["package_url"] == "https://drive.google.com/file/d/pkg-123_x/view"
    assert row["before_url"] == ""
    assert row["after_url"] == ""
    assert row["approval_status"] == "Да"


def test_card_row_leaves_unknown_numbers_empty() -> None:
    """Пустая ячейка, а не ноль: «0 г белка» — это утверждение."""
    row = card_row(replace(_draft(), protein=None, kcal=None))
    assert row["protein"] == ""
    assert row["kcal"] == ""


def test_card_row_cleans_text() -> None:
    row = card_row(replace(_draft(), name=f"  Соус{_ZWSP}  Барбекю\n"))
    assert row["name"] == "Соус Барбекю"


# ---------------------------------------------------------------------------
# Чего не хватает для отправки
# ---------------------------------------------------------------------------
def test_rejected_without_photos_can_be_submitted() -> None:
    """Как в боте: «Отбракован» — и сразу итог, без фото продукта и описания."""
    draft = replace(
        _draft(),
        approval=REJECTED,
        package_file_id="",
        before_file_id="",
        after_file_id="",
        description="",
    )
    assert missing_for_submit(draft) == ()


def test_photos_description_and_label_fields_are_optional() -> None:
    draft = CardDraftData(
        supplier="Поставщик «Север»",
        category="Соусы",
        name="Соус Барбекю",
        approval=APPROVED,
        label_file_id="lbl123",
    )
    assert missing_for_submit(draft) == ()


def test_empty_draft_lists_what_is_missing() -> None:
    assert missing_for_submit(CardDraftData()) == (
        "Поставщик",
        "Категория",
        "Название",
        "Фото этикетки",
        "Согласован ли продукт",
    )


def test_blank_name_is_missing() -> None:
    assert missing_for_submit(replace(_draft(), name=f"  {_ZWSP} ")) == ("Название",)


def test_unknown_approval_is_missing() -> None:
    assert missing_for_submit(replace(_draft(), approval="да")) == ("Согласован ли продукт",)


# ---------------------------------------------------------------------------
# Категории, ссылки, имена файлов
# ---------------------------------------------------------------------------
def test_default_categories_are_the_bots() -> None:
    assert DEFAULT_CATEGORIES == (
        "Мясо",
        "Птица",
        "Рыба и морепродукты",
        "Молочные продукты",
        "Сыры",
        "Овощи",
        "Фрукты",
        "Зелень",
        "Бакалея",
        "Соусы",
        "Специи",
        "Тесто и мука",
        "Напитки",
        "Прочее",
        "Десерты",
    )


def test_drive_view_url() -> None:
    assert drive_view_url("1AbC-d_9") == "https://drive.google.com/file/d/1AbC-d_9/view"


@pytest.mark.parametrize("file_id", ["", "../x", "a/b", "id?export=download", "id с пробелом"])
def test_drive_view_url_refuses_odd_ids(file_id: str) -> None:
    with pytest.raises(ValueError, match="Drive"):
        drive_view_url(file_id)


def test_photo_file_name_like_the_bot() -> None:
    """Шаблон бота; время — московское."""
    moment = datetime(2026, 9, 30, 9, 5, 7, tzinfo=UTC)
    assert (
        photo_file_name("Поставщик «Север»", "Соус  Барбекю 1,5 кг", "label", moment)
        == "Поставщик_Север_Соус_Барбекю_15_кг_этикетка_2026-09-30_12-05-07.jpg"
    )


@pytest.mark.parametrize(
    ("kind", "word"),
    [("label", "этикетка"), ("package", "упаковка"), ("before", "до"), ("after", "после")],
)
def test_photo_file_name_kinds(kind: str, word: str) -> None:
    moment = datetime(2026, 9, 30, 21, 30, 0, tzinfo=UTC)
    # 21:30 UTC — уже следующие сутки по Москве.
    assert photo_file_name("П", "И", kind, moment) == f"П_И_{word}_2026-10-01_00-30-00.jpg"


def test_photo_file_name_fallbacks_and_limits() -> None:
    moment = datetime(2026, 9, 30, 9, 0, 0, tzinfo=UTC)
    name = photo_file_name("«»", "б" * 200, "label", moment)
    assert name == f"noname_{'б' * 80}_этикетка_2026-09-30_12-00-00.jpg"


def test_photo_file_name_needs_time_zone_and_known_kind() -> None:
    with pytest.raises(ValueError, match="пояс"):
        photo_file_name("П", "И", "label", datetime(2026, 9, 30, 9, 0, 0))  # noqa: DTZ001
    with pytest.raises(ValueError, match="selfie"):
        photo_file_name("П", "И", "selfie", datetime(2026, 9, 30, 9, 0, 0, tzinfo=UTC))


# ---------------------------------------------------------------------------
# Разбор ответа модели: модель читает, код считает
# ---------------------------------------------------------------------------
def _extraction(**changes: object) -> dict[str, object]:
    extraction: dict[str, object] = {
        "label_name": " Соус барбекю ",
        "manufacturer": "Завод соусов",
        "composition": "томаты,\nсахар",
        "proteins": "1,2 г",
        "fats": "0,5",
        "carbohydrates": "30",
        "kcal": "130 ккал / 544 кДж",
        "nutrition_basis": "на 100 г",
        "shelf_life_period": None,
        "manufactured_on": "15.06.25",
        "best_before": "15 июня 2026 г.",
        "storage_conditions": "при t +2..+6°C",
        "shelf_life_defrost": None,
        "shelf_life_after": "72 часа",
        "defrost_conditions": "",
    }
    extraction.update(changes)
    return extraction


def test_label_reading_is_parsed_by_code() -> None:
    extraction = _extraction()
    assert set(extraction) == set(LABEL_EXTRACTION_KEYS)
    values, warnings = label_fields_from_extraction(extraction)
    assert tuple(values) == LABEL_FIELDS
    assert values == {
        "label_name": "Соус барбекю",
        "manufacturer": "Завод соусов",
        "composition": "томаты, сахар",
        "protein": _D("1.2"),
        "fat": _D("0.5"),
        "carbs": _D("30"),
        "kcal": _D("130"),
        "shelf_life_sealed": "12 месяцев (с 15.06.2025 до 15.06.2026) при t +2..+6°C",
        "shelf_life_defrost": "",
        "shelf_life_after": "72 часа",
        "defrost_conditions": "",
    }
    assert warnings == ()


def test_unclear_numbers_become_warnings() -> None:
    values, warnings = label_fields_from_extraction(_extraction(proteins="<0,5", fats="10-12"))
    assert values["protein"] is None
    assert values["fat"] is None
    assert values["carbs"] == _D("30")
    assert any("Белки" in w and "<0,5" in w for w in warnings)
    assert any("Жиры" in w and "10-12" in w for w in warnings)


def test_nutrient_checks_reach_warnings() -> None:
    values, warnings = label_fields_from_extraction(_extraction(proteins="120"))
    assert values["protein"] == _D("120")
    assert any("Белки" in w and "120" in w for w in warnings)


def test_portion_basis_is_not_copied_into_per_hundred() -> None:
    """На порцию — не на 100 г. Переписывать такие числа молча нельзя."""
    values, warnings = label_fields_from_extraction(_extraction(nutrition_basis="на порцию 30 г"))
    assert [values[f] for f in ("protein", "fat", "carbs", "kcal")] == [None] * 4
    assert len(warnings) == 1
    assert "на порцию 30 г" in warnings[0]


def test_portion_basis_without_numbers_is_quiet() -> None:
    values, warnings = label_fields_from_extraction(
        _extraction(
            nutrition_basis="на порцию 30 г",
            proteins=None,
            fats=None,
            carbohydrates=None,
            kcal=None,
        )
    )
    assert values["protein"] is None
    assert warnings == ()


@pytest.mark.parametrize("basis", ["100 г", "на 100 мл", "в 100 г продукта", "100g", "", None])
def test_per_hundred_basis_keeps_numbers(basis: str | None) -> None:
    values, _ = label_fields_from_extraction(_extraction(nutrition_basis=basis))
    assert values["protein"] == _D("1.2")


def test_period_on_the_label_wins() -> None:
    values, _ = label_fields_from_extraction(
        _extraction(shelf_life_period="180 суток", storage_conditions="при t -18°C")
    )
    assert values["shelf_life_sealed"] == "180 суток при t -18°C"


def test_shelf_life_warnings_reach_the_cook() -> None:
    values, warnings = label_fields_from_extraction(
        _extraction(manufactured_on="15.06.2026", best_before="15.06.2025")
    )
    assert values["shelf_life_sealed"] == ""
    assert len(warnings) == 1


def test_model_made_shelf_life_is_ignored() -> None:
    """Готовую строку срока от модели не берём: срок считает код."""
    extraction = _extraction(manufactured_on=None, best_before="15.06.2026", storage_conditions="")
    extraction["shelf_life_sealed"] = "13 месяцев (с 15.06.2025 до 15.06.2026)"
    values, _ = label_fields_from_extraction(extraction)
    assert values["shelf_life_sealed"] == "Годен до 15.06.2026"


def test_empty_reading() -> None:
    values, warnings = label_fields_from_extraction({})
    assert values == dict.fromkeys(LABEL_FIELDS, "") | dict.fromkeys(
        ("protein", "fat", "carbs", "kcal"), None
    )
    assert warnings == ()
