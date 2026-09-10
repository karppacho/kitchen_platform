"""Ручки чтения.

Фаза 2 — только просмотр. Запись появится в фазе 3, и не раньше, чем
будет решено, что делать с листами: пока живы боты, они там единственные
писатели.

Все ручки требуют входа. Открытым остаётся только `/healthz`, и то он
слушается изнутри — наружу его закрывает nginx.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from kitchen.db import models
from kitchen.db.recipes import load_recipes
from kitchen.domain.costs import calculate
from kitchen.web.auth import CurrentUserDep, SessionDep

if TYPE_CHECKING:
    from kitchen.domain.recipe import DishCost

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Схемы ответов
# ---------------------------------------------------------------------------
class Me(BaseModel):
    email: str
    display_name: str
    roles: list[str]


class IngredientRow(BaseModel):
    id: int
    legacy_id: str
    name: str
    category: str
    unit: str
    status: str
    price_per_kg: Decimal | None
    weight_per_piece_g: Decimal | None
    has_card: bool
    """Есть ли карточка, заполненная поваром. Главный вопрос справочника:
    что оформлено, но не доехало до расчёта."""


class DishRow(BaseModel):
    legacy_id: str
    name: str
    category: str
    status: str
    price_menu: Decimal | None
    uc_rub: Decimal
    uc_percent: Decimal | None
    margin_percent: Decimal | None
    output_grams: Decimal
    warnings: int
    """Число замечаний. Ноль — не «всё хорошо», а «нам не на что указать»."""


class ComponentRow(BaseModel):
    name: str
    short_name: str
    row_type: str
    unit: str
    net_weight_g: Decimal
    gross_weight_g: Decimal | None
    price_per_unit: Decimal | None
    cost_rub: Decimal
    share_percent: Decimal | None


class DishDetail(DishRow):
    protein_g: Decimal
    fat_g: Decimal
    carbs_g: Decimal
    kcal: Decimal
    kbju_coverage: Decimal
    components: list[ComponentRow]
    warning_texts: list[str]


class CandidateRow(BaseModel):
    ingredient_id: int
    legacy_id: str
    name: str
    score: float | None = None


class ReconciliationRow(BaseModel):
    """Карточка, требующая решения человека."""

    card_id: int
    name: str
    link_status: str
    supplier: str
    candidates: list[CandidateRow]


class ReconciliationSummary(BaseModel):
    total: int
    linked: int
    needs_human: int
    rows: list[ReconciliationRow]


# ---------------------------------------------------------------------------
def _to_row(cost: DishCost, dish: models.Dish) -> DishRow:
    return DishRow(
        legacy_id=dish.legacy_id,
        name=dish.name,
        category=dish.category,
        status=dish.status,
        price_menu=cost.price_menu,
        uc_rub=cost.uc_rub,
        uc_percent=cost.uc_percent,
        margin_percent=cost.margin_percent,
        output_grams=cost.output_grams,
        warnings=len(cost.warnings),
    )


@router.get("/me", response_model=Me)
def me(user: CurrentUserDep) -> Me:
    return Me(email=user.email, display_name=user.display_name, roles=sorted(user.roles))


@router.get("/ingredients", response_model=list[IngredientRow])
def ingredients(
    session: SessionDep,
    user: CurrentUserDep,
    search: Annotated[str, Query(description="часть имени")] = "",
    status_filter: Annotated[str, Query(alias="status")] = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[IngredientRow]:
    """Справочник ингредиентов.

    Архивные не прячем: они стоят в составе живых блюд, и вопрос «почему
    у этого блюда такая себестоимость» без них не разобрать. Фильтр по
    статусу есть, умолчание — показывать всё.
    """
    query = select(models.Ingredient).order_by(models.Ingredient.name)
    if search:
        query = query.where(models.Ingredient.name.ilike(f"%{search}%"))
    if status_filter:
        query = query.where(models.Ingredient.status == status_filter)

    rows = session.scalars(query.limit(limit)).all()
    linked = {
        card.ingredient_id
        for card in session.scalars(
            select(models.IngredientCard).where(models.IngredientCard.ingredient_id.is_not(None))
        ).all()
    }

    return [
        IngredientRow(
            id=row.id,
            legacy_id=row.legacy_id,
            name=row.name,
            category=row.category,
            unit=row.unit,
            status=row.status,
            price_per_kg=row.price_per_kg,
            weight_per_piece_g=row.weight_per_piece_g,
            has_card=row.id in linked,
        )
        for row in rows
    ]


@router.get("/dishes", response_model=list[DishRow])
def dishes(
    session: SessionDep,
    user: CurrentUserDep,
    search: Annotated[str, Query(description="часть названия")] = "",
    status_filter: Annotated[str, Query(alias="status")] = "",
) -> list[DishRow]:
    """Блюда со свежесчитанной себестоимостью.

    Себестоимость не хранится, а считается: хранимое число живёт своей
    жизнью и однажды разойдётся с ценами в справочнике, а объяснить
    расхождение будет некому.
    """
    recipes = {recipe.key: recipe for recipe in load_recipes(session)}

    query = select(models.Dish).order_by(models.Dish.legacy_id)
    if search:
        query = query.where(models.Dish.name.ilike(f"%{search}%"))
    if status_filter:
        query = query.where(models.Dish.status == status_filter)

    result: list[DishRow] = []
    for dish in session.scalars(query).all():
        recipe = recipes.get(dish.legacy_id)
        if recipe is None:
            continue
        result.append(_to_row(calculate(recipe), dish))
    return result


@router.get("/dishes/{legacy_id}", response_model=DishDetail)
def dish_detail(
    legacy_id: str,
    session: SessionDep,
    user: CurrentUserDep,
) -> DishDetail:
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="блюдо не найдено")

    recipe = next((r for r in load_recipes(session) if r.key == legacy_id), None)
    if recipe is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="блюдо не найдено")

    cost = calculate(recipe)
    base = _to_row(cost, dish)
    return DishDetail(
        **base.model_dump(),
        protein_g=cost.protein_g,
        fat_g=cost.fat_g,
        carbs_g=cost.carbs_g,
        kcal=cost.kcal,
        kbju_coverage=cost.kbju_coverage,
        components=[
            ComponentRow(
                name=item.name,
                short_name=item.short_name,
                row_type=item.row_type,
                unit=item.unit,
                net_weight_g=item.net_weight_g,
                gross_weight_g=item.gross_weight_g,
                price_per_unit=item.price_per_unit,
                cost_rub=item.cost_rub,
                share_percent=item.share_percent,
            )
            for item in cost.components
        ],
        warning_texts=list(cost.warnings),
    )


@router.get("/reconciliation", response_model=ReconciliationSummary)
def reconciliation(
    session: SessionDep,
    user: CurrentUserDep,
) -> ReconciliationSummary:
    """Карточки, по которым решение принимает человек.

    Автоматически склеенное сюда не попадает. Здесь только спорное: тёзки,
    похожие имена и то, чему пары нет вовсе. Подставить не тот ингредиент
    хуже, чем не подставить никакого.
    """
    # Явным циклом, а не через dict(): у SQLAlchemy строка результата
    # типизирована как Row, и dict() от неё mypy не принимает, а
    # dict-comprehension не принимает ruff.
    counts: dict[str, int] = {}
    for link_status, number in session.execute(
        select(models.IngredientCard.link_status, func.count()).group_by(
            models.IngredientCard.link_status
        )
    ).all():
        counts[link_status] = number

    cards = session.scalars(
        select(models.IngredientCard)
        .where(models.IngredientCard.link_status != "linked")
        .order_by(models.IngredientCard.link_status, models.IngredientCard.name)
    ).all()

    rows: list[ReconciliationRow] = []
    for card in cards:
        # Для спорных показываем тёзок; для остальных кандидатов ищет
        # экран сверки по запросу — гонять подбор по всему справочнику на
        # каждый список незачем.
        candidates: list[CandidateRow] = []
        if card.link_status == "ambiguous":
            namesakes = session.scalars(
                select(models.Ingredient).where(
                    func.lower(models.Ingredient.name) == card.name.lower(),
                    or_(models.Ingredient.status != "архив", models.Ingredient.status.is_(None)),
                )
            ).all()
            candidates = [
                CandidateRow(ingredient_id=row.id, legacy_id=row.legacy_id, name=row.name)
                for row in namesakes
            ]

        rows.append(
            ReconciliationRow(
                card_id=card.id,
                name=card.name,
                link_status=card.link_status,
                supplier=card.supplier,
                candidates=candidates,
            )
        )

    total = sum(counts.values())
    linked = counts.get("linked", 0)
    return ReconciliationSummary(
        total=total,
        linked=linked,
        needs_human=total - linked,
        rows=rows,
    )
