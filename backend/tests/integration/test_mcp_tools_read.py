"""Инструменты чтения MCP на настоящей базе и фальшивых листах.

Данные — фальшивые таблицы (``tests/fake_sheets.py``) через ``Importer``;
пользователь — профиль в базе с ролью. Сверка с сайтом: ответы инструментов
сравниваются с ответами ручек ``/api/*`` на тех же данных.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.server import MCPServer
from sqlalchemy import func, select

from kitchen.config import Settings
from kitchen.db import models
from kitchen.mcp import tools_read
from kitchen.mcp.deps import McpDeps
from kitchen.sync import specs
from kitchen.sync.cycle import SyncCycle
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetsReader
from kitchen.web import auth
from kitchen.web.app import create_app
from tests.fake_sheets import IDS, kitchen_sheets, sheets_client
from tests.integration.test_database import _url

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

CHEF = uuid.UUID("11111111-1111-1111-1111-111111111111")
COOK = uuid.UUID("22222222-2222-2222-2222-222222222222")
PRICE_WORDS = ("price", "cost", "uc_", "margin", "losses", "pack")
PRICE_IN_FIXTURE = "177"  # цена «Томатов» в tests/fake_sheets.py


def _ing_with_units() -> list[list[str]]:
    """ING фикстуры с единицей «кг»: без неё расчёт даёт замечание и ноль."""
    ing = kitchen_sheets()["ING"]
    unit = next(i for i, c in enumerate(specs.INGREDIENTS.columns) if c.field == "unit")
    return [ing[0], *[[*r[:unit], "кг", *r[unit + 1 :]] for r in ing[1:]]]


def _import(sessions: sessionmaker[Session], **sheets: Any) -> None:
    Importer(SheetsReader(sheets_client(**sheets), IDS), sessions).run()


def _profiles(sessions: sessionmaker[Session]) -> None:
    with sessions() as session, session.begin():
        for profile_id, email, role in (
            (CHEF, "chef@example.com", "chef"),
            (COOK, "cook@example.com", "cook"),
        ):
            profile = models.Profile(id=profile_id, email=email, display_name=role)
            profile.roles = [models.UserRole(profile_id=profile_id, role_code=role)]
            session.add(profile)


@pytest.fixture
def world(sessions: sessionmaker[Session]) -> sessionmaker[Session]:
    _import(sessions, kitchen={"ING": _ing_with_units()})
    _profiles(sessions)
    return sessions


def _call(sessions: sessionmaker[Session], who: uuid.UUID, name: str, args: dict[str, Any]) -> Any:
    deps = McpDeps(
        sessions=sessions,
        settings=Settings(app_env="test", database_url=_url()),  # type: ignore[call-arg]
        subject=lambda: str(who),
    )
    server = MCPServer("test")
    tools_read.register_read_tools(server, deps)

    async def run() -> Any:
        async with Client(server) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


@pytest.fixture
def site() -> TestClient:
    app = create_app(Settings(app_env="test", database_url=_url()))  # type: ignore[call-arg]
    app.dependency_overrides[auth.current_user] = lambda: auth.CurrentUser(
        id=CHEF, email="chef@example.com", display_name="Шеф", roles=frozenset({"chef"})
    )
    return TestClient(app)


def _ingredient_id(sessions: sessionmaker[Session], legacy_id: str) -> int:
    with sessions() as session:
        found = session.scalar(
            select(models.Ingredient.id).where(models.Ingredient.legacy_id == legacy_id)
        )
    assert found is not None
    return found


def test_dish_matches_site_handler(world: sessionmaker[Session], site: TestClient) -> None:
    web = site.get("/api/dishes/B001").json()

    tool = _call(world, CHEF, "blyudo", {"id": "B001"})

    assert tool.is_error is False
    body = tool.structured_content
    for key in ("uc_rub", "uc_percent", "margin_percent", "output_grams", "price_menu", "kcal"):
        assert body[key] == web[key], key
    assert body["kbju_coverage"] == web["kbju_coverage"]
    assert body["warning_texts"] == web["warning_texts"]
    assert body["warnings"] == web["warnings"]
    assert body["components"] == web["components"]


def test_dish_list_matches_site_handler(world: sessionmaker[Session], site: TestClient) -> None:
    web = site.get("/api/dishes").json()

    body = _call(world, CHEF, "spisok_blyud", {}).structured_content

    assert body["total"] == body["shown"] == len(web) == 1
    dish = body["dishes"][0]
    assert dish["id"] == web[0]["legacy_id"]
    for key in ("name", "uc_rub", "uc_percent", "margin_percent", "price_menu", "warnings"):
        assert dish[key] == web[0][key], key
    assert (
        _call(world, CHEF, "spisok_blyud", {"filtr": "нет такого"}).structured_content["dishes"]
        == []
    )


def test_composition_gives_same_uc_as_dish(world: sessionmaker[Session]) -> None:
    dish = _call(world, CHEF, "blyudo", {"id": "B001"}).structured_content
    tomato = _ingredient_id(world, "1")

    calc = _call(
        world, CHEF, "poschitat_sostav", {"sostav": [{"ingredient_id": tomato, "grams": "100"}]}
    ).structured_content

    assert calc["uc_rub"] == dish["uc_rub"]
    assert Decimal(calc["output_grams"]) == Decimal(dish["output_grams"])
    assert Decimal(dish["uc_rub"]) == Decimal("17.70")
    assert Decimal(calc["kcal"]) == Decimal(dish["kcal"])


def test_composition_unknown_ingredient_and_no_writes(world: sessionmaker[Session]) -> None:
    def counts() -> tuple[int, int, int]:
        with world() as session:
            return tuple(  # type: ignore[return-value]
                session.scalar(select(func.count()).select_from(model)) or 0
                for model in (models.Ingredient, models.Dish, models.SheetWrite)
            )

    before = counts()
    bad = _call(
        world, CHEF, "poschitat_sostav", {"sostav": [{"ingredient_id": 99999, "grams": "5"}]}
    )
    good = _call(
        world,
        CHEF,
        "poschitat_sostav",
        {"sostav": [{"ingredient_id": _ingredient_id(world, "2"), "grams": "250.5"}]},
    )

    assert bad.is_error is True
    assert "99999" in bad.content[0].text
    assert good.is_error is False
    assert Decimal(good.structured_content["uc_rub"]) == Decimal("25.05")
    assert counts() == before


def test_reconciliation_matches_site_handler(
    world: sessionmaker[Session], site: TestClient
) -> None:
    web = site.get("/api/reconciliation").json()

    body = _call(world, CHEF, "sverka", {}).structured_content

    assert (body["total"], body["linked"], body["needs_human"]) == (
        web["total"],
        web["linked"],
        web["needs_human"],
    )
    assert [(r["card_id"], r["name"], r["actions"]) for r in body["rows"]] == [
        (r["card_id"], r["name"], r["actions"]) for r in web["rows"]
    ]
    assert [r["candidates"] for r in body["rows"]] == [r["candidates"] for r in web["rows"]]


def test_freshness_matches_site_handler(sessions: sessionmaker[Session], site: TestClient) -> None:
    SyncCycle(SheetsReader(sheets_client(), IDS), sessions).run()
    _profiles(sessions)
    web = site.get("/api/sync").json()

    body = _call(sessions, COOK, "svezhest", {}).structured_content

    assert body["stale"] is False
    assert [(b["book"], b["problem"]) for b in body["books"]] == [
        (b["book"], b["problem"]) for b in web["books"]
    ]


def test_search_and_card_hide_removed_and_show_has_card(world: sessionmaker[Session]) -> None:
    found = _call(world, CHEF, "poisk_ingredienta", {"zapros": "сахар"}).structured_content
    assert {i["legacy_id"] for i in found["ingredients"]} == {"2", "3"}
    assert all(i["price_per_unit"] is not None for i in found["ingredients"])

    ing = _ing_with_units()
    _import(world, kitchen={"ING": [ing[0], ing[1], ing[3]]})

    found = _call(world, CHEF, "poisk_ingredienta", {"zapros": "сахар"}).structured_content
    assert {i["legacy_id"] for i in found["ingredients"]} == {"3"}
    gone = _call(world, CHEF, "ingredient", {"id": _ingredient_id(world, "2")})
    assert gone.is_error is True

    tomato = _call(world, CHEF, "ingredient", {"id": _ingredient_id(world, "1")}).structured_content
    assert tomato["name"] == "Томаты"
    assert Decimal(tomato["price_per_unit"]) == Decimal(177)
    assert tomato["has_card"] is True


def test_removed_and_unknown_dish_are_errors(world: sessionmaker[Session]) -> None:
    _import(world, kitchen={"Блюда": [[c.expected_header for c in specs.DISHES.columns]]})

    removed = _call(world, CHEF, "blyudo", {"id": "B001"})
    unknown = _call(world, CHEF, "blyudo", {"id": "B999"})

    assert "удалено из таблицы" in removed.content[0].text
    assert "не найдено" in unknown.content[0].text


def test_cook_never_sees_prices_in_any_tool(world: sessionmaker[Session]) -> None:
    """Перебор всех инструментов: повару закрыты четыре, у трёх остальных цен нет
    ни в ключах, ни в значениях."""
    tomato = _ingredient_id(world, "1")
    calls: dict[str, dict[str, Any]] = {
        "poisk_ingredienta": {"zapros": "т"},
        "ingredient": {"id": tomato},
        "svezhest": {},
        "spisok_blyud": {},
        "blyudo": {"id": "B001"},
        "poschitat_sostav": {"sostav": [{"ingredient_id": tomato, "grams": "100"}]},
        "sverka": {},
    }
    open_to_cook = {"poisk_ingredienta", "ingredient", "svezhest"}

    for name, args in calls.items():
        result = _call(world, COOK, name, args)
        if name not in open_to_cook:
            assert result.is_error is True, name
            assert "нужна роль" in result.content[0].text, name
            continue
        assert result.is_error is False, name
        assert result.structured_content, name
        text = result.content[0].text
        assert not [w for w in PRICE_WORDS if f'"{w}' in text or f"_{w}" in text], name
        assert PRICE_IN_FIXTURE not in text, name
