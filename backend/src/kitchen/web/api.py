"""Ручки чтения: профиль, справочник, блюда, свежесть данных.

Здесь только просмотр. Запись платформы в таблицы живёт в своих ручках
(docs/adr/0003): новая строка карточки в «Лист1» книги карточек — в ручках
карточек (``kitchen.web.cards``), ручные ячейки строки ING — в ручках
«Сверки» (``kitchen.web.reconciliation``, там же её список дел).

Все ручки требуют входа. Открытым остаётся только `/healthz`, и то он
слушается изнутри — наружу его закрывает nginx.

Строки, удалённые из листа, ручки не показывают (`removed_at`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from kitchen.cards import catalog
from kitchen.cards.sync_status import SyncStatus, read_sync_status
from kitchen.config import Settings
from kitchen.web.auth import CurrentUser, CurrentUserDep, SessionDep, get_settings, require

if TYPE_CHECKING:
    from kitchen.db import models
    from kitchen.domain.recipe import DishCost

router = APIRouter(prefix="/api")

# Справочник и блюда несут цены и маржу. Повару (`cook`) они не
# положены: он заводит карточки ингредиентов и больше ничего не видит.
# Спрятать раздел в меню мало — ручку можно открыть и по адресу, поэтому
# отказ живёт здесь. `/me` и `/sync` открыты всем вошедшим: строка
# свежести данных висит и над экраном повара.
PricesViewerDep = Annotated[CurrentUser, Depends(require("chef", "developer", "commerce"))]


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
    user: PricesViewerDep,
    search: Annotated[str, Query(description="часть имени")] = "",
    status_filter: Annotated[str, Query(alias="status")] = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[IngredientRow]:
    """Справочник ингредиентов (запрос и правила — в ``kitchen.cards.catalog``)."""
    return [
        IngredientRow(
            id=entry.row.id,
            legacy_id=entry.row.legacy_id,
            name=entry.row.name,
            category=entry.row.category,
            unit=entry.row.unit,
            status=entry.row.status,
            price_per_kg=entry.row.price_per_kg,
            weight_per_piece_g=entry.row.weight_per_piece_g,
            has_card=entry.has_card,
        )
        for entry in catalog.list_ingredients(session, search, status_filter, limit)
    ]


@router.get("/dishes", response_model=list[DishRow])
def dishes(
    session: SessionDep,
    user: PricesViewerDep,
    search: Annotated[str, Query(description="часть названия")] = "",
    status_filter: Annotated[str, Query(alias="status")] = "",
) -> list[DishRow]:
    """Блюда со свежесчитанной себестоимостью (считает ``kitchen.cards.catalog``)."""
    return [
        _to_row(entry.cost, entry.dish)
        for entry in catalog.list_dishes(session, search, status_filter)
    ]


@router.get("/dishes/{legacy_id}", response_model=DishDetail)
def dish_detail(
    legacy_id: str,
    session: SessionDep,
    user: PricesViewerDep,
) -> DishDetail:
    """Блюдо с разбивкой по составу и всеми замечаниями.

    Замечания отдаются полностью, а не числом: именно они объясняют, почему
    себестоимость такая, и без них цифра — просто цифра.
    """
    try:
        entry = catalog.get_dish(session, legacy_id)
    except catalog.DishRemovedError as error:
        # Не 404: блюдо было, шеф убрал строку из листа. Вернёт — появится снова.
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=error.detail) from error
    except catalog.DishLookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.detail) from error

    cost = entry.cost
    base = _to_row(cost, entry.dish)
    return DishDetail(
        **base.model_dump(),
        protein_g=cost.protein_g,
        fat_g=cost.fat_g,
        carbs_g=cost.carbs_g,
        kcal=cost.kcal,
        kbju_coverage=catalog.rounded_share(cost.kbju_coverage),
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


@router.get("/sync", response_model=SyncStatus)
def sync(
    session: SessionDep,
    user: CurrentUserDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncStatus:
    """Насколько свежи данные — для строки над каждым экраном."""
    return read_sync_status(session, settings.sync_stale_after_seconds)
