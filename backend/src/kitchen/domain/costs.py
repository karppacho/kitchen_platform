"""Расчёт себестоимости — детерминированная арифметика.

Перенесён из `kitchen_bot/src/calc/costs.py` без изменения поведения.
Эталон в `tests/golden/dishes_uc.json` снят со старого кода, и совпадать
обязано до копейки — включая тексты предупреждений: шеф читает именно их,
а не наши намерения.

**Числа считает этот модуль, не модель.** LLM получает готовый результат и
пересказывает его. Никакой арифметики в промптах.

Правила, каждое из которых стоит за конкретным разбором:

* Потери накидываются СВЕРХУ на нетто: брутто = нетто / ((1−перетарка) ×
  (1−нарезка)). Деньги считаются по брутто, а в выход блюда идёт нетто.
* Тепловые потери читаются, но не применяются. Осознанный пробел.
* Нет цены, цена ровно `0`, штучный без веса штуки — стоимость 0 И
  предупреждение. Заниженный UC без замечания подрывает доверие к цифрам.
* Нет цены меню — маржа `None`, а не ноль.
* Себестоимость выше цены — почти всегда ошибка в данных, и молчать
  нельзя: 04.08.2026 так создалось блюдо с UC 1398 ₽ при цене 280 ₽.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from kitchen.domain.recipe import (
    ROW_MAIN,
    ROW_PACKAGING,
    UNIT_MILLILITRE,
    UNIT_PIECE,
    UNIT_WEIGHT,
    ComponentCost,
    DishCost,
)

if TYPE_CHECKING:
    from kitchen.domain.recipe import Component, IngredientSpec, Recipe

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")
_GRAMS_IN_KG = Decimal("1000")

COVERAGE_COMPLETE = Decimal("0.999")
COVERAGE_POOR = Decimal("0.5")


def round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_percent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def kbju_coverage_status(coverage: Decimal) -> str:
    """complete / partial / poor.

    `poor` (меньше половины состава по весу) означает, что цифрам КБЖУ
    доверять нельзя, а не что они слегка неточны.
    """
    if coverage >= COVERAGE_COMPLETE:
        return "complete"
    if coverage >= COVERAGE_POOR:
        return "partial"
    return "poor"


def _remaining(ingredient: IngredientSpec) -> Decimal:
    """Доля, остающаяся после потерь при перетарке и нарезке."""
    unpacking = ingredient.losses_unpacking or _ZERO
    cutting = ingredient.losses_cutting or _ZERO
    return (_ONE - unpacking) * (_ONE - cutting)


def gross_weight_g(ingredient: IngredientSpec, net_weight_g: Decimal) -> Decimal:
    """Брутто из нетто.

    Потери 100% — вырожденный случай: возвращаем нетто, чтобы не делить на
    ноль. Используется только для отображения рецептуры.
    """
    remaining = _remaining(ingredient)
    if remaining <= _ZERO:
        return net_weight_g
    return net_weight_g / remaining


def ingredient_cost(ingredient: IngredientSpec, net_weight_g: Decimal) -> Decimal:
    """Стоимость нетто-веса с учётом потерь.

    Весовые: цена за кг (или литр) на количество. Штучные: в ТТК записано
    «столько-то граммов этой штуки», и перевести в штуки можно только через
    вес одной — отсюда обязательность колонки «Вес 1 шт».
    """
    if ingredient.price_per_unit is None:
        return _ZERO

    remaining = _remaining(ingredient)
    if remaining == _ZERO:
        return _ZERO
    gross = net_weight_g / remaining

    if ingredient.unit == UNIT_PIECE:
        if not ingredient.weight_per_piece_g:
            return _ZERO
        return (gross / ingredient.weight_per_piece_g) * ingredient.price_per_unit
    if ingredient.unit in UNIT_WEIGHT:
        return (gross / _GRAMS_IN_KG) * ingredient.price_per_unit
    if ingredient.unit == UNIT_MILLILITRE:
        # Цена за миллилитр маловероятна, но если так — без деления.
        return gross * ingredient.price_per_unit
    return _ZERO


def calculate(recipe: Recipe) -> DishCost:
    """Себестоимость, маржа и КБЖУ блюда."""
    price = recipe.price_menu
    has_price = price is not None and price > _ZERO

    if not recipe.components:
        return DishCost(
            key=recipe.key,
            name=recipe.name,
            price_menu=price,
            uc_rub=_ZERO,
            uc_percent=_ZERO if has_price else None,
            margin_rub=price if has_price else None,
            margin_percent=_HUNDRED if has_price else None,
            output_grams=_ZERO,
            protein_g=_ZERO,
            fat_g=_ZERO,
            carbs_g=_ZERO,
            kcal=_ZERO,
            kbju_coverage=_ZERO,
            components=[],
            warnings=["В ТТК нет ни одной строки для этого блюда"],
        )

    state = _Accumulator()
    for component in recipe.components:
        if component.row_type == ROW_MAIN:
            state.add_main(component)
        elif component.row_type == ROW_PACKAGING:
            state.add_packaging(component)

    uc = round_money(state.total_cost)

    uc_percent: Decimal | None = None
    margin: Decimal | None = None
    margin_percent: Decimal | None = None
    if has_price and price is not None:
        uc_percent = round_percent(uc / price * _HUNDRED)
        margin = round_money(price - uc)
        margin_percent = round_percent(margin / price * _HUNDRED)
        if uc > price:
            state.warnings.append(
                f"Себестоимость ({uc} ₽) выше цены меню ({price} ₽) — "
                f"маржа отрицательная. Проверь единицы измерения и цены "
                f"ингредиентов, обычно это ошибка в данных"
            )
    else:
        state.warnings.append("Цена меню не заполнена — считаю только себестоимость, маржу не могу")

    # Доли считаются после того, как известна итоговая себестоимость.
    # ComponentCost заморожен, поэтому строки пересоздаются, а не правятся:
    # результат расчёта не должен меняться под руками у того, кто его уже
    # получил.
    items = state.items
    if uc > _ZERO:
        items = [
            replace(item, share_percent=round_percent(item.cost_rub / uc * _HUNDRED))
            for item in state.items
        ]

    coverage = state.kbju_covered_g / state.output_grams if state.output_grams > _ZERO else _ZERO
    if state.missing_kbju:
        base = f"КБЖУ нет у {len(state.missing_kbju)} ингр. ({', '.join(state.missing_kbju)})"
        if kbju_coverage_status(coverage) == "poor":
            # round() от float возвращает int — обёртка int() была бы лишней.
            percent = round(float(coverage) * 100)
            state.warnings.append(
                base + f" — заполнено лишь {percent}% состава по весу, цифрам нельзя доверять"
            )
        else:
            state.warnings.append(base + " — нутриенты приблизительны")

    return DishCost(
        key=recipe.key,
        name=recipe.name,
        price_menu=price,
        uc_rub=uc,
        uc_percent=uc_percent,
        margin_rub=margin,
        margin_percent=margin_percent,
        output_grams=state.output_grams,
        protein_g=round_percent(state.protein),
        fat_g=round_percent(state.fat),
        carbs_g=round_percent(state.carbs),
        kcal=round_money(state.kcal).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
        kbju_coverage=coverage,
        components=items,
        warnings=state.warnings,
    )


class _Accumulator:
    """Накопитель по строкам состава.

    Отдельным классом, чтобы `calculate` читался как последовательность
    шагов, а не как трёхсотстрочный цикл с дюжиной переменных.
    """

    def __init__(self) -> None:
        self.items: list[ComponentCost] = []
        self.warnings: list[str] = []
        self.total_cost = _ZERO
        self.output_grams = _ZERO
        self.protein = _ZERO
        self.fat = _ZERO
        self.carbs = _ZERO
        self.kcal = _ZERO
        self.kbju_covered_g = _ZERO
        self.missing_kbju: list[str] = []

    def add_main(self, component: Component) -> None:
        ingredient = component.ingredient
        if ingredient is None:
            self.warnings.append(f"Строка ТТК без id_ингредиента (вес {component.net_weight_g} г)")
            return

        cost = self._main_cost(ingredient, component.net_weight_g)

        self.items.append(
            ComponentCost(
                name=ingredient.name,
                short_name=ingredient.short_name,
                row_type=ROW_MAIN,
                unit=ingredient.unit,
                net_weight_g=component.net_weight_g,
                gross_weight_g=round_money(gross_weight_g(ingredient, component.net_weight_g)),
                price_per_unit=ingredient.price_per_unit,
                weight_per_piece_g=ingredient.weight_per_piece_g,
                cost_rub=round_money(cost),
            )
        )
        self.total_cost += cost
        # Съедобный выход — только основные строки, и только нетто.
        self.output_grams += component.net_weight_g
        self._add_kbju(ingredient, component.net_weight_g)

    def _main_cost(self, ingredient: IngredientSpec, net_weight_g: Decimal) -> Decimal:
        """Стоимость строки, и предупреждение вместо молчания.

        Ингредиент остаётся в составе даже при нулевой стоимости: его вес
        входит в выход, КБЖУ и рецептуру. Выбросить его значило бы отдать
        неполную карту и заниженный выход.
        """
        if ingredient.price_per_unit is None or ingredient.price_per_unit == _ZERO:
            self.warnings.append(
                f"У ингредиента «{ingredient.name}» не заполнена цена (пусто или 0) — "
                f"в UC не учтён (стоимость 0)"
            )
            return _ZERO
        if ingredient.unit == UNIT_PIECE and not ingredient.weight_per_piece_g:
            self.warnings.append(
                f"У штучного ингредиента «{ingredient.name}» не указан вес 1 шт — "
                f"в UC не учтён (стоимость 0)"
            )
            return _ZERO
        return ingredient_cost(ingredient, net_weight_g)

    def _add_kbju(self, ingredient: IngredientSpec, net_weight_g: Decimal) -> None:
        """КБЖУ с сотни граммов на фактический вес.

        Тепловые потери не учитываются — как и в стоимости.
        """
        if ingredient.kcal_100g is None:
            self.missing_kbju.append(ingredient.name)
            return
        factor = net_weight_g / _HUNDRED
        self.protein += (ingredient.protein_100g or _ZERO) * factor
        self.fat += (ingredient.fat_100g or _ZERO) * factor
        self.carbs += (ingredient.carbs_100g or _ZERO) * factor
        self.kcal += ingredient.kcal_100g * factor
        self.kbju_covered_g += net_weight_g

    def add_packaging(self, component: Component) -> None:
        packaging = component.packaging
        if packaging is None:
            self.warnings.append("Строка ТТК-упаковки без id_упаковки")
            return
        if packaging.price_per_piece is None:
            # Строка выпадает целиком, а не добавляется с нулём: у бота было
            # так, и эталон снят с него.
            self.warnings.append(f"У упаковки «{packaging.name}» нет цены")
            return

        cost = packaging.price_per_piece * component.net_weight_g
        self.items.append(
            ComponentCost(
                name=packaging.name,
                short_name="",
                row_type=ROW_PACKAGING,
                unit=UNIT_PIECE,
                net_weight_g=component.net_weight_g,
                gross_weight_g=None,
                price_per_unit=packaging.price_per_piece,
                weight_per_piece_g=None,
                cost_rub=round_money(cost),
            )
        )
        self.total_cost += cost
