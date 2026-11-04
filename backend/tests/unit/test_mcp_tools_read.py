"""Инструменты чтения MCP: роли, цены повару, формат чисел, ``readOnlyHint``.

Чтение базы подменено (``kitchen.cards.catalog``), HTTP не нужен: сервер
``MCPServer`` собирается вручную, а ``Client`` ходит к нему в памяти. Настоящая
база и настоящие листы — в ``tests/integration/test_mcp_tools_read.py``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from mcp import Client
from mcp.server import MCPServer

from kitchen.cards import catalog
from kitchen.cards.sync_status import BookRow, build_sync_status
from kitchen.config import Settings
from kitchen.db import models
from kitchen.domain.costs import calculate
from kitchen.domain.recipe import ROW_MAIN, Component, IngredientSpec, Recipe
from kitchen.mcp import tools_read
from kitchen.mcp.deps import McpDeps
from tests.unit.test_web_auth import PROFILE_ID, FakeSession, make_profile

EXPECTED_TOOLS = {
    "poisk_ingredienta",
    "ingredient",
    "spisok_blyud",
    "blyudo",
    "poschitat_sostav",
    "sverka",
    "svezhest",
}
CHEF_ONLY = {
    "spisok_blyud": {},
    "blyudo": {"id": "B001"},
    "poschitat_sostav": {"sostav": [{"ingredient_id": 1, "grams": "100"}]},
    "sverka": {},
}
PRICE_WORDS = ("price", "cost", "uc_", "margin", "losses", "pack")


def _ingredient_row() -> models.Ingredient:
    return models.Ingredient(
        id=7,
        legacy_id="7",
        name="Томаты",
        full_name="Томаты свежие",
        short_name="Томат",
        category="Овощи",
        manufacturer="",
        composition="томаты",
        protein=Decimal("1.1"),
        fat=Decimal("0.2"),
        carbs=Decimal("3.8"),
        kcal=Decimal("20"),
        price_per_kg=Decimal("177.50"),
        price_per_pack=Decimal("90"),
        unit="кг",
        weight_per_piece_g=None,
        losses_total=Decimal("0.1"),
        losses_unpacking=Decimal("0.05"),
        losses_cutting=Decimal("0.05"),
        losses_thermal=None,
        status="активное",
    )


def _cost() -> catalog.DishEntry:
    spec = IngredientSpec(
        key="7",
        name="Томаты",
        unit="кг",
        price_per_unit=Decimal("177.50"),
        protein_100g=Decimal("1.1"),
        fat_100g=Decimal("0.2"),
        carbs_100g=Decimal("3.8"),
        kcal_100g=Decimal("20"),
    )
    recipe = Recipe(
        key="B001",
        name="Салат",
        price_menu=Decimal("280"),
        components=(Component(ROW_MAIN, Decimal("100"), ingredient=spec),),
    )
    dish = models.Dish(legacy_id="B001", name="Салат", category="Холодное", status="активное")
    return catalog.DishEntry(dish=dish, cost=calculate(recipe))


@pytest.fixture
def fake_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = catalog.IngredientEntry(row=_ingredient_row(), has_card=True)
    monkeypatch.setattr(catalog, "list_ingredients", lambda *_a, **_k: [entry])
    monkeypatch.setattr(catalog, "get_ingredient", lambda _s, i: entry if i == 7 else None)
    monkeypatch.setattr(catalog, "list_dishes", lambda *_a, **_k: [_cost()])

    def get_dish(_session: object, legacy_id: str) -> catalog.DishEntry:
        if legacy_id != "B001":
            raise catalog.DishNotFoundError
        return _cost()

    monkeypatch.setattr(catalog, "get_dish", get_dish)
    monkeypatch.setattr(
        catalog, "calculate_composition", lambda _s, items: _cost().cost if items else None
    )
    monkeypatch.setattr(
        catalog,
        "reconciliation_overview",
        lambda _s: catalog.ReconciliationOverview(total=0, linked=0, needs_human=0, items=[]),
    )
    now = datetime(2026, 11, 1, tzinfo=UTC)
    monkeypatch.setattr(
        tools_read,
        "read_sync_status",
        lambda _s, _after: build_sync_status(
            [BookRow("kitchen", now, now, None, None)], now, timedelta(1)
        ),
    )


def _client(roles: tuple[str, ...]) -> Client:
    fake = FakeSession(make_profile(roles=roles))

    @contextmanager
    def sessions() -> Iterator[FakeSession]:
        yield fake

    deps = McpDeps(
        sessions=sessions,  # type: ignore[arg-type]
        settings=Settings(app_env="test"),  # type: ignore[call-arg]
        subject=lambda: str(PROFILE_ID),
    )
    server = MCPServer("test")
    tools_read.register_read_tools(server, deps)
    return Client(server)


def _call(roles: tuple[str, ...], name: str, arguments: dict[str, Any]) -> Any:
    async def run() -> Any:
        async with _client(roles) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(run())


def _keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield str(key)
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


def test_all_tools_registered_and_read_only() -> None:
    async def run() -> Any:
        async with _client(("chef",)) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(run())

    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert all(t.annotations is not None and t.annotations.read_only_hint is True for t in tools)


@pytest.mark.usefixtures("fake_catalog")
@pytest.mark.parametrize("name", sorted(CHEF_ONLY))
def test_cook_is_refused_dishes_composition_reconciliation(name: str) -> None:
    result = _call(("cook",), name, CHEF_ONLY[name])

    assert result.is_error is True
    assert "нужна роль" in result.content[0].text


@pytest.mark.usefixtures("fake_catalog")
@pytest.mark.parametrize(
    ("name", "arguments"),
    [("poisk_ingredienta", {"zapros": "том"}), ("ingredient", {"id": 7}), ("svezhest", {})],
)
def test_cook_gets_no_price_field_anywhere(name: str, arguments: dict[str, Any]) -> None:
    result = _call(("cook",), name, arguments)

    assert result.is_error is False
    keys = set(_keys(result.structured_content))
    assert keys, "ответ не пуст"
    assert not [k for k in keys if any(word in k for word in PRICE_WORDS)]
    assert "177.50" not in result.content[0].text


@pytest.mark.usefixtures("fake_catalog")
def test_cook_gets_card_without_prices_but_with_kbju() -> None:
    body = _call(("cook",), "ingredient", {"id": 7}).structured_content

    assert body["kcal_100g"] == "20"
    assert body["composition"] == "томаты"
    assert "177.50" not in str(body)


@pytest.mark.usefixtures("fake_catalog")
def test_chef_gets_prices_as_strings_and_null_for_empty() -> None:
    body = _call(("chef",), "ingredient", {"id": 7}).structured_content

    assert body["price_per_unit"] == "177.50"
    assert body["losses_total"] == "0.1"
    assert body["losses_thermal"] is None
    assert body["weight_per_piece_g"] is None
    found = _call(("commerce",), "poisk_ingredienta", {"zapros": "том"}).structured_content
    assert found["ingredients"][0]["price_per_unit"] == "177.50"


@pytest.mark.usefixtures("fake_catalog")
def test_dish_detail_has_numbers_as_strings_and_warning_texts() -> None:
    body = _call(("chef",), "blyudo", {"id": "B001"}).structured_content

    assert body["id"] == "B001"
    assert Decimal(body["uc_rub"]) == Decimal("17.75")
    assert isinstance(body["uc_rub"], str)
    assert isinstance(body["components"][0]["cost_rub"], str)
    assert body["warning_texts"] == []
    assert body["warnings"] == 0


@pytest.mark.usefixtures("fake_catalog")
def test_missing_dish_and_ingredient_are_tool_errors() -> None:
    assert _call(("chef",), "blyudo", {"id": "B404"}).is_error is True
    assert "не найден" in _call(("chef",), "ingredient", {"id": 8}).content[0].text


@pytest.mark.usefixtures("fake_catalog")
@pytest.mark.parametrize("query", ["", "   ", "x" * 101])
def test_search_rejects_empty_or_huge_query(query: str) -> None:
    assert _call(("chef",), "poisk_ingredienta", {"zapros": query}).is_error is True


@pytest.mark.usefixtures("fake_catalog")
@pytest.mark.parametrize(
    "sostav",
    [
        [],
        [{"ingredient_id": 1, "grams": "0"}],
        [{"ingredient_id": 1, "grams": "-5"}],
        [{"ingredient_id": 1, "grams": "NaN"}],
        [{"ingredient_id": 1, "grams": "Infinity"}],
        [{"ingredient_id": 1, "grams": "10000000"}],
        [{"ingredient_id": 0, "grams": "10"}],
        [{"ingredient_id": 1, "grams": "10", "price": "1"}],
        [{"ingredient_id": 1, "grams": "10"}] * 101,
    ],
)
def test_composition_input_is_validated(sostav: list[dict[str, Any]]) -> None:
    assert _call(("chef",), "poschitat_sostav", {"sostav": sostav}).is_error is True


@pytest.mark.usefixtures("fake_catalog")
def test_composition_accepts_float_grams_and_returns_calculation() -> None:
    result = _call(
        ("chef",), "poschitat_sostav", {"sostav": [{"ingredient_id": 7, "grams": 100.5}]}
    )

    assert result.is_error is False
    assert isinstance(result.structured_content["uc_rub"], str)


def test_cook_refused_even_before_catalog_is_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отказ по роли раньше, чем чтение: подмена, которую нельзя звать."""

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("база не должна читаться")

    for name in ("list_dishes", "get_dish", "calculate_composition", "reconciliation_overview"):
        monkeypatch.setattr(catalog, name, boom)

    for name, arguments in CHEF_ONLY.items():
        result = _call(("cook",), name, arguments)
        assert "нужна роль" in result.content[0].text


def test_unknown_user_is_refused() -> None:
    async def run() -> Any:
        fake = FakeSession(None)

        @contextmanager
        def sessions() -> Iterator[FakeSession]:
            yield fake

        deps = McpDeps(
            sessions=sessions,  # type: ignore[arg-type]
            settings=Settings(app_env="test"),  # type: ignore[call-arg]
            subject=lambda: str(uuid.uuid4()),
        )
        server = MCPServer("test")
        tools_read.register_read_tools(server, deps)
        async with Client(server) as client:
            return await client.call_tool("svezhest", {})

    assert asyncio.run(run()).is_error is True
