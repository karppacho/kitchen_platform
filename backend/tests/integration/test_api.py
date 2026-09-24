"""Ручки чтения на настоящей базе: удалённое скрыто, /api/sync отвечает."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from kitchen.config import Settings
from kitchen.sync import specs
from kitchen.sync.cycle import SyncCycle
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetsReader
from kitchen.web import auth
from kitchen.web.app import create_app
from tests.fake_sheets import IDS, cards_sheet, header, kitchen_sheets, row, sheets_client
from tests.integration.test_database import _url

pytestmark = pytest.mark.integration


@pytest.fixture
def client(sessions) -> TestClient:
    app = create_app(Settings(app_env="test", database_url=_url()))
    app.dependency_overrides[auth.current_user] = lambda: auth.CurrentUser(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        email="chef@example.com",
        display_name="Шеф",
        roles=frozenset({"chef"}),
    )
    return TestClient(app)


def _import(sessions, **sheets) -> None:
    Importer(SheetsReader(sheets_client(**sheets), IDS), sessions).run()


def test_removed_dish_hidden_from_list_and_gone_on_card(sessions, client) -> None:
    _import(sessions)
    _import(sessions, kitchen={"Блюда": [header(specs.DISHES)]})

    assert client.get("/api/dishes").json() == []
    reply = client.get("/api/dishes/B001")
    assert reply.status_code == 410
    assert "удалено из таблицы" in reply.json()["detail"]


def test_unknown_dish_is_still_404(sessions, client) -> None:
    _import(sessions)

    assert client.get("/api/dishes/B999").status_code == 404


def test_removed_ingredient_hidden_from_reference(sessions, client) -> None:
    _import(sessions)
    ing = kitchen_sheets()["ING"]
    _import(sessions, kitchen={"ING": [ing[0], ing[1], ing[3]]})

    assert {i["legacy_id"] for i in client.get("/api/ingredients").json()} == {"1", "3"}


def test_removed_card_leaves_reconciliation_and_has_card(sessions, client) -> None:
    _import(sessions)
    assert {i["legacy_id"]: i["has_card"] for i in client.get("/api/ingredients").json()}["1"]

    cards = [
        line for line in cards_sheet() if "Томаты" not in line and "Пастрами из индейки" not in line
    ]
    _import(sessions, cards=cards)

    summary = client.get("/api/reconciliation").json()
    assert summary["total"] == 1, "осталась одна карточка — «Сахар»"
    assert [r["name"] for r in summary["rows"]] == ["Сахар"]
    assert not {i["legacy_id"]: i["has_card"] for i in client.get("/api/ingredients").json()}["1"]


def test_removed_namesake_not_offered_on_reconciliation(sessions, client) -> None:
    """Шеф убрал из ING третий «Сахар» — карточка ещё спорная, между двумя.

    Удалённый тёзка в кандидаты не попадает: выбрать его значило бы привязать
    карточку к позиции, которой в листе больше нет.
    """
    ing = kitchen_sheets()["ING"]
    third = row(specs.INGREDIENTS, id="4", name="Сахар", price_per_kg="90", status="активное")
    _import(sessions, kitchen={"ING": [*ing, third]})
    _import(sessions)

    rows = client.get("/api/reconciliation").json()["rows"]
    sugar = next(r for r in rows if r["name"] == "Сахар")
    assert sugar["link_status"] == "ambiguous"
    assert {c["legacy_id"] for c in sugar["candidates"]} == {"2", "3"}


def test_sync_reports_both_books(sessions, client) -> None:
    SyncCycle(SheetsReader(sheets_client(), IDS), sessions).run()

    body = client.get("/api/sync").json()

    assert body["stale"] is False
    assert body["data_as_of"] is not None
    assert [(b["book"], b["problem"]) for b in body["books"]] == [
        ("kitchen", None),
        ("ingredient_cards", None),
    ]


def test_sync_before_first_cycle_is_stale(sessions, client) -> None:
    body = client.get("/api/sync").json()

    assert (body["data_as_of"], body["stale"]) == (None, True)
