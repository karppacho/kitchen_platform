"""Расчёт себестоимости.

Числа здесь выведены руками, а не получены из того же кода, который
проверяется: иначе тест подтверждал бы сам себя и проходил при любой
ошибке. Каждый случай называет поломку, которую ловит.

Приёмка переноса — сверка со 130 блюдами эталона (`scripts/verify_golden.py`).
Эти тесты стерегут правила по отдельности: когда сверка покажет
расхождение, они скажут, какое именно правило сломалось.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kitchen.domain.costs import calculate, kbju_coverage_status, money_text
from kitchen.domain.recipe import (
    ROW_MAIN,
    ROW_PACKAGING,
    Component,
    IngredientSpec,
    PackagingSpec,
    Recipe,
)


def ing(**overrides: object) -> IngredientSpec:
    defaults: dict[str, object] = {
        "key": "1",
        "name": "Томаты",
        "unit": "кг",
        "price_per_unit": Decimal("177"),
    }
    defaults.update(overrides)
    return IngredientSpec(**defaults)  # type: ignore[arg-type]


def dish(*components: Component, price: str | None = "280") -> Recipe:
    return Recipe(
        key="T001",
        name="Тестовое",
        price_menu=Decimal(price) if price is not None else None,
        components=components,
    )


def main(ingredient: IngredientSpec, grams: str) -> Component:
    return Component(row_type=ROW_MAIN, net_weight_g=Decimal(grams), ingredient=ingredient)


# ---------------------------------------------------------------------------
# Потери: главное правило расчёта
# ---------------------------------------------------------------------------
def test_losses_are_added_on_top_of_net() -> None:
    """Шеф пишет НЕТТО, потери накидываются сверху.

    Разбор из записки бота: томаты с нарезкой 11.16%, в ТТК 100 г.
    Брутто = 100 / (1 − 0.1116) = 112.5619… г
    Стоимость = 112.5619 / 1000 × 177 = 19.9234… → 19.92 ₽

    Если потери начнут вычитать вместо накидывания, множитель уйдёт не в
    ту сторону, и неверная стоимость составит (1−L)² от верной. Здесь это
    15.72 ₽ вместо 19.92 — падение на 21%; у айсберга с нарезкой 27.91%
    падение уже 48%.

    Опасно не падение, а его незаметность: 15.72 ₽ за сто граммов томатов
    при цене 177 ₽/кг выглядит совершенно нормально. Ошибка не проявится
    ни исключением, ни абсурдной цифрой — только деньгами.
    """
    tomato = ing(losses_cutting=Decimal("0.1116"))
    result = calculate(dish(main(tomato, "100")))

    assert result.uc_rub == Decimal("19.92")
    assert result.components[0].gross_weight_g == Decimal("112.56")


def test_output_uses_net_not_gross() -> None:
    """В выход блюда идёт нетто, в деньги — брутто.

    Перепутать легко, и заявленный вес порции станет больше настоящего.
    """
    tomato = ing(losses_cutting=Decimal("0.1116"))
    result = calculate(dish(main(tomato, "100")))

    assert result.output_grams == Decimal("100")
    assert result.components[0].gross_weight_g > Decimal("100")


def test_both_losses_multiply_not_add() -> None:
    """Потери перемножаются: (1−0.2)×(1−0.5) = 0.4, а не 1−0.7 = 0.3.

    Сложение дало бы брутто 333.33 вместо 250 — завышение на треть.
    """
    item = ing(
        price_per_unit=Decimal("1000"),
        losses_unpacking=Decimal("0.2"),
        losses_cutting=Decimal("0.5"),
    )
    result = calculate(dish(main(item, "100")))

    assert result.components[0].gross_weight_g == Decimal("250.00")
    assert result.uc_rub == Decimal("250.00")


def test_total_losses_do_not_divide_by_zero() -> None:
    """Потери 100% — вырожденный случай, а не падение."""
    item = ing(losses_cutting=Decimal("1"))
    result = calculate(dish(main(item, "100")))

    assert result.components[0].gross_weight_g == Decimal("100.00")
    assert result.uc_rub == Decimal("0.00")


def test_thermal_losses_are_read_but_not_applied() -> None:
    """Тепловые потери в расчёте НЕ участвуют.

    Осознанный пробел, унаследованный от бота. Тест стережёт его: если
    кто-то «доделает» их из лучших побуждений, все 130 блюд разойдутся с
    эталоном, и причина будет неочевидна.
    """
    without = calculate(dish(main(ing(), "100")))
    with_thermal = calculate(dish(main(ing(losses_thermal=Decimal("0.3")), "100")))

    assert without.uc_rub == with_thermal.uc_rub


# ---------------------------------------------------------------------------
# Единицы измерения
# ---------------------------------------------------------------------------
def test_weight_ingredient_divides_by_thousand() -> None:
    """Цена за кг, вес в граммах: 100 г × 177 ₽/кг = 17.70 ₽."""
    assert calculate(dish(main(ing(), "100"))).uc_rub == Decimal("17.70")


def test_piece_ingredient_converts_grams_to_pieces() -> None:
    """В ТТК граммы, цена за штуку — переводит «Вес 1 шт».

    140 г при весе штуки 70 г = 2 шт × 14 ₽ = 28 ₽. Без перевода вышло бы
    140 × 14 = 1960 ₽, то есть в семьдесят раз больше.
    """
    tortilla = ing(
        name="Тортилья",
        unit="шт",
        price_per_unit=Decimal("14"),
        weight_per_piece_g=Decimal("70"),
    )
    assert calculate(dish(main(tortilla, "140"))).uc_rub == Decimal("28.00")


def test_unknown_unit_costs_nothing() -> None:
    """Незнакомая единица не должна давать выдуманное число."""
    weird = ing(unit="ящик", price_per_unit=Decimal("500"))
    assert calculate(dish(main(weird, "100"))).uc_rub == Decimal("0.00")


# ---------------------------------------------------------------------------
# Незаполненные данные никогда не молчат
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("price", "expected_fragment"),
    [(None, "не заполнена цена"), (Decimal("0"), "не заполнена цена")],
)
def test_missing_price_warns_and_costs_zero(price: Decimal | None, expected_fragment: str) -> None:
    """Пусто и ровно ноль означают одно: данных нет.

    Ноль особенно коварен — без предупреждения ингредиент молча выпал бы
    из себестоимости, и шеф увидел бы заниженный UC без единого замечания.
    """
    result = calculate(dish(main(ing(price_per_unit=price), "100")))

    assert result.uc_rub == Decimal("0.00")
    assert any(expected_fragment in w for w in result.warnings)


def test_ingredient_without_price_stays_in_recipe() -> None:
    """Ингредиент остаётся в составе даже с нулевой стоимостью.

    Его вес входит в выход, КБЖУ и рецептуру. Выбросить его значило бы
    отдать неполную технологическую карту и заниженный выход.
    """
    result = calculate(dish(main(ing(price_per_unit=None), "100")))

    assert len(result.components) == 1
    assert result.output_grams == Decimal("100")


def test_piece_without_weight_warns() -> None:
    """Штучный без «Вес 1 шт» посчитать нельзя — и молчать об этом нельзя."""
    broken = ing(unit="шт", price_per_unit=Decimal("14"), weight_per_piece_g=None)
    result = calculate(dish(main(broken, "140")))

    assert result.uc_rub == Decimal("0.00")
    assert any("вес 1 шт" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Штучные: в ТТК бывает вписано число штук, а не граммы
# ---------------------------------------------------------------------------
# Решение Александра 03.10.2026 заменило решение 23.09. Тогда расчёт читал
# любое значение как граммы и только предупреждал «похоже, вписано
# количество штук», а лист должен был править шеф; тесты того решения
# закрепляли 0,83 ₽ у круассана B001 и замечание вместо пересчёта. Они
# заменены здесь осознанно: значение меньше 10 % веса штуки — это штуки, и
# пересчитывает их в граммы платформа.
PIECES_NOTE = "посчитано штуками"
ZERO_NET = "в ТТК нетто 0"


def croissant(**overrides: object) -> IngredientSpec:
    """Круассан из B001: штучный, 49,50 ₽ за штуку весом 60 г."""
    defaults: dict[str, object] = {
        "name": "Круассан сливочный 60гр",
        "unit": "шт",
        "price_per_unit": Decimal("49.50"),
        "weight_per_piece_g": Decimal("60"),
    }
    defaults.update(overrides)
    return ing(**defaults)


@pytest.mark.parametrize(
    ("value", "cost", "net", "pieces"),
    [
        ("1", "49.50", "60", "1"),
        ("0.5", "24.75", "30", "0.5"),
        ("5.9", "292.05", "354", "5.9"),
        ("6", "4.95", "6", None),
        ("30", "24.75", "30", None),
    ],
)
def test_piece_value_below_tenth_of_piece_weight_is_a_count(
    value: str, cost: str, net: str, pieces: str | None
) -> None:
    """Меньше 10 % веса штуки — число штук: нетто = штуки × вес штуки.

    B001: в ТТК «1» у круассана весом 60 г. Прочитанная граммами, единица
    давала 1/60 штуки — 0,83 ₽ вместо 49,50 ₽. Полштуки шеф вправе записать
    как «0.5»: 30 г, 24,75 ₽. 5,9 и 6 держат порог с двух сторон: 5,9 шт —
    это 354 г и 292,05 ₽, а ровно 10 % (6 из 60) — уже граммы, правило —
    «меньше 10 %»: 6 г = 0,1 штуки = 4,95 ₽. 30 г из 60 — честные полштуки
    граммами, как и раньше. Справка ставится только там, где был пересчёт.
    """
    result = calculate(dish(main(croissant(), value)))
    [row] = result.components

    assert result.uc_rub == Decimal(cost)
    assert row.net_weight_g == Decimal(net)
    assert row.pieces == (Decimal(pieces) if pieces is not None else None)
    assert any(PIECES_NOTE in w for w in result.warnings) is (pieces is not None)


def test_piece_note_writes_numbers_as_chef_does() -> None:
    """Справка называет позицию, число из ТТК и вес штуки — без хвостовых нулей.

    База хранит веса как Numeric(12,3) и отдаёт «1.000» и «60.000». «В ТТК
    1.000» шеф прочитал бы как тысячу — ровно обратное тому, о чём справка.
    """
    item = croissant(weight_per_piece_g=Decimal("60.000"))
    result = calculate(dish(main(item, "1.000")))

    assert (
        "Штучный «Круассан сливочный 60гр»: в ТТК 1 — посчитано штуками: 1 шт = 60 г"
        in result.warnings
    )


def test_piece_zero_net_warns_and_costs_zero() -> None:
    """Нетто 0 — ни штук, ни граммов: строка в себестоимость не попала.

    B114 и B115 («Котлета по киевски»): в ТТК 0 при цене 133,33 ₽ за штуку.
    Пересчитывать нечего, а молчать нельзя — шеф должен вписать штуки или
    граммы. Строка при этом остаётся в составе, как любая нулевая.
    """
    result = calculate(dish(main(croissant(), "0")))

    assert result.uc_rub == Decimal("0.00")
    assert (
        "Штучный «Круассан сливочный 60гр»: в ТТК нетто 0 — в себестоимости не учтён, "
        "впишите штуки или граммы" in result.warnings
    )
    assert not any(PIECES_NOTE in w for w in result.warnings)
    assert len(result.components) == 1


def test_piece_count_gets_losses_on_top() -> None:
    """Штуки переводятся в нетто, потери накидываются сверху — как у граммов.

    1 шт = 60 г нетто; при потерях 10 % брутто 60 / 0,9 = 66,67 г, это
    1,111… штуки × 49,50 ₽ = 55,00 ₽. Мимо потерь (штуки × цена) вышло бы
    49,50 ₽ — недосчёт ровно на потери.
    """
    result = calculate(dish(main(croissant(losses_cutting=Decimal("0.1")), "1")))
    [row] = result.components

    assert row.gross_weight_g == Decimal("66.67")
    assert result.uc_rub == Decimal("55.00")


def test_piece_count_goes_to_output_and_kbju_as_grams() -> None:
    """Выход блюда и КБЖУ — по граммам, а не по числу из ТТК.

    Круассан 400 ккал на 100 г: 1 шт = 60 г = 240 ккал; белки 8 × 0,6 = 4,8,
    жиры 20 × 0,6 = 12, углеводы 45 × 0,6 = 27. По сырому значению выход
    вышел бы 1 г, а калорий — 4.
    """
    item = croissant(
        protein_100g=Decimal("8"),
        fat_100g=Decimal("20"),
        carbs_100g=Decimal("45"),
        kcal_100g=Decimal("400"),
    )
    result = calculate(dish(main(item, "1")))

    assert result.output_grams == Decimal("60")
    assert result.protein_g == Decimal("4.8")
    assert result.fat_g == Decimal("12.0")
    assert result.carbs_g == Decimal("27.0")
    assert result.kcal == Decimal("240")
    assert result.kbju_coverage == Decimal("1")


@pytest.mark.parametrize("price", [None, Decimal("0")])
def test_piece_count_without_price_still_gives_grams(price: Decimal | None) -> None:
    """Без цены — стоимость 0 и прежнее замечание, но граммы считаются.

    От граммов зависят выход и КБЖУ, а им цена не нужна. Справка ставится и
    здесь: шеф должен видеть, что «1» прочитана как 60 г.
    """
    result = calculate(dish(main(croissant(price_per_unit=price), "1")))
    [row] = result.components

    assert result.uc_rub == Decimal("0.00")
    assert row.net_weight_g == Decimal("60")
    assert result.output_grams == Decimal("60")
    assert any("не заполнена цена" in w for w in result.warnings)
    assert any(PIECES_NOTE in w for w in result.warnings)


@pytest.mark.parametrize("piece_weight", [None, Decimal("0.000")])
def test_piece_without_piece_weight_stays_raw(piece_weight: Decimal | None) -> None:
    """Без веса штуки пересчитать нельзя — как раньше: замечание, значение сырое.

    Вес 0 — отдельный случай: база отдаёт его как «0.000», и проверка
    `is None` вместо «пусто» пропустила бы строку с нулевой стоимостью без
    единого замечания.
    """
    result = calculate(dish(main(croissant(weight_per_piece_g=piece_weight), "1")))
    [row] = result.components

    assert result.uc_rub == Decimal("0.00")
    assert any("вес 1 шт" in w for w in result.warnings)
    assert not any(PIECES_NOTE in w for w in result.warnings)
    assert row.net_weight_g == Decimal("1")
    assert row.pieces is None
    assert result.output_grams == Decimal("1")


@pytest.mark.parametrize(
    ("overrides", "old_fragment"),
    [
        ({"weight_per_piece_g": None}, "вес 1 шт"),
        ({"price_per_unit": None}, "не заполнена цена"),
    ],
)
def test_zero_net_on_zero_cost_row_keeps_only_its_old_warning(
    overrides: dict[str, object], old_fragment: str
) -> None:
    """Строка, которая и так стоит 0, получает одно замечание о стоимости — прежнее.

    Так было и при решении 23.09: второе «не учтён» к той же строке — шум,
    шефу уже сказано, чего не хватает.
    """
    result = calculate(dish(main(croissant(**overrides), "0")))

    assert any(old_fragment in w for w in result.warnings)
    assert not any(ZERO_NET in w for w in result.warnings)


def test_weight_ingredient_with_piece_weight_is_not_converted() -> None:
    """Ветку выбирает единица из справочника, а не наличие «Вес 1 шт».

    У весового ингредиента колонка «Вес 1 шт» может быть заполнена, но 1 г
    соли на порцию — это просто 1 г: 1 / 1000 × 177 ₽ = 0,177 → 0,18 ₽.
    """
    salt = ing(name="Соль", unit="кг", weight_per_piece_g=Decimal("60"))
    result = calculate(dish(main(salt, "1")))
    [row] = result.components

    assert result.uc_rub == Decimal("0.18")
    assert row.net_weight_g == Decimal("1")
    assert row.pieces is None
    assert not any(PIECES_NOTE in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Цена меню и маржа
# ---------------------------------------------------------------------------
def test_no_menu_price_gives_none_not_zero() -> None:
    """Ноль шеф прочитал бы как настоящую нулевую маржу."""
    result = calculate(dish(main(ing(), "100"), price=None))

    assert result.uc_rub == Decimal("17.70"), "себестоимость от цены продажи не зависит"
    assert result.uc_percent is None
    assert result.margin_rub is None
    assert result.margin_percent is None
    assert any("Цена меню не заполнена" in w for w in result.warnings)


def test_margin_arithmetic() -> None:
    """280 − 17.70 = 262.30; UC% = 17.70/280 = 6.3%; маржа = 93.7%."""
    result = calculate(dish(main(ing(), "100"), price="280"))

    assert result.margin_rub == Decimal("262.30")
    assert result.uc_percent == Decimal("6.3")
    assert result.margin_percent == Decimal("93.7")


def test_cost_above_price_warns() -> None:
    """Почти всегда это ошибка в данных, а не реальный убыток.

    04.08.2026 так создалось блюдо с UC 1398 ₽ при цене 280 ₽, и система
    промолчала.
    """
    result = calculate(dish(main(ing(), "10000"), price="100"))

    assert result.margin_rub < 0
    assert any("выше цены меню" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Упаковка
# ---------------------------------------------------------------------------
def test_packaging_costs_per_piece_without_losses() -> None:
    box = PackagingSpec(key="u1", name="Коробка", price_per_piece=Decimal("12.50"))
    result = calculate(
        dish(
            main(ing(), "100"),
            Component(row_type=ROW_PACKAGING, net_weight_g=Decimal("2"), packaging=box),
        )
    )

    assert result.uc_rub == Decimal("42.70"), "17.70 за томаты + 25.00 за две коробки"
    assert not any("удалена из справочника" in w for w in result.warnings)


def test_packaging_not_in_output_weight() -> None:
    """Упаковка несъедобна и в выход блюда не входит."""
    box = PackagingSpec(key="u1", name="Коробка", price_per_piece=Decimal("12.50"))
    result = calculate(
        dish(
            main(ing(), "100"),
            Component(row_type=ROW_PACKAGING, net_weight_g=Decimal("2"), packaging=box),
        )
    )

    assert result.output_grams == Decimal("100")


def test_packaging_without_price_is_dropped_entirely() -> None:
    """Строка выпадает целиком, а не добавляется с нулём.

    Так было у бота, и эталон снят с него. Отличие заметное: в карте не
    появится строка «Коробка — 0 ₽».
    """
    box = PackagingSpec(key="u1", name="Коробка", price_per_piece=None)
    result = calculate(
        dish(
            main(ing(), "100"),
            Component(row_type=ROW_PACKAGING, net_weight_g=Decimal("2"), packaging=box),
        )
    )

    assert len(result.components) == 1
    assert any("нет цены" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Удалено из справочника, а ТТК ссылается
# ---------------------------------------------------------------------------
REMOVED = "удалён из справочника"


def test_removed_ingredient_counts_by_last_known_data_and_warns() -> None:
    """Шеф удалил строку из ING, а ТТК на неё ещё ссылается.

    Решение 23.09: считать по последним известным данным — себестоимость не
    проваливается — и сказать об этом, чтобы шеф поправил ТТК.
    """
    result = calculate(dish(main(ing(removed=True), "100")))

    assert result.uc_rub == Decimal("17.70"), "100 г × 177 ₽/кг — как у неудалённого"
    assert any(f"«Томаты» {REMOVED}" in w for w in result.warnings)


def test_present_ingredient_gets_no_removed_warning() -> None:
    result = calculate(dish(main(ing(), "100")))

    assert not any(REMOVED in w for w in result.warnings)


def test_removed_ingredient_without_price_gets_both_warnings() -> None:
    """Замечание об удалении добавочное: «нет цены» к той же строке никуда не девается.

    Удалённый ингредиент без цены стоит 0 ₽, и шеф должен узнать оба факта:
    ТТК ссылается на то, чего в справочнике уже нет, а сама строка занижает
    UC. Перепиши кто-нибудь проверку удаления через `elif` или ранний
    `return` — замечание о цене пропадёт молча, а при раннем выходе и сама
    строка уйдёт из состава вместе со своим весом в выходе и КБЖУ.
    """
    result = calculate(dish(main(ing(removed=True, price_per_unit=None), "100")))

    assert any(f"«Томаты» {REMOVED}" in w for w in result.warnings)
    assert any("не заполнена цена" in w for w in result.warnings)
    assert result.uc_rub == Decimal("0.00")
    assert len(result.components) == 1, "строка остаётся в составе"


def test_removed_packaging_counts_and_warns() -> None:
    box = PackagingSpec(key="u1", name="Коробка", price_per_piece=Decimal("12.50"), removed=True)
    result = calculate(
        dish(
            main(ing(), "100"),
            Component(row_type=ROW_PACKAGING, net_weight_g=Decimal("2"), packaging=box),
        )
    )

    assert result.uc_rub == Decimal("42.70"), "17.70 за томаты + 25.00 за две коробки"
    assert any("«Коробка» удалена из справочника" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# КБЖУ
# ---------------------------------------------------------------------------
def test_kbju_scales_from_hundred_grams() -> None:
    """КБЖУ в справочнике на 100 г; в блюде — на фактический вес."""
    nutritious = ing(
        protein_100g=Decimal("10"),
        fat_100g=Decimal("20"),
        carbs_100g=Decimal("30"),
        kcal_100g=Decimal("400"),
    )
    result = calculate(dish(main(nutritious, "250")))

    assert result.protein_g == Decimal("25.0")
    assert result.fat_g == Decimal("50.0")
    assert result.carbs_g == Decimal("75.0")
    assert result.kcal == Decimal("1000")


def test_kbju_coverage_counts_weight_not_items() -> None:
    """Доля считается по ВЕСУ состава, а не по числу позиций.

    Ингредиент без КБЖУ весом 900 г при одном заполненном в 100 г — это
    покрытие 10%, а не 50%.
    """
    filled = ing(name="С КБЖУ", kcal_100g=Decimal("100"))
    empty = ing(name="Без КБЖУ", kcal_100g=None)
    result = calculate(dish(main(filled, "100"), main(empty, "900")))

    assert result.kbju_coverage == Decimal("0.1")
    assert any("цифрам нельзя доверять" in w for w in result.warnings)


@pytest.mark.parametrize(
    ("coverage", "status"),
    [("1", "complete"), ("0.999", "complete"), ("0.5", "partial"), ("0.49", "poor")],
)
def test_coverage_status_boundaries(coverage: str, status: str) -> None:
    assert kbju_coverage_status(Decimal(coverage)) == status


# ---------------------------------------------------------------------------
# Прочее
# ---------------------------------------------------------------------------
def test_empty_recipe_is_explicit() -> None:
    """Блюдо без состава — не ноль молча, а названная ситуация."""
    result = calculate(dish(price="280"))

    assert result.uc_rub == Decimal("0")
    assert result.margin_rub == Decimal("280"), "без затрат вся цена — маржа"
    assert result.margin_percent == Decimal("100")
    assert result.warnings == ["В ТТК нет ни одной строки для этого блюда"]


def test_shares_sum_to_hundred() -> None:
    """Доли в себестоимости считаются от итога."""
    result = calculate(dish(main(ing(), "100"), main(ing(name="Второй"), "300")))

    shares = [c.share_percent for c in result.components]
    assert shares == [Decimal("25.0"), Decimal("75.0")]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("99.00", "99"), ("100.00", "100"), ("1000.00", "1000"), ("99.50", "99.5"), ("0.00", "0")],
)
def test_money_text_drops_meaningless_zeros(value: str, expected: str) -> None:
    """Цена печатается так, как её пишет шеф.

    Ловушка: `normalize()` в одиночку превращает Decimal("100.00") в 1E+2,
    и шеф увидел бы в предупреждении «выше цены меню (1E+2 ₽)».
    """
    assert money_text(Decimal(value)) == expected
