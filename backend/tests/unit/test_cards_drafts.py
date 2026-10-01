"""Черновик карточки без базы: проверка фото, правки повара, замечания, подсказки.

Ручки целиком — на настоящей базе в ``tests/integration/test_cards_drafts_api.py``.
Здесь — правила, которые от базы не зависят: что считается JPEG, как
разбирается правка повара, как живут замечания к КБЖУ, в каком порядке
предлагаются категории и что такое «похожее название».
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

import pytest

from kitchen.cards.drafts import (
    NUTRIENT_INPUT_LIMIT,
    PHOTO_LIMIT,
    PHOTO_SLOTS,
    PROXY_LIMIT,
    STEPS,
    CardName,
    CardsError,
    DraftFieldError,
    NotJpegError,
    PhotoTooLargeError,
    ReferenceName,
    check_jpeg,
    match_names,
    ranked,
    renew_nutrient_warnings,
    submit_request_key,
    validate_changes,
)
from kitchen.domain.cards import (
    DEFAULT_CATEGORIES,
    PHOTO_KINDS,
    TEXT_LIMITS,
    check_nutrients,
)
from kitchen.web.cards import ERROR_STATUS
from tests.unit.test_web_auth import make_client

_D = Decimal
JPEG = b"\xff\xd8\xff\xe0" + bytes(64) + b"\xff\xd9"


# ---------------------------------------------------------------------------
# Фото
# ---------------------------------------------------------------------------
def test_jpeg_passes() -> None:
    check_jpeg(JPEG)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\x89PNG\r\n\x1a\n" + bytes(64),
        b"GIF89a" + bytes(64) + b"\xff\xd9",
        # Начало JPEG, а конца нет: файл оборвался по дороге.
        b"\xff\xd8\xff\xe0" + bytes(64),
        # Разметка страницы, переименованная в .jpg.
        b"<html><script>alert(1)</script></html>",
    ],
    ids=["empty", "png", "gif", "jpeg-without-ffd9", "html"],
)
def test_not_jpeg_is_refused(content: bytes) -> None:
    with pytest.raises(NotJpegError, match="JPEG"):
        check_jpeg(content)


def test_photo_over_limit_is_refused_before_the_signature() -> None:
    with pytest.raises(PhotoTooLargeError, match="8 МБ"):
        check_jpeg(b"\xff\xd8\xff" + bytes(PHOTO_LIMIT) + b"\xff\xd9")


def test_photo_exactly_at_limit_passes() -> None:
    check_jpeg(b"\xff\xd8\xff" + bytes(PHOTO_LIMIT - 5) + b"\xff\xd9")


def test_proxy_limit_is_above_upload_limit() -> None:
    """Прокси отдаёт всё, что мы могли загрузить сами; заметно больше — подмена."""
    assert PHOTO_LIMIT < PROXY_LIMIT <= 2 * PHOTO_LIMIT


def test_photo_slots_are_the_four_kinds() -> None:
    assert set(PHOTO_SLOTS) == set(PHOTO_KINDS)


def test_submit_key_is_per_draft() -> None:
    draft = uuid.UUID("22222222-2222-2222-2222-222222222222")
    assert submit_request_key(draft) == f"card-draft:{draft}"


def test_every_refusal_has_a_status_code() -> None:
    """Новый отказ без кода ответа ушёл бы повару ошибкой 500."""
    assert set(CardsError.__subclasses__()) == set(ERROR_STATUS)


def test_cors_allows_photo_upload() -> None:
    """Фото загружаются PUT: при разработке с другого адреса предварительный
    запрос CORS должен его разрешать."""
    reply = make_client().options(
        "/api/cards/drafts/x/photos/label",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "x-kitchen-csrf",
        },
    )

    assert reply.status_code == 200
    assert "PUT" in reply.headers["access-control-allow-methods"]


# ---------------------------------------------------------------------------
# Правка повара
# ---------------------------------------------------------------------------
def test_wizard_has_nine_steps() -> None:
    """«Шаг N из 9»: поставщик, категория, название, этикетка, проверка,
    согласование, фото, описание, итог."""
    assert len(STEPS) == 9
    assert STEPS[0] == "supplier"
    assert STEPS[-1] == "summary"


def test_text_is_cleaned() -> None:
    assert validate_changes({"name": "  Соус​  Барбекю "}) == {"name": "Соус Барбекю"}


def test_empty_text_means_empty_field() -> None:
    assert validate_changes({"supplier": None, "name": ""}) == {"supplier": "", "name": ""}


@pytest.mark.parametrize("field", sorted(TEXT_LIMITS))
def test_text_over_limit_is_refused_not_cut(field: str) -> None:
    """Молча обрезанный текст — потерянная работа повара (ревью задачи 1)."""
    limit = TEXT_LIMITS[field]
    assert validate_changes({field: "а" * limit}) == {field: "а" * limit}
    with pytest.raises(DraftFieldError) as caught:
        validate_changes({field: "а" * (limit + 1)})
    assert caught.value.field == field
    assert str(caught.value).endswith(f": не больше {limit} знаков")


def test_text_limit_counts_cleaned_text() -> None:
    """Лишние пробелы чистка уберёт — они в предел не идут."""
    limit = TEXT_LIMITS["name"]
    assert validate_changes({"name": "а" * limit + "   "}) == {"name": "а" * limit}


def test_absurdly_long_text_is_refused_without_cleaning() -> None:
    """25 МБ пробелов чистка разбирала бы секундами — отказ сразу."""
    started = time.monotonic()
    with pytest.raises(DraftFieldError, match="Описание: не больше"):
        validate_changes({"description": " " * 5_000_000})
    assert time.monotonic() - started < 0.5


def test_nutrient_with_comma_becomes_decimal() -> None:
    assert validate_changes({"protein": "12,5"}) == {"protein": _D("12.5")}


@pytest.mark.parametrize("raw", [None, "", "н/д"])
def test_nutrient_without_data_is_none(raw: str | None) -> None:
    assert validate_changes({"kcal": raw}) == {"kcal": None}


def test_unclear_nutrient_names_the_field() -> None:
    with pytest.raises(DraftFieldError) as caught:
        validate_changes({"protein": "abc"})
    assert caught.value.field == "protein"
    assert str(caught.value) == "Белки: «abc» — не число"


def test_long_nutrient_is_refused_before_parsing() -> None:
    """Разбор ккал на длинной строке растёт квадратично: 100 000 знаков он
    разбирал бы часами. Предел проверяется до разбора."""
    raw = "ккал " + "1" * 100_000
    started = time.monotonic()
    with pytest.raises(DraftFieldError) as caught:
        validate_changes({"kcal": raw})
    assert time.monotonic() - started < 0.5
    assert caught.value.field == "kcal"
    assert str(caught.value) == f"Ккал: не больше {NUTRIENT_INPUT_LIMIT} знаков"


def test_nutrient_at_input_limit_is_parsed() -> None:
    raw = "12,5" + " " * (NUTRIENT_INPUT_LIMIT - 4)
    assert validate_changes({"fat": raw}) == {"fat": _D("12.5")}


@pytest.mark.parametrize("value", ["Да", "Отбракован", None])
def test_approval_values(value: str | None) -> None:
    assert validate_changes({"approval": value}) == {"approval": value}


def test_unknown_approval_is_refused() -> None:
    with pytest.raises(DraftFieldError) as caught:
        validate_changes({"approval": "Может быть"})
    assert caught.value.field == "approval"


@pytest.mark.parametrize("step", STEPS)
def test_known_steps_pass(step: str) -> None:
    assert validate_changes({"step": step}) == {"step": step}


@pytest.mark.parametrize("step", ["teleport", "", None])
def test_unknown_step_is_refused(step: str | None) -> None:
    with pytest.raises(DraftFieldError) as caught:
        validate_changes({"step": step})
    assert caught.value.field == "step"


@pytest.mark.parametrize(
    "field", ["label_file_id", "package_file_id", "before_file_id", "after_file_id", "status"]
)
def test_file_ids_and_status_are_not_editable(field: str) -> None:
    """id файла приходит только из нашей загрузки: с охватом ``drive``
    корзина и скачивание дотянулись бы до любого файла аккаунта."""
    with pytest.raises(DraftFieldError) as caught:
        validate_changes({field: "someone-elses-file"})
    assert caught.value.field == field


def test_first_error_wins_and_nothing_is_returned() -> None:
    with pytest.raises(DraftFieldError) as caught:
        validate_changes({"name": "Соус", "protein": "abc", "fat": "xyz"})
    assert caught.value.field == "protein"


# ---------------------------------------------------------------------------
# Замечания к КБЖУ
# ---------------------------------------------------------------------------
_LABEL_NOTE = "Жиры: «<0,5» — не точное число, впишите значение сами"
_NONE4: tuple[Decimal | None, ...] = (None, None, None, None)


def _check(*values: Decimal | None) -> list[str]:
    protein, fat, carbs, kcal = values
    return list(check_nutrients(protein, fat, carbs, kcal))


def test_plausibility_follows_the_numbers() -> None:
    wrong = (_D("120"), None, None, None)
    notes = renew_nutrient_warnings([], _NONE4, wrong)
    assert notes == _check(*wrong)
    assert renew_nutrient_warnings(notes, wrong, (_D("12"), None, None, None)) == []


def test_label_notes_stay_when_numbers_change() -> None:
    recognized = (_D("120"), None, _D("10"), _D("300"))
    notes = [_LABEL_NOTE, *_check(*recognized)]

    fixed = (_D("12"), None, _D("10"), _D("300"))
    renewed = renew_nutrient_warnings(notes, recognized, fixed)

    assert renewed == [_LABEL_NOTE, *_check(*fixed)]
    assert renewed.count(_LABEL_NOTE) == 1


def test_same_numbers_do_not_duplicate_notes() -> None:
    numbers = (_D("120"), None, None, None)
    notes = [_LABEL_NOTE, *_check(*numbers)]
    assert renew_nutrient_warnings(notes, numbers, numbers) == notes


# ---------------------------------------------------------------------------
# Категории и поставщики
# ---------------------------------------------------------------------------
def test_categories_by_frequency_then_seed_order() -> None:
    cards = ["Соусы", "Морепродукты", "соусы ", "Морепродукты", "Соусы", "Мясо", "сыры", ""]

    result = ranked(cards, DEFAULT_CATEGORIES)

    assert result[:4] == ["Соусы", "Морепродукты", "Мясо", "Сыры"]
    rest = [c for c in DEFAULT_CATEGORIES if c not in {"Соусы", "Мясо", "Сыры"}]
    assert result[4:] == rest
    assert len(result) == len(set(result)) == len(DEFAULT_CATEGORIES) + 1


def test_new_spelling_shows_the_commonest_variant() -> None:
    assert ranked(["метро", "Метро", "Метро", "Ашан"]) == ["Метро", "Ашан"]


def test_equal_counts_without_seed_go_by_name() -> None:
    assert ranked(["Ашан", "Вкусвилл", "Азбука"]) == ["Азбука", "Ашан", "Вкусвилл"]


# ---------------------------------------------------------------------------
# Похожие названия
# ---------------------------------------------------------------------------
_CARDS = (
    CardName(name="Соус Барбекю", supplier="Метро", hidden=False),
    CardName(name="Соус Сырный", supplier="Ашан", hidden=False),
    CardName(name="Сыр Моцарелла", supplier="Метро", hidden=True),
)
_REFERENCE = (
    ReferenceName(key="1", name="Соус барбекю", status="активное"),
    ReferenceName(key="2", name="Сыр Моцарелла", status=""),
    ReferenceName(key="3", name="Кетчуп", status="архив"),
)


def test_exact_card_is_a_duplicate() -> None:
    found = match_names("соус  барбекю", _CARDS, _REFERENCE)

    assert [(c.name, c.supplier) for c in found.cards.exact] == [("Соус Барбекю", "Метро")]
    assert found.cards.similar == ()
    assert found.hidden.exact == found.hidden.similar == ()
    assert found.reference.exact == ("Соус барбекю",)


def test_typo_finds_similar() -> None:
    found = match_names("Соус барбекью", _CARDS, _REFERENCE)

    assert found.cards.exact == ()
    assert [c.name for c in found.cards.similar] == ["Соус Барбекю"]
    assert found.reference.similar == ("Соус барбекю",)


def test_hidden_card_is_told_apart() -> None:
    """Карточку убрали из таблицы: имя свободно, но повар должен знать —
    новая строка с этим именем вернёт её на сайт со старыми связями."""
    exact = match_names("Сыр Моцарелла", _CARDS, _REFERENCE)
    similar = match_names("Сыр моцарела", _CARDS, _REFERENCE)

    assert [c.name for c in exact.hidden.exact] == ["Сыр Моцарелла"]
    assert exact.cards.exact == exact.cards.similar == ()
    assert exact.reference.exact == ("Сыр Моцарелла",)
    assert [c.name for c in similar.hidden.similar] == ["Сыр Моцарелла"]


def test_archived_reference_is_not_offered() -> None:
    """Взяв имя из справочника, карточка склеится сама — с архивной не склеится."""
    found = match_names("Кетчуп", _CARDS, _REFERENCE)

    assert found.reference.exact == found.reference.similar == ()


def test_empty_name_finds_nothing() -> None:
    found = match_names("  ", _CARDS, _REFERENCE)

    assert found.cards.exact == found.cards.similar == ()
    assert found.hidden.exact == found.hidden.similar == ()
    assert found.reference.exact == found.reference.similar == ()
