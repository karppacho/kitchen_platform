"""Сопоставление карточек со справочником.

Главный риск здесь — не «не нашли пару», а «нашли не ту». Подставленный
не тот ингредиент даёт себестоимость по чужой цене, и она выглядит
правдоподобно. Поэтому тесты в первую очередь проверяют, что код
отказывается решать там, где решать нельзя.
"""

from __future__ import annotations

import pytest

from kitchen.domain.matching import Entry, NameIndex, normalise_name


def _index(*entries: tuple[str, str, str]) -> NameIndex:
    return NameIndex(Entry(key=k, name=n, status=s) for k, n, s in entries)


# ---------------------------------------------------------------------------
# Нормализация
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Томаты", "томаты"),
        ("Огурцы  маринованные", "Огурцы маринованные"),
        (" Лук репчатый ", "лук репчатый"),
        # ё пишут по-разному, и это никогда не различает продукты.
        ("Сгущённое молоко", "Сгущенное молоко"),
        ("Соус сырный, острый", "Соус сырный острый"),
        ("Масло (подсолнечное)", "Масло подсолнечное"),
    ],
)
def test_normalisation_unifies_human_variation(left: str, right: str) -> None:
    assert normalise_name(left) == normalise_name(right)


def test_normalisation_keeps_word_order() -> None:
    """«Соус сырный» и «Сырный соус» — почти наверняка одно.

    Но доказать это мы не можем, а ошибка стоит неверной себестоимости.
    Пусть решает шеф.
    """
    assert normalise_name("Соус сырный") != normalise_name("Сырный соус")


def test_normalisation_keeps_meaningful_difference() -> None:
    assert normalise_name("Огурцы резаные") != normalise_name("Огурцы не резаные")


# ---------------------------------------------------------------------------
# Однозначность
# ---------------------------------------------------------------------------
def test_single_exact_match_resolves() -> None:
    index = _index(("1", "Томаты", "активное"), ("2", "Лук репчатый", "активное"))
    match = index.match("томаты")

    assert match.resolved is not None
    assert match.resolved.key == "1"
    assert not match.ambiguous


def test_two_active_namesakes_are_ambiguous() -> None:
    """Точное совпадение решает, только если оно одно.

    Два активных «сахара» — по 100 ₽/кг и по 0 ₽/шт — различаются ценой в
    сто раз. Выбор первого попавшегося из словаря даст себестоимость,
    посчитанную не по тому продукту, молча.
    """
    index = _index(("12", "Сахар", "активное"), ("123", "Сахар", "активное"))
    match = index.match("Сахар")

    assert match.ambiguous
    assert match.resolved is None, "код обязан отказаться выбирать"
    assert {e.key for e in match.exact} == {"12", "123"}


def test_archived_namesake_does_not_create_ambiguity() -> None:
    """Пара «архивная + активная» неоднозначности не даёт."""
    index = _index(
        ("40", "Огурцы маринованные", "архив"), ("41", "Огурцы маринованные", "активное")
    )
    match = index.match("Огурцы маринованные")

    assert not match.ambiguous
    assert match.resolved is not None
    assert match.resolved.key == "41"


def test_archived_entries_stay_reachable_by_key() -> None:
    """Архив не предлагается в новое, но продолжает считаться в старом.

    Полтора десятка живых блюд ссылаются на архивные позиции, и их
    себестоимость обязана считаться по-прежнему.
    """
    index = _index(("40", "Огурцы маринованные", "архив"))

    assert index.match("Огурцы маринованные").resolved is None, "в подбор не попадает"
    assert index.get("40") is not None, "по ключу доступен"
    assert index.get("40").name == "Огурцы маринованные"


# ---------------------------------------------------------------------------
# Архивные тёзки — для пары карточки, не для новых блюд
# ---------------------------------------------------------------------------
def test_archived_namesake_is_seen_apart_from_the_dish_choice() -> None:
    """Живые данные 02.10: у карточки «Корж для римской пиццы» точный тёзка —
    в архиве, а похожая активная «Основа для римской пиццы» — другой продукт.

    Архивный тёзка виден в своём поле — по нему импорт свяжет карточку. А
    подбор для блюд — как раньше: архивное в новое блюдо не предлагается ни
    ответом, ни похожим.
    """
    index = _index(
        ("80", "Корж для римской пиццы", "архив"),
        ("81", "Основа для римской пиццы", "активное"),
    )
    match = index.match("корж для римской пиццы")

    assert [e.key for e in match.archived_exact] == ["80"]
    assert match.exact == ()
    assert match.resolved is None, "архивное в новые блюда не предлагается"
    assert not match.ambiguous
    assert [c.entry.key for c in match.similar] == ["81"], "похожие — только активные"
    assert not match.orphan


def test_archived_namesake_alone_leaves_the_dish_choice_empty() -> None:
    """Четыре карточки 02.10 «без пары»: точный тёзка в архиве, похожих
    активных нет. Для новых блюд пары по-прежнему нет."""
    index = _index(("40", "Огурцы маринованные, не резанные", "архив"), ("1", "Томаты", "активное"))
    match = index.match("Огурцы маринованные, не резанные")

    assert [e.key for e in match.archived_exact] == ["40"]
    assert match.similar == ()
    assert match.orphan
    assert match.resolved is None


def test_close_archived_name_is_not_offered() -> None:
    """Архивное — только точный тёзка: похожее архивное не предлагается
    никуда, ни в пару карточке, ни в блюдо."""
    index = _index(("40", "Огурцы маринованные, не резанные", "архив"))
    match = index.match("Огурцы маринованные не резаные")

    assert match.archived_exact == ()
    assert match.similar == ()
    assert match.orphan


def test_active_namesake_leaves_archived_field_empty() -> None:
    """Активный тёзка важнее архивного: поле архивных пусто, ответ — активный."""
    index = _index(
        ("40", "Огурцы маринованные", "архив"), ("41", "Огурцы маринованные", "активное")
    )
    match = index.match("Огурцы маринованные")

    assert match.archived_exact == ()
    assert match.resolved is not None
    assert match.resolved.key == "41"


def test_two_archived_namesakes_are_both_seen_but_not_a_duplicate() -> None:
    """Два архивных тёзки — оба в поле: выбирать человеку. Дефектом данных
    («активные тёзки») это не считается, и для блюд неоднозначности нет —
    архивное туда не предлагается."""
    index = _index(("50", "Соус горчичный", "архив"), ("51", "Соус горчичный", "архив"))
    match = index.match("Соус горчичный")

    assert [e.key for e in match.archived_exact] == ["50", "51"]
    assert match.exact == ()
    assert not match.ambiguous
    assert match.resolved is None
    assert index.duplicates() == {}


# ---------------------------------------------------------------------------
# Похожие
# ---------------------------------------------------------------------------
def test_close_name_becomes_candidate_not_answer() -> None:
    index = _index(("40", "Огурцы маринованные, не резанные", "активное"))
    match = index.match("Огурцы маринованные не резаные")

    assert match.resolved is None, "похожее — не ответ, а предложение"
    assert match.similar
    assert match.similar[0].entry.key == "40"


def test_different_products_are_not_offered() -> None:
    """«Соус сырный» и «Соус чесночный» — разные продукты."""
    index = _index(("1", "Соус чесночный", "активное"))
    assert index.match("Соус сырный").similar == ()


def test_missing_name_is_orphan() -> None:
    index = _index(("1", "Томаты", "активное"))
    match = index.match("Пастрами из индейки")

    assert match.orphan
    assert match.resolved is None


def test_candidate_order_is_stable() -> None:
    """Отчёт, меняющийся между прогонами, невозможно сверять."""
    index = _index(
        ("1", "Сыр моцарелла", "активное"),
        ("2", "Сыр моцарелла для пиццы", "активное"),
        ("3", "Сыр моцарелла в шариках", "активное"),
    )
    first = [c.entry.key for c in index.match("Сыр моцарелла блок").similar]
    second = [c.entry.key for c in index.match("Сыр моцарелла блок").similar]

    assert first == second


# ---------------------------------------------------------------------------
# Дефекты данных
# ---------------------------------------------------------------------------
def test_duplicates_are_listed_not_raised() -> None:
    """Активные тёзки — известный дефект, а не повод падать.

    Список нужен, чтобы за ним следил храповик: он не должен расти.
    """
    index = _index(
        ("12", "Сахар", "активное"),
        ("123", "Сахар", "активное"),
        ("5", "Томаты", "активное"),
    )
    duplicates = index.duplicates()

    assert set(duplicates) == {"сахар"}
    assert len(duplicates["сахар"]) == 2
