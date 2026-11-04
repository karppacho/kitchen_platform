"""Инструменты чтения: справочник, блюда, расчёт состава, «Сверка», свежесть.

Все с ``readOnlyHint``: клиент не спрашивает подтверждения. Данные берутся теми
же функциями слоя приложения (:mod:`kitchen.cards.catalog`,
:mod:`kitchen.cards.sync_status`), что и ручки сайта, — второй копии запросов и
расчёта нет.

Два правила, которые держит этот файл:

* Правило 3: числа считает код. Инструмент отдаёт посчитанное с единицами, а
  «а если» — только ``poschitat_sostav`` (тот же ``domain.costs``).
* Решение 4 спеки: повар видит справочник без цен. Цены, потери, себестоимость
  и маржа отсекаются **в данных** (поля не попадают в ответ), а не просьбой в
  описании. Блюда, расчёт и «Сверка» повару закрыты ролью.

Числа — строками без потери точности (``format(d, "f")``), пусто — ``null``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from kitchen.cards import catalog
from kitchen.cards.sync_status import read_sync_status

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from kitchen.db import models
    from kitchen.domain.recipe import DishCost
    from kitchen.mcp.deps import McpDeps

READ_ONLY = ToolAnnotations(read_only_hint=True)

# Кому видны цены и блюда — те же роли, что у ручек сайта (``web.api``).
PRICE_ROLES = ("chef", "developer", "commerce")

SEARCH_LIMIT = 20
"""Сколько позиций отдаёт поиск: ответ читает модель, а не человек."""
QUERY_MAX = 100
DISHES_DEFAULT_LIMIT = 200
DISHES_MAX_LIMIT = 500
COMPOSITION_MAX_ITEMS = 100
GRAMS_MAX = Decimal(1_000_000)
ID_MAX = 2**63 - 1

NO_RECALC = "Числа посчитаны платформой; не пересчитывай их сам."


def _num(value: Decimal | None) -> str | None:
    """Число строкой без потери точности; пусто — ``None``."""
    return None if value is None else format(value, "f")


class CompositionItem(BaseModel):
    """Строка состава для ``poschitat_sostav``."""

    model_config = ConfigDict(extra="forbid")

    ingredient_id: Annotated[int, Field(ge=1, le=ID_MAX, description="id из poisk_ingredienta")]
    grams: Annotated[
        Decimal,
        Field(gt=0, le=GRAMS_MAX, allow_inf_nan=False, description="граммы нетто в блюде"),
    ]


# ---------------------------------------------------------------------------
# Оформление ответов
# ---------------------------------------------------------------------------
def _ingredient_short(entry: catalog.IngredientEntry, *, prices: bool) -> dict[str, Any]:
    row = entry.row
    short: dict[str, Any] = {
        "id": row.id,
        "legacy_id": row.legacy_id,
        "name": row.name,
        "unit": row.unit,
        "category": row.category,
        "status": row.status,
        "has_card": entry.has_card,
    }
    if prices:
        short["price_per_unit"] = _num(row.price_per_kg)
    return short


def _ingredient_full(entry: catalog.IngredientEntry, *, prices: bool) -> dict[str, Any]:
    row: models.Ingredient = entry.row
    full = _ingredient_short(entry, prices=False)
    full.update(
        {
            "full_name": row.full_name,
            "short_name": row.short_name,
            "manufacturer": row.manufacturer,
            "composition": row.composition,
            "protein_100g": _num(row.protein),
            "fat_100g": _num(row.fat),
            "carbs_100g": _num(row.carbs),
            "kcal_100g": _num(row.kcal),
            "weight_per_piece_g": _num(row.weight_per_piece_g),
        }
    )
    if prices:
        full.update(
            {
                "price_per_unit": _num(row.price_per_kg),
                "price_per_pack": _num(row.price_per_pack),
                "losses_total": _num(row.losses_total),
                "losses_unpacking": _num(row.losses_unpacking),
                "losses_cutting": _num(row.losses_cutting),
                "losses_thermal": _num(row.losses_thermal),
            }
        )
    return full


def _components(cost: DishCost) -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "short_name": item.short_name,
            "row_type": item.row_type,
            "unit": item.unit,
            "net_weight_g": _num(item.net_weight_g),
            "gross_weight_g": _num(item.gross_weight_g),
            "price_per_unit": _num(item.price_per_unit),
            "cost_rub": _num(item.cost_rub),
            "share_percent": _num(item.share_percent),
        }
        for item in cost.components
    ]


def _kbju(cost: DishCost) -> dict[str, Any]:
    return {
        "protein_g": _num(cost.protein_g),
        "fat_g": _num(cost.fat_g),
        "carbs_g": _num(cost.carbs_g),
        "kcal": _num(cost.kcal),
        "kbju_coverage": _num(catalog.rounded_share(cost.kbju_coverage)),
    }


def _dish_row(entry: catalog.DishEntry) -> dict[str, Any]:
    cost = entry.cost
    return {
        "id": entry.dish.legacy_id,
        "name": entry.dish.name,
        "category": entry.dish.category,
        "status": entry.dish.status,
        "price_menu": _num(cost.price_menu),
        "uc_rub": _num(cost.uc_rub),
        "uc_percent": _num(cost.uc_percent),
        "margin_percent": _num(cost.margin_percent),
        "output_grams": _num(cost.output_grams),
        "warnings": len(cost.warnings),
    }


def _sverka_row(item: catalog.ReconciliationItem) -> dict[str, Any]:
    return {
        "card_id": item.card.id,
        "name": item.card.name,
        "link_status": item.card.link_status,
        "supplier": item.card.supplier,
        "approved": item.approved,
        "candidates": [
            {
                "ingredient_id": c.ingredient_id,
                "legacy_id": c.legacy_id,
                "name": c.name,
                "score": c.score,
            }
            for c in item.candidates
        ],
        "actions": list(item.actions),
    }


# ---------------------------------------------------------------------------
def register_read_tools(server: MCPServer, deps: McpDeps) -> None:
    """Зарегистрировать инструменты чтения на ``server`` (с ``readOnlyHint``)."""

    def prices_visible() -> bool:
        return deps.user().has(*PRICE_ROLES)

    @server.tool(annotations=READ_ONLY)
    def poisk_ingredienta(zapros: str) -> dict[str, Any]:
        """Найти позиции справочника по части названия (до 20 штук).

        Отдаёт id, название, единицу, категорию, статус и есть ли карточка.
        Цена за единицу — только ролям с ценами. id нужен для ingredient и
        poschitat_sostav.
        """
        user = deps.user()
        query = zapros.strip()
        if not query:
            raise ToolError("запрос пуст: напиши часть названия")
        if len(query) > QUERY_MAX:
            raise ToolError(f"запрос длиннее {QUERY_MAX} знаков")
        prices = user.has(*PRICE_ROLES)
        with deps.sessions() as session:
            entries = catalog.list_ingredients(session, query, "", SEARCH_LIMIT)
        return {
            "found": len(entries),
            "ingredients": [_ingredient_short(e, prices=prices) for e in entries],
        }

    @server.tool(annotations=READ_ONLY)
    def ingredient(id: Annotated[int, Field(ge=1, le=ID_MAX)]) -> dict[str, Any]:  # noqa: A002
        """Карточка позиции справочника: состав, КБЖУ на 100 г, вес штуки, есть ли карточка.

        Цены и потери (доли, 0.05 = 5 %) — только ролям с ценами. КБЖУ —
        данные из таблицы; не пересчитывай.
        """
        prices = prices_visible()
        with deps.sessions() as session:
            entry = catalog.get_ingredient(session, id)
        if entry is None:
            raise ToolError("позиция не найдена или удалена из справочника")
        return _ingredient_full(entry, prices=prices)

    @server.tool(annotations=READ_ONLY)
    def spisok_blyud(
        filtr: str = "",
        limit: Annotated[int, Field(ge=1, le=DISHES_MAX_LIMIT)] = DISHES_DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Блюда: id, название, UC (себестоимость), цена меню, маржа, число замечаний.

        ``filtr`` — часть названия. Для шефа, коммерции и разработчика.
        """
        deps.user(*PRICE_ROLES)
        with deps.sessions() as session:
            entries = catalog.list_dishes(session, filtr.strip())
        shown = entries[:limit]
        return {
            "total": len(entries),
            "shown": len(shown),
            "dishes": [_dish_row(e) for e in shown],
            "note": NO_RECALC,
        }

    @server.tool(annotations=READ_ONLY)
    def blyudo(id: Annotated[str, Field(min_length=1, max_length=64)]) -> dict[str, Any]:  # noqa: A002
        """Расчёт блюда по id (например B001): состав со стоимостью и долями, КБЖУ, замечания.

        Для шефа, коммерции и разработчика. Замечания объясняют, почему
        себестоимость такая, — прочти их.
        """
        deps.user(*PRICE_ROLES)
        with deps.sessions() as session:
            try:
                entry = catalog.get_dish(session, id)
            except catalog.DishLookupError as error:
                raise ToolError(error.detail) from error
        cost = entry.cost
        return {
            **_dish_row(entry),
            **_kbju(cost),
            "components": _components(cost),
            "warning_texts": list(cost.warnings),
            "note": NO_RECALC,
        }

    @server.tool(annotations=READ_ONLY)
    def poschitat_sostav(
        sostav: Annotated[list[CompositionItem], Field(min_length=1, max_length=100)],
    ) -> dict[str, Any]:
        """Посчитать состав «а если»: список {ingredient_id, grams} -> UC, выход, КБЖУ, замечания.

        Считает платформа тем же расчётом, что и блюда, по справочнику базы;
        граммы — нетто. Ничего не сохраняет. Только позиции справочника
        (упаковка не поддерживается). Не пересчитывай числа сам.
        """
        deps.user(*PRICE_ROLES)
        items = [(item.ingredient_id, item.grams) for item in sostav]
        with deps.sessions() as session:
            try:
                cost = catalog.calculate_composition(session, items)
            except catalog.UnknownIngredientError as error:
                ids = ", ".join(str(i) for i in error.missing)
                raise ToolError(f"нет в справочнике: id {ids}") from error
        return {
            "uc_rub": _num(cost.uc_rub),
            "output_grams": _num(cost.output_grams),
            **_kbju(cost),
            "components": _components(cost),
            "warning_texts": list(cost.warnings),
            "note": NO_RECALC,
        }

    @server.tool(annotations=READ_ONLY)
    def sverka() -> dict[str, Any]:
        """Список дел «Сверки»: карточки без пары в справочнике, кандидаты и доступные действия.

        Для шефа, коммерции и разработчика.
        """
        deps.user(*PRICE_ROLES)
        with deps.sessions() as session:
            overview = catalog.reconciliation_overview(session)
        return {
            "total": overview.total,
            "linked": overview.linked,
            "needs_human": overview.needs_human,
            "rows": [_sverka_row(i) for i in overview.items],
        }

    @server.tool(annotations=READ_ONLY)
    def svezhest() -> dict[str, Any]:
        """Насколько свежи данные: когда последний раз брали таблицы и нет ли причины «не перенесено»."""
        deps.user()
        with deps.sessions() as session:
            status = read_sync_status(session, deps.settings.sync_stale_after_seconds)
        return status.model_dump(mode="json")
