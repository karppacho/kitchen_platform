"""Чтение справочника, блюд и «Сверки» — одно на сайт и на MCP.

Раньше эта логика жила в ручках ``web/api.py`` и ``web/reconciliation.py``.
MCP стоит в слое рядом с ``web`` и импортировать ручки не может, а второй
копии запросов и расчёта быть не должно: два места, где считается
себестоимость, однажды разойдутся. Поэтому запросы и расчёт — здесь, а ручки и
инструменты MCP только оформляют ответ (доли, строки, роли — их дело).

Строки, удалённые из листа (``removed_at``), не показываются; исключение —
блюдо по id, где «удалено» отличимо от «нет такого» (:class:`DishRemovedError`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from kitchen.db import models
from kitchen.db.links import card_candidates
from kitchen.db.recipes import ingredient_spec, load_recipes
from kitchen.domain.cards import APPROVED
from kitchen.domain.costs import calculate
from kitchen.domain.recipe import ROW_MAIN, Component, Recipe

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from kitchen.db.links import LinkCandidate
    from kitchen.domain.recipe import DishCost

DISH_NOT_FOUND = "блюдо не найдено"
DISH_REMOVED = "блюдо удалено из таблицы"

_SHARE_STEP = Decimal("0.001")


class DishLookupError(Exception):
    """Блюда нет для показа; ``detail`` — текст для человека."""

    detail: str = DISH_NOT_FOUND

    def __init__(self) -> None:
        super().__init__(self.detail)


class DishNotFoundError(DishLookupError):
    detail = DISH_NOT_FOUND


class DishRemovedError(DishLookupError):
    """Не «нет такого»: блюдо было, шеф убрал строку из листа. Вернёт — появится снова."""

    detail = DISH_REMOVED


class UnknownIngredientError(Exception):
    """В составе есть id, которого нет в справочнике."""

    def __init__(self, missing: Sequence[int]) -> None:
        super().__init__(", ".join(str(i) for i in missing))
        self.missing = tuple(missing)


def rounded_share(value: Decimal) -> Decimal:
    """Доля для показа: три знака, не двадцать восемь.

    В домене это результат деления и хранится с полной точностью — там она
    нужна, потому что доля сравнивается с порогами. Наружу столько знаков
    отдавать незачем: фронтенд получал «0.7777777777777777777777777778»
    и был обязан сам решать, до чего это округлять.
    """
    return value.quantize(_SHARE_STEP, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Справочник
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class IngredientEntry:
    """Позиция справочника: строка таблицы и есть ли у неё карточка."""

    row: models.Ingredient
    has_card: bool
    """Есть ли карточка, заполненная поваром. Главный вопрос справочника:
    что оформлено, но не доехало до расчёта."""


def _linked_ingredient_ids(session: Session) -> set[int]:
    linked: set[int] = set()
    for ingredient_id in session.scalars(
        select(models.IngredientCard.ingredient_id).where(
            models.IngredientCard.ingredient_id.is_not(None),
            models.IngredientCard.removed_at.is_(None),
        )
    ).all():
        if ingredient_id is not None:
            linked.add(ingredient_id)
    return linked


def list_ingredients(
    session: Session, search: str = "", status: str = "", limit: int = 200
) -> list[IngredientEntry]:
    """Справочник ингредиентов.

    Архивные не прячем: они стоят в составе живых блюд, и вопрос «почему
    у этого блюда такая себестоимость» без них не разобрать. Фильтр по
    статусу есть, умолчание — показывать всё. Удалённые из листа — прячем:
    шеф сказал, что позиции больше нет; в составе блюда она остаётся видна
    с замечанием «удалён из справочника».
    """
    query = (
        select(models.Ingredient)
        .where(models.Ingredient.removed_at.is_(None))
        .order_by(models.Ingredient.name)
    )
    if search:
        query = query.where(models.Ingredient.name.ilike(f"%{search}%"))
    if status:
        query = query.where(models.Ingredient.status == status)

    rows = session.scalars(query.limit(limit)).all()
    linked = _linked_ingredient_ids(session)
    return [IngredientEntry(row=row, has_card=row.id in linked) for row in rows]


def get_ingredient(session: Session, ingredient_id: int) -> IngredientEntry | None:
    """Позиция по id; ``None`` — нет такой или удалена из листа."""
    row = session.scalar(
        select(models.Ingredient).where(
            models.Ingredient.id == ingredient_id,
            models.Ingredient.removed_at.is_(None),
        )
    )
    if row is None:
        return None
    card_id = session.scalar(
        select(models.IngredientCard.id)
        .where(
            models.IngredientCard.ingredient_id == ingredient_id,
            models.IngredientCard.removed_at.is_(None),
        )
        .limit(1)
    )
    return IngredientEntry(row=row, has_card=card_id is not None)


# ---------------------------------------------------------------------------
# Блюда
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DishEntry:
    """Блюдо со свежесчитанной себестоимостью."""

    dish: models.Dish
    cost: DishCost


def list_dishes(session: Session, search: str = "", status: str = "") -> list[DishEntry]:
    """Блюда со свежесчитанной себестоимостью.

    Себестоимость не хранится, а считается: хранимое число живёт своей
    жизнью и однажды разойдётся с ценами в справочнике, а объяснить
    расхождение будет некому.
    """
    recipes = {recipe.key: recipe for recipe in load_recipes(session)}

    query = (
        select(models.Dish).where(models.Dish.removed_at.is_(None)).order_by(models.Dish.legacy_id)
    )
    if search:
        query = query.where(models.Dish.name.ilike(f"%{search}%"))
    if status:
        query = query.where(models.Dish.status == status)

    result: list[DishEntry] = []
    for dish in session.scalars(query).all():
        recipe = recipes.get(dish.legacy_id)
        if recipe is None:
            continue
        result.append(DishEntry(dish=dish, cost=calculate(recipe)))
    return result


def get_dish(session: Session, legacy_id: str) -> DishEntry:
    """Блюдо с разбивкой по составу и всеми замечаниями.

    Замечания отдаются полностью, а не числом: именно они объясняют, почему
    себестоимость такая, и без них цифра — просто цифра.
    """
    dish = session.scalar(
        select(models.Dish)
        .options(selectinload(models.Dish.components))
        .where(models.Dish.legacy_id == legacy_id)
    )
    if dish is None:
        raise DishNotFoundError
    if dish.removed_at is not None:
        raise DishRemovedError

    recipe = next((r for r in load_recipes(session) if r.key == legacy_id), None)
    if recipe is None:
        raise DishNotFoundError
    return DishEntry(dish=dish, cost=calculate(recipe))


def calculate_composition(session: Session, items: Sequence[tuple[int, Decimal]]) -> DishCost:
    """Посчитать состав «ингредиент — граммы нетто» тем же ``domain.costs``.

    Ничего не пишет. Цены, потери и КБЖУ берутся из справочника базы, как у
    блюда; цены меню у состава нет, поэтому доли от меню пусты. Строка,
    удалённая из листа, считается по последним данным и с замечанием — как в
    блюде.
    """
    ids = sorted({ingredient_id for ingredient_id, _ in items})
    rows = {
        row.id: row
        for row in session.scalars(
            select(models.Ingredient).where(models.Ingredient.id.in_(ids))
        ).all()
    }
    missing = [i for i in ids if i not in rows]
    if missing:
        raise UnknownIngredientError(missing)

    recipe = Recipe(
        key="composition",
        name="Состав из запроса",
        components=tuple(
            Component(
                row_type=ROW_MAIN,
                net_weight_g=grams,
                ingredient=ingredient_spec(rows[ingredient_id]),
            )
            for ingredient_id, grams in items
        ),
    )
    return calculate(recipe)


# ---------------------------------------------------------------------------
# «Сверка»
# ---------------------------------------------------------------------------
Action = Literal["confirm", "to_reference"]
"""Что можно сделать с карточкой на «Сверке»:

* ``confirm`` — «Это он»: выбрать ингредиент из ``candidates``;
* ``to_reference`` — форма переноса в справочник: «Добавить в справочник» у
  карточки без пары, «Это новый» у спорной.
"""


@dataclass(frozen=True, slots=True)
class ReconciliationItem:
    """Карточка, требующая решения человека."""

    card: models.IngredientCard
    approved: bool
    """Карточка согласована («Да» в книге карточек — по последнему переносу в
    базу). В справочник попадают только такие; запись всё равно сверяет «Да»
    свежим чтением."""
    candidates: tuple[LinkCandidate, ...]
    actions: list[Action]
    """Доступные действия; пусто — только пояснение, без кнопки."""


@dataclass(frozen=True, slots=True)
class ReconciliationOverview:
    total: int
    linked: int
    needs_human: int
    items: list[ReconciliationItem]


def reconciliation_actions(approved: bool, candidates: tuple[object, ...]) -> list[Action]:
    """«Это он» — когда есть из кого выбрать; форма переноса — только для
    согласованной карточки: иначе её откажет запись."""
    found: list[Action] = []
    if candidates:
        found.append("confirm")
    if approved:
        found.append("to_reference")
    return found


def reconciliation_overview(session: Session) -> ReconciliationOverview:
    """Карточки, по которым решение принимает человек, — список дел.

    Автоматически склеенное сюда не попадает. Здесь только спорное: тёзки,
    похожие имена и то, чему пары нет вовсе. Подставить не тот ингредиент
    хуже, чем не подставить никакого.
    """
    # Явным циклом, а не через dict(): у SQLAlchemy строка результата
    # типизирована как Row, и dict() от неё mypy не принимает, а
    # dict-comprehension не принимает ruff.
    counts: dict[str, int] = {}
    for link_status, number in session.execute(
        select(models.IngredientCard.link_status, func.count())
        .where(models.IngredientCard.removed_at.is_(None))
        .group_by(models.IngredientCard.link_status)
    ).all():
        counts[link_status] = number

    cards = session.scalars(
        select(models.IngredientCard)
        .where(
            models.IngredientCard.link_status != "linked",
            models.IngredientCard.removed_at.is_(None),
        )
        .order_by(models.IngredientCard.link_status, models.IngredientCard.name)
    ).all()
    # Кандидаты — тем же подбором, что у импорта и у проверки «Это он»: из
    # этого списка человек выбирает, по нему же проверяют выбор.
    candidates = card_candidates(session, cards)

    items: list[ReconciliationItem] = []
    for card in cards:
        approved = card.approval_status == APPROVED
        offered = candidates[card.id]
        items.append(
            ReconciliationItem(
                card=card,
                approved=approved,
                candidates=offered,
                actions=reconciliation_actions(approved, offered),
            )
        )

    total = sum(counts.values())
    linked = counts.get("linked", 0)
    return ReconciliationOverview(
        total=total, linked=linked, needs_human=total - linked, items=items
    )
