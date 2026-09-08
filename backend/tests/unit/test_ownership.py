"""Правила владения колонками.

Самый дорогой сценарий в проекте — молчаливая перезапись чужой работы:
цен, которые коммерческий отдел вписывал руками, или правок шефа в
справочнике. Эти данные существуют в единственном экземпляре. Поэтому
правило проверяется тестами, а не доверием к внимательности.
"""

from __future__ import annotations

import pytest

from kitchen.sync import ownership, specs
from kitchen.sync.ownership import Column, ForbiddenWriteError, Kind, Owner


# ---------------------------------------------------------------------------
# Адресация колонок
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("letter", "index"),
    [("A", 0), ("B", 1), ("T", 19), ("V", 21), ("Z", 25), ("AA", 26), ("AB", 27)],
)
def test_column_index(letter: str, index: int) -> None:
    column = Column(letter, "заголовок", "field", Kind.TEXT, Owner.APP)
    assert column.index == index


def test_specs_letters_match_positions() -> None:
    """Буква колонки обязана соответствовать её месту в описании.

    Храповик против опечатки: пропущенная буква сдвинула бы чтение, и цена
    поехала бы в поле веса — молча и с правдоподобным результатом.
    """
    for spec in specs.ALL_SPECS:
        for position, column in enumerate(spec.columns):
            assert column.index == position, (
                f"«{spec.title}»: колонка {column.letter} ({column.title}) "
                f"стоит на месте {position}, а её буква указывает на {column.index}"
            )


def test_specs_have_no_duplicate_fields() -> None:
    for spec in specs.ALL_SPECS:
        fields = [c.field for c in spec.columns]
        assert len(fields) == len(set(fields)), f"«{spec.title}»: повтор имени поля"


# ---------------------------------------------------------------------------
# Кто что может писать
# ---------------------------------------------------------------------------
def test_human_column_is_never_writable() -> None:
    """Цена продажная в расчётке — работа коммерческого отдела."""
    with pytest.raises(ForbiddenWriteError, match="принадлежит людям"):
        specs.PRICING_NEW.check_writable("price_sale")


def test_app_columns_are_writable_now() -> None:
    """Вычисляемые колонки расчётки — наши, их пишем свободно."""
    specs.PRICING_NEW.check_writable("uc_rub", "margin_percent", "kcal")
    assert "price_sale" not in {c.field for c in specs.PRICING_NEW.writable()}


def test_shared_columns_blocked_while_bots_alive() -> None:
    """Справочник закрыт, пока в него пишут боты."""
    with pytest.raises(ForbiddenWriteError, match="Telegram-бот"):
        specs.INGREDIENTS.check_writable("price_per_kg")


def test_ingredient_cards_are_blocked_too() -> None:
    """Карточки ингредиентов тоже закрыты — в них пишет pizza_bot.

    Их легко счесть безопасными: kitchen_bot эту таблицу не трогает. Но
    писатель там всё равно есть, и критерий именно «пишет ли живой бот»,
    а не «это справочник шефа».
    """
    assert specs.INGREDIENT_CARDS.writable() == ()
    with pytest.raises(ForbiddenWriteError):
        specs.INGREDIENT_CARDS.check_writable("supplier")


def test_retiring_bots_unblocks_shared_but_not_human(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вывод ботов открывает справочник — и НЕ открывает человеческие колонки.

    Это главный тест файла. Когда в фазе 5 кто-то снимет BOTS_ALIVE, он не
    должен заодно получить право затирать цены коммерсов и цену меню,
    которую ставит шеф. Владение человека не зависит от судьбы ботов.
    """
    monkeypatch.setattr(ownership, "BOTS_ALIVE", False)

    # Справочник открылся.
    specs.INGREDIENTS.check_writable("price_per_kg", "status")
    specs.INGREDIENT_CARDS.check_writable("supplier")

    # А человеческое — нет.
    with pytest.raises(ForbiddenWriteError, match="принадлежит людям"):
        specs.PRICING_NEW.check_writable("price_sale")
    with pytest.raises(ForbiddenWriteError, match="принадлежит людям"):
        specs.DISHES.check_writable("price_menu")
    with pytest.raises(ForbiddenWriteError, match="принадлежит людям"):
        specs.INGREDIENT_CARDS.check_writable("declaration")


def test_unknown_field_is_loud() -> None:
    """Опечатка в имени поля должна падать, а не молча ничего не проверить."""
    with pytest.raises(KeyError):
        specs.INGREDIENTS.check_writable("цена_за_кг")


# ---------------------------------------------------------------------------
# Раскладка листов
# ---------------------------------------------------------------------------
def test_pricing_data_starts_at_third_row() -> None:
    """Строка 1 — подсказки коммерсам, строка 2 — шапка."""
    assert specs.PRICING_NEW.header_rows == 2
    assert specs.PRICING_NEW.first_data_row == 3


def test_cooking_methods_remember_old_sheet_name() -> None:
    """Лист переименовывали; загрузчик обязан пережить это."""
    assert "Впитывание масла" in specs.COOKING_METHODS.fallback_titles


def test_dish_price_menu_belongs_to_chef() -> None:
    """Цену меню ставит шеф, и он главнее любого расчёта."""
    assert specs.DISHES.column("price_menu").owner is Owner.HUMAN
