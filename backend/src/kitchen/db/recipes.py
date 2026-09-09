"""Сборка рецептур из базы для калькулятора.

Мост между хранилищем и чистой предметной областью: `kitchen.domain` не
знает ни про SQLAlchemy, ни про наши таблицы, и знать не должен. Поэтому
перекладывание полей живёт здесь, а не там.

Одно соответствие стоит назвать вслух: колонка `price_per_kg` в справочнике
называется «Цена за 1 кг (или 1 шт / 1 л)» — то есть это цена за единицу,
какой бы та ни была. Что именно значит число, определяет колонка «Единица
измерения», а не имя поля.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from kitchen.db import models
from kitchen.domain.recipe import (
    Component,
    IngredientSpec,
    PackagingSpec,
    Recipe,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_ZERO = Decimal("0")


def _ingredient(row: models.Ingredient) -> IngredientSpec:
    return IngredientSpec(
        key=row.legacy_id,
        name=row.name,
        short_name=row.short_name,
        unit=row.unit,
        price_per_unit=row.price_per_kg,
        weight_per_piece_g=row.weight_per_piece_g,
        losses_unpacking=row.losses_unpacking,
        losses_cutting=row.losses_cutting,
        losses_thermal=row.losses_thermal,
        protein_100g=row.protein,
        fat_100g=row.fat,
        carbs_100g=row.carbs,
        kcal_100g=row.kcal,
    )


def _packaging(row: models.Packaging) -> PackagingSpec:
    return PackagingSpec(
        key=row.legacy_id,
        name=row.name,
        price_per_piece=row.price_per_piece,
    )


def load_recipes(session: Session) -> list[Recipe]:
    """Все блюда с составом, отсортированные по идентификатору.

    Состав грузится одним запросом на связь (`selectinload`), а не по
    строке на блюдо: расчёт всей карты — обычная операция, и превращать её
    в четыре сотни запросов незачем.
    """
    ingredients = {
        row.id: _ingredient(row) for row in session.scalars(select(models.Ingredient)).all()
    }
    packagings = {
        row.id: _packaging(row) for row in session.scalars(select(models.Packaging)).all()
    }

    dishes = session.scalars(
        select(models.Dish)
        .options(selectinload(models.Dish.components))
        .order_by(models.Dish.legacy_id)
    ).all()

    recipes: list[Recipe] = []
    for dish in dishes:
        components = tuple(
            Component(
                row_type=component.row_type,
                # Пустой вес в листе означает ноль, а не отсутствие строки:
                # позиция всё равно попадает в рецептуру и в предупреждения.
                net_weight_g=component.net_weight_g or _ZERO,
                ingredient=ingredients.get(component.ingredient_id or -1),
                packaging=packagings.get(component.packaging_id or -1),
            )
            for component in sorted(dish.components, key=lambda c: c.source_row or 0)
        )
        recipes.append(
            Recipe(
                key=dish.legacy_id,
                name=dish.name,
                price_menu=dish.price_menu,
                components=components,
            )
        )
    return recipes
