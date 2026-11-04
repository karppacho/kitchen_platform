"""Каркас MCP: флаг, проверка доступа, точки расширения.

Приложение собирается настоящим ``create_app``; база подменена фальшивой
сессией (как в ``test_web_auth``), HTTP — ``TestClient`` с запущенным
lifespan: менеджер сессий SDK стартует именно там.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from kitchen.config import Settings
from kitchen.mcp import server as mcp_server
from kitchen.mcp.deps import McpDeps
from kitchen.web import app as app_module
from tests.unit.test_web_auth import PROFILE_ID, FakeSession, make_profile

DEV_TOKEN = "dev-token-for-tests"
HOST = "testserver"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "mcp_enabled": True,
        "mcp_allowed_hosts": [HOST],
        "mcp_dev_token": DEV_TOKEN,
        "mcp_dev_user_id": str(PROFILE_ID),
    }
    return Settings(**{**base, **over})  # type: ignore[arg-type]


def _build(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    roles: tuple[str, ...] = ("chef",),
) -> TestClient:
    fake = FakeSession(make_profile(roles=roles))

    @contextmanager
    def sessions() -> Iterator[FakeSession]:
        yield fake

    monkeypatch.setattr(app_module, "make_session_factory", lambda *_a, **_k: sessions)
    return TestClient(app_module.create_app(settings))


def _rpc(method: str, params: dict[str, Any] | None = None) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def _result(reply: Any) -> dict[str, Any]:
    """Ответ SDK приходит потоком событий: берём строку ``data:``."""
    for line in reply.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())["result"]
    return reply.json()["result"]


def _bearer(token: str) -> dict[str, str]:
    return {**HEADERS, "Authorization": f"Bearer {token}"}


def test_flag_off_mcp_is_not_mounted(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build(monkeypatch, _settings(mcp_enabled=False))

    with client:
        reply = client.post("/mcp", content=_rpc("tools/list"), headers=_bearer(DEV_TOKEN))

    assert reply.status_code == 404
    assert client.get("/healthz").status_code == 200


def test_flag_is_off_by_default() -> None:
    assert Settings(app_env="test").mcp_enabled is False  # type: ignore[call-arg]
    assert Settings(app_env="test").mcp_dev_token.get_secret_value() == ""  # type: ignore[call-arg]


def test_without_token_gives_401_with_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    with _build(monkeypatch, _settings()) as client:
        reply = client.post("/mcp", content=_rpc("tools/list"), headers=HEADERS)

    assert reply.status_code == 401
    assert reply.headers["WWW-Authenticate"].startswith("Bearer")


@pytest.mark.parametrize("token", ["foreign-token", ""])
def test_wrong_token_gives_401(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    with _build(monkeypatch, _settings()) as client:
        reply = client.post("/mcp", content=_rpc("tools/list"), headers=_bearer(token))

    assert reply.status_code == 401


@pytest.mark.parametrize("token", [DEV_TOKEN, "", "anything"])
def test_empty_dev_token_setting_lets_nobody_in(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    """Пустая настройка — «выключено», а не «пароль пустой»."""
    with _build(monkeypatch, _settings(mcp_dev_token="")) as client:
        reply = client.post("/mcp", content=_rpc("tools/list"), headers=_bearer(token))

    assert reply.status_code == 401


def test_dev_token_lists_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пока инструментов нет (заглушки M2/M4): список пуст, но ответ настоящий."""
    with _build(monkeypatch, _settings()) as client:
        reply = client.post("/mcp", content=_rpc("tools/list"), headers=_bearer(DEV_TOKEN))

    assert reply.status_code == 200
    assert _result(reply)["tools"] == []


def test_foreign_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Защита от DNS rebinding: чужой Host не пускается даже с верным токеном."""
    with _build(monkeypatch, _settings()) as client:
        reply = client.post(
            "/mcp",
            content=_rpc("tools/list"),
            headers={**_bearer(DEV_TOKEN), "Host": "evil.example"},
        )

    assert reply.status_code in {400, 403, 421}


def _probe_tools(
    role_check: tuple[str, ...],
) -> Callable[[MCPServer, McpDeps], None]:
    def register(server: MCPServer, deps: McpDeps) -> None:
        @server.tool()
        def kto_ya() -> dict[str, object]:
            """Проверочный инструмент теста: кто вызвал."""
            user = deps.user(*role_check)
            return {"email": user.email, "roles": sorted(user.roles)}

    return register


def _call(client: TestClient) -> dict[str, Any]:
    reply = client.post(
        "/mcp",
        content=_rpc("tools/call", {"name": "kto_ya", "arguments": {}}),
        headers=_bearer(DEV_TOKEN),
    )
    assert reply.status_code == 200
    return _result(reply)


def test_tool_gets_current_user_with_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Точка расширения: инструмент из ``register_read_tools`` видит пользователя и роли."""
    monkeypatch.setattr(mcp_server, "register_read_tools", _probe_tools(("chef",)))

    with _build(monkeypatch, _settings(), roles=("chef", "commerce")) as client:
        result = _call(client)

    assert result["isError"] is False
    assert result["structuredContent"] == {
        "email": "chef@example.com",
        "roles": ["chef", "commerce"],
    }


def test_tool_role_check_refuses_cook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "register_card_tools", _probe_tools(("chef",)))

    with _build(monkeypatch, _settings(), roles=("cook",)) as client:
        result = _call(client)

    assert result["isError"] is True
    assert "нужна роль: chef" in result["content"][0]["text"]


def test_tool_without_user_id_in_settings_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Токен разработчика без профиля — вызов инструмента отказывает."""
    monkeypatch.setattr(mcp_server, "register_read_tools", _probe_tools(("chef",)))

    with _build(monkeypatch, _settings(mcp_dev_user_id="")) as client:
        result = _call(client)

    assert result["isError"] is True
