"""Права ролей на ручках чтения.

Повар (``cook``) заводит карточки ингредиентов — и только. Справочник,
блюда, карточка блюда и сверка несут цены и маржу, а их повару сервер не
отдаёт. Спрятать раздел в меню мало: ручку можно открыть и напрямую, по
адресу, — поэтому отказ живёт на сервере.

Вошедший берётся подменой ``current_user``, как в интеграционных тестах:
здесь проверяются роли, а не разбор токена — его держит test_web_auth.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from kitchen.config import Settings
from kitchen.db import models
from kitchen.web import auth
from kitchen.web.app import create_app

# Ручки с ценами и маржой.
PRICED = ["/api/ingredients", "/api/dishes", "/api/dishes/B001", "/api/reconciliation"]


class FakeResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)


class FakeSession:
    """Дублёр сессии: отвечает по модели из запроса.

    В базе одно блюдо без состава, остальное пусто. Этого хватает, чтобы
    каждая ручка прошла до конца и ответила 200: условия запроса дублёр
    не разбирает, роли от них не зависят.
    """

    def __init__(self) -> None:
        dish = models.Dish(
            legacy_id="B001", name="Пицца", category="", status="", price_menu=None, components=[]
        )
        self._rows: dict[type, list[object]] = {models.Dish: [dish]}

    def _rows_of(self, statement: object) -> list[object]:
        entity = statement.column_descriptions[0]["entity"]  # type: ignore[attr-defined]
        return self._rows.get(entity, [])

    def scalar(self, statement: object) -> object | None:
        rows = self._rows_of(statement)
        return rows[0] if rows else None

    def scalars(self, statement: object) -> FakeResult:
        return FakeResult(self._rows_of(statement))

    def execute(self, _statement: object) -> FakeResult:
        return FakeResult([])


def client_as(*roles: str) -> TestClient:
    app = create_app(Settings(app_env="test"))  # type: ignore[call-arg]
    app.dependency_overrides[auth.current_user] = lambda: auth.CurrentUser(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        email="user@example.com",
        display_name="Пользователь",
        roles=frozenset(roles),
    )
    app.dependency_overrides[auth.get_session] = FakeSession
    return TestClient(app)


@pytest.mark.parametrize("path", PRICED)
def test_cook_gets_403_where_prices_are(path: str) -> None:
    reply = client_as("cook").get(path)

    assert reply.status_code == 403
    # Только отказ — ни строки данных.
    assert reply.json() == {"detail": "нужна роль: chef, commerce, developer"}


@pytest.mark.parametrize("role", ["chef", "commerce", "developer"])
@pytest.mark.parametrize("path", PRICED)
def test_price_roles_get_200(path: str, role: str) -> None:
    assert client_as(role).get(path).status_code == 200


@pytest.mark.parametrize("path", ["/api/me", "/api/sync"])
def test_cook_sees_himself_and_data_freshness(path: str) -> None:
    """Строка свежести данных висит над каждым экраном — и над экраном повара."""
    assert client_as("cook").get(path).status_code == 200


def test_cook_with_chef_role_sees_prices() -> None:
    """Роли складываются: шеф, которому дали ещё и роль повара, цены не теряет."""
    assert client_as("cook", "chef").get("/api/dishes").status_code == 200
