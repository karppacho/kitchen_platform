"""Свой OAuth 2.1 для MCP на настоящей базе: DCR, PKCE, вход паролем, токены хешами.

Приложение собирается настоящим ``create_app``; GoTrue — ``httpx.MockTransport``
(как в ``test_web_auth``), база — Postgres с миграциями. Вся «сеть» идёт через
``TestClient``: регистрация клиента, ``/authorize`` и страница входа, обмен кода,
вызов ``/mcp`` с выданным токеном, продление, отзыв.

Что стерегут тесты:

* код живёт один раз; повтор — отказ и отзыв выданного по нему;
* PKCE: чужой ``code_verifier`` не принимается;
* продление вращается: старый refresh мёртв, а его повтор гасит всю цепочку;
* ``redirect_uri`` — только из белого списка и только зарегистрированный;
* форма входа защищена от подделки (кука + поле + привязка в базе);
* в базе нет ни одного кода и токена в открытом виде.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server import MCPServer
from sqlalchemy import select, text, update

from kitchen.config import Settings
from kitchen.db import models
from kitchen.mcp import gotrue
from kitchen.mcp import server as mcp_server
from kitchen.mcp.deps import McpDeps
from kitchen.web.app import create_app
from tests.integration.test_database import _url
from tests.unit.test_web_auth import PROFILE_ID, SECRET, make_token

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

HOST = "testserver"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "https://kitchen.test/mcp"
PASSWORD = "верный-пароль"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "database_url": _url(),
        "mcp_enabled": True,
        "mcp_allowed_hosts": [HOST],
        "mcp_public_url": "https://kitchen.test",
        "supabase_url": "http://supabase.test",
        "supabase_anon_key": "anon-key-test",
        "supabase_jwt_secret": SECRET,
        # TestClient ходит по http: кука с Secure по нему не вернулась бы.
        "session_cookie_secure": False,
    }
    return Settings(**{**base, **over})  # type: ignore[arg-type]


def _gotrue(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body.get("password") != PASSWORD:
        return httpx.Response(400, json={"error": "invalid_grant"})
    return httpx.Response(
        200,
        json={
            "access_token": make_token(PROFILE_ID),
            "refresh_token": "gotrue-refresh",
            "expires_in": 3600,
        },
    )


@pytest.fixture
def chef(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        profile = models.Profile(id=PROFILE_ID, email="chef@example.com", display_name="Алексей")
        profile.roles = [models.UserRole(profile_id=PROFILE_ID, role_code="chef")]
        session.add(profile)


@pytest.fixture
def make_client(chef: None, monkeypatch: pytest.MonkeyPatch) -> Callable[..., TestClient]:
    def build(**over: Any) -> TestClient:
        monkeypatch.setattr(
            gotrue, "make_http_client", lambda: httpx.Client(transport=httpx.MockTransport(_gotrue))
        )
        return TestClient(create_app(_settings(**over)))

    return build


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> Any:
    with make_client() as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Помощники потока
# ---------------------------------------------------------------------------
def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def register(client: TestClient, **over: Any) -> dict[str, Any]:
    body = {
        "client_name": "Claude",
        "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "client_secret_post",
        **over,
    }
    reply = client.post("/register", json=body)
    assert reply.status_code == 201, reply.text
    info: dict[str, Any] = reply.json()
    return info


def authorize(
    client: TestClient, info: dict[str, Any], challenge: str, **over: Any
) -> httpx.Response:
    params = {
        "response_type": "code",
        "client_id": info["client_id"],
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "state-1",
        "resource": RESOURCE,
        **over,
    }
    return client.get("/authorize", params=params, follow_redirects=False)


def field(html: str, name: str) -> str:
    found = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert found, f"в странице нет поля {name}"
    return found.group(1)


def sign_in(client: TestClient, location: str, password: str = PASSWORD) -> httpx.Response:
    """Открыть страницу входа по ссылке из ``/authorize`` и отправить пароль."""
    page = client.get(location)
    assert page.status_code == 200, page.text
    return client.post(
        "/oauth/login",
        data={
            "step": "login",
            "txn": field(page.text, "txn"),
            "csrf": field(page.text, "csrf"),
            "email": "chef@example.com",
            "password": password,
        },
    )


def consent(client: TestClient, page: httpx.Response, step: str = "consent") -> httpx.Response:
    return client.post(
        "/oauth/login",
        data={"step": step, "txn": field(page.text, "txn"), "csrf": field(page.text, "csrf")},
        follow_redirects=False,
    )


def get_code(client: TestClient, info: dict[str, Any], challenge: str) -> str:
    """Весь путь человека: ``/authorize`` → вход → согласие → ``code`` из ссылки возврата."""
    started = authorize(client, info, challenge)
    assert started.status_code == 302, started.text
    after_login = sign_in(client, started.headers["location"])
    assert after_login.status_code == 200, after_login.text
    done = consent(client, after_login)
    assert done.status_code == 302, done.text
    target = urlsplit(done.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == REDIRECT
    query = parse_qs(target.query)
    assert query["state"] == ["state-1"]
    return query["code"][0]


def exchange(
    client: TestClient, info: dict[str, Any], code: str, verifier: str, **over: Any
) -> httpx.Response:
    return client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": info["client_id"],
            "client_secret": info["client_secret"],
            "code_verifier": verifier,
            **over,
        },
    )


def refresh(client: TestClient, info: dict[str, Any], token: str) -> httpx.Response:
    return client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token,
            "client_id": info["client_id"],
            "client_secret": info["client_secret"],
        },
    )


def tokens(client: TestClient, info: dict[str, Any]) -> dict[str, Any]:
    verifier, challenge = pkce()
    reply = exchange(client, info, get_code(client, info, challenge), verifier)
    assert reply.status_code == 200, reply.text
    body: dict[str, Any] = reply.json()
    return body


def mcp_status(client: TestClient, access: str) -> int:
    reply = client.post(
        "/mcp",
        content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
        headers={**MCP_HEADERS, "Authorization": f"Bearer {access}"},
    )
    return reply.status_code


# ---------------------------------------------------------------------------
# Метаданные
# ---------------------------------------------------------------------------
def test_metadata_is_published(client: TestClient) -> None:
    reply = client.get("/.well-known/oauth-authorization-server")

    assert reply.status_code == 200
    body = reply.json()
    assert body["issuer"].rstrip("/") == "https://kitchen.test"
    assert body["code_challenge_methods_supported"] == ["S256"]
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert body[key].startswith("https://kitchen.test/")
    assert "revocation_endpoint" in body
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200


def test_mcp_without_token_points_to_resource_metadata(client: TestClient) -> None:
    reply = client.post("/mcp", content="{}", headers=MCP_HEADERS)

    assert reply.status_code == 401
    assert "resource_metadata" in reply.headers["WWW-Authenticate"]


# ---------------------------------------------------------------------------
# Полный поток
# ---------------------------------------------------------------------------
def test_full_flow_gives_tokens_that_open_mcp(
    make_client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регистрация → вход → согласие → обмен кода → ``/mcp``; ``sub`` ведёт к ролям."""

    def probe(server: MCPServer, deps: McpDeps) -> None:
        @server.tool()
        def kto_ya() -> dict[str, object]:
            """Проверочный инструмент: кто вызвал."""
            user = deps.user()
            return {"email": user.email, "roles": sorted(user.roles)}

    monkeypatch.setattr(mcp_server, "register_read_tools", probe)
    with make_client() as client:
        info = register(client)

        pair = tokens(client, info)

        assert pair["token_type"] == "Bearer"
        assert pair["expires_in"] == 3600
        assert pair["access_token"] != pair["refresh_token"]
        reply = client.post(
            "/mcp",
            content=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "kto_ya", "arguments": {}},
                }
            ),
            headers={**MCP_HEADERS, "Authorization": f"Bearer {pair['access_token']}"},
        )
        assert reply.status_code == 200
        data = next(line for line in reply.text.splitlines() if line.startswith("data:"))
        result = json.loads(data.removeprefix("data:").strip())["result"]
        assert result["structuredContent"] == {"email": "chef@example.com", "roles": ["chef"]}


def test_consent_page_names_the_person_and_the_app(client: TestClient) -> None:
    info = register(client, client_name="Claude <b>тест</b>")
    started = authorize(client, info, pkce()[1])

    page = sign_in(client, started.headers["location"])

    assert "Алексей" in page.text
    assert "читать справочник и блюда" in page.text
    assert "создавать черновики карточек" in page.text
    assert "Claude &lt;b&gt;тест&lt;/b&gt;" in page.text
    assert "<b>тест</b>" not in page.text


def test_pages_forbid_framing_and_caching(client: TestClient) -> None:
    info = register(client)
    started = authorize(client, info, pkce()[1])

    page = client.get(started.headers["location"])

    assert page.headers["X-Frame-Options"] == "DENY"
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    cookie = page.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Path=/oauth" in cookie
    assert "SameSite=strict" in cookie


# ---------------------------------------------------------------------------
# Код и PKCE
# ---------------------------------------------------------------------------
def test_code_cannot_be_used_twice_and_first_tokens_die(client: TestClient) -> None:
    info = register(client)
    verifier, challenge = pkce()
    code = get_code(client, info, challenge)
    first = exchange(client, info, code, verifier)
    assert first.status_code == 200

    second = exchange(client, info, code, verifier)

    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"
    # Повтор кода — признак кражи: всё, что выдано по этому коду, отзывается.
    assert mcp_status(client, first.json()["access_token"]) == 401
    assert refresh(client, info, first.json()["refresh_token"]).status_code == 400


def test_wrong_code_verifier_is_refused(client: TestClient) -> None:
    info = register(client)
    _verifier, challenge = pkce()
    code = get_code(client, info, challenge)

    reply = exchange(client, info, code, pkce()[0])

    assert reply.status_code == 400
    assert reply.json()["error"] == "invalid_grant"


def test_code_expires_in_ten_minutes(client: TestClient, sessions: sessionmaker[Session]) -> None:
    info = register(client)
    verifier, challenge = pkce()
    code = get_code(client, info, challenge)
    with sessions.begin() as session:
        issued = session.scalar(select(models.McpOauthCode))
        assert issued is not None
        lifetime = issued.expires_at - issued.issued_at  # type: ignore[operator]
        session.execute(
            update(models.McpOauthCode).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    assert lifetime == timedelta(minutes=10)
    assert exchange(client, info, code, verifier).status_code == 400


def test_code_of_another_client_is_refused(client: TestClient) -> None:
    mine, other = register(client), register(client)
    verifier, challenge = pkce()
    code = get_code(client, mine, challenge)

    assert exchange(client, other, code, verifier).status_code == 400


# ---------------------------------------------------------------------------
# redirect_uri
# ---------------------------------------------------------------------------
def test_foreign_redirect_uri_is_refused_at_registration(client: TestClient) -> None:
    reply = client.post(
        "/register",
        json={"client_name": "x", "redirect_uris": ["https://evil.example/callback"]},
    )

    assert reply.status_code == 400
    assert reply.json()["error"] == "invalid_redirect_uri"


def test_loopback_redirect_is_allowed_for_claude_code(client: TestClient) -> None:
    reply = client.post(
        "/register",
        json={"client_name": "Claude Code", "redirect_uris": ["http://localhost:54321/callback"]},
    )

    assert reply.status_code == 201


def test_unregistered_redirect_uri_is_not_followed(client: TestClient) -> None:
    info = register(client)

    reply = authorize(
        client, info, pkce()[1], redirect_uri="https://claude.com/api/mcp/auth_callback"
    )

    assert reply.status_code == 400
    assert "location" not in reply.headers


def test_redirect_uri_removed_from_allowlist_is_refused(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    """Клиент зарегистрирован давно, а адрес из списка убрали — ``/authorize`` не пускает."""
    with sessions.begin() as session:
        session.add(
            models.McpOauthClient(
                client_id="old-client",
                client_secret=None,
                metadata_json={
                    "redirect_uris": ["https://old.example/cb"],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                },
            )
        )

    reply = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": "old-client",
            "redirect_uri": "https://old.example/cb",
            "code_challenge": pkce()[1],
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )

    # Ни кода, ни ошибки на выпавший из списка адрес: прямой отказ без переадресации.
    assert reply.status_code == 400
    assert "location" not in reply.headers


def test_resource_of_another_server_is_refused(client: TestClient) -> None:
    info = register(client)

    reply = authorize(client, info, pkce()[1], resource="https://other.example/mcp")

    assert reply.status_code == 302
    assert "error=invalid_target" in reply.headers["location"]


# ---------------------------------------------------------------------------
# Продление, отзыв, срок
# ---------------------------------------------------------------------------
def test_refresh_rotates_and_old_refresh_is_dead(client: TestClient) -> None:
    info = register(client)
    first = tokens(client, info)

    second = refresh(client, info, first["refresh_token"])

    assert second.status_code == 200
    renewed = second.json()
    assert renewed["refresh_token"] != first["refresh_token"]
    assert renewed["access_token"] != first["access_token"]
    assert mcp_status(client, renewed["access_token"]) == 200
    old = refresh(client, info, first["refresh_token"])
    assert old.status_code == 400
    assert old.json()["error"] == "invalid_grant"


def test_replayed_refresh_kills_the_whole_chain(client: TestClient) -> None:
    """Старый refresh предъявили снова — кто-то из двоих чужой: гасим цепочку."""
    info = register(client)
    first = tokens(client, info)
    renewed = refresh(client, info, first["refresh_token"]).json()

    refresh(client, info, first["refresh_token"])

    assert refresh(client, info, renewed["refresh_token"]).status_code == 400
    assert mcp_status(client, renewed["access_token"]) == 401


def test_revoke_kills_access_and_refresh(client: TestClient) -> None:
    info = register(client)
    pair = tokens(client, info)

    reply = client.post(
        "/revoke",
        data={
            "token": pair["access_token"],
            "client_id": info["client_id"],
            "client_secret": info["client_secret"],
        },
    )

    assert reply.status_code == 200
    assert mcp_status(client, pair["access_token"]) == 401
    assert refresh(client, info, pair["refresh_token"]).status_code == 400


def test_foreign_client_cannot_revoke_my_token(client: TestClient) -> None:
    mine, other = register(client), register(client)
    pair = tokens(client, mine)

    client.post(
        "/revoke",
        data={
            "token": pair["access_token"],
            "client_id": other["client_id"],
            "client_secret": other["client_secret"],
        },
    )

    assert mcp_status(client, pair["access_token"]) == 200


def test_expired_access_gives_401(client: TestClient, sessions: sessionmaker[Session]) -> None:
    info = register(client)
    pair = tokens(client, info)
    assert mcp_status(client, pair["access_token"]) == 200
    with sessions.begin() as session:
        session.execute(
            update(models.McpOauthToken)
            .where(models.McpOauthToken.kind == "access")
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    assert mcp_status(client, pair["access_token"]) == 401


def test_access_lives_one_hour_and_refresh_longer(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    tokens(client, register(client))

    with sessions() as session:
        rows = {row.kind: row for row in session.scalars(select(models.McpOauthToken))}

    assert rows["access"].expires_at - rows["access"].created_at <= timedelta(hours=1, seconds=5)
    assert rows["access"].expires_at - rows["access"].created_at >= timedelta(minutes=59)
    assert rows["refresh"].expires_at > rows["access"].expires_at


def test_disabled_profile_loses_access_at_once(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    """Токен жив, но человека отключили — вызов инструмента откажет (``deps.user``);
    а вход в ``/authorize`` для него закрыт уже на странице."""
    with sessions.begin() as session:
        session.execute(update(models.Profile).values(is_active=False))
    info = register(client)
    started = authorize(client, info, pkce()[1])

    page = sign_in(client, started.headers["location"])

    assert page.status_code == 403
    assert "доступ отключён" in page.text
    assert "Подключить" not in page.text


# ---------------------------------------------------------------------------
# Вход и согласие
# ---------------------------------------------------------------------------
def test_wrong_password_issues_no_code(client: TestClient, sessions: sessionmaker[Session]) -> None:
    info = register(client)
    started = authorize(client, info, pkce()[1])

    page = sign_in(client, started.headers["location"], password="неверный")

    assert page.status_code == 401
    assert "Неверная почта или пароль" in page.text
    with sessions() as session:
        pending = session.scalar(select(models.McpOauthCode))
        assert pending is not None
        assert pending.subject is None
        assert pending.issued_at is None


def test_login_attempts_per_request_are_limited(client: TestClient) -> None:
    info = register(client)
    started = authorize(client, info, pkce()[1])
    location = started.headers["location"]

    for _ in range(5):
        sign_in(client, location, password="неверный")
    page = client.get(location)

    assert page.status_code == 400
    assert "Запрос устарел" in page.text


def test_unknown_request_gives_error_page(client: TestClient) -> None:
    reply = client.get("/oauth/login", params={"txn": "нет-такого"})

    assert reply.status_code == 400
    assert "Запрос устарел" in reply.text


def test_consent_without_login_is_refused(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    """Пропустить вход и сразу согласиться нельзя."""
    info = register(client)
    started = authorize(client, info, pkce()[1])
    page = client.get(started.headers["location"])

    reply = consent(client, page)

    assert reply.status_code == 400
    with sessions() as session:
        assert session.scalar(select(models.McpOauthCode.issued_at)) is None


def test_form_without_csrf_cookie_is_refused(client: TestClient) -> None:
    info = register(client)
    started = authorize(client, info, pkce()[1])
    page = client.get(started.headers["location"])
    csrf = field(page.text, "csrf")
    client.cookies.clear()

    reply = client.post(
        "/oauth/login",
        data={
            "step": "login",
            "txn": field(page.text, "txn"),
            "csrf": csrf,
            "email": "chef@example.com",
            "password": PASSWORD,
        },
    )

    assert reply.status_code == 403


def test_form_with_wrong_csrf_field_is_refused(client: TestClient) -> None:
    info = register(client)
    started = authorize(client, info, pkce()[1])
    page = client.get(started.headers["location"])

    reply = client.post(
        "/oauth/login",
        data={
            "step": "login",
            "txn": field(page.text, "txn"),
            "csrf": "подделка",
            "email": "chef@example.com",
            "password": PASSWORD,
        },
    )

    assert reply.status_code == 403


def test_consent_with_csrf_of_another_request_is_refused(client: TestClient) -> None:
    """Кука и поле совпадают, но принадлежат не этому запросу — привязка в базе не даёт пройти."""
    info = register(client)
    first = authorize(client, info, pkce()[1])
    second = authorize(client, info, pkce()[1])
    page_one = sign_in(client, first.headers["location"])
    foreign = client.get(second.headers["location"])
    client.cookies.set("kitchen_mcp_csrf", field(foreign.text, "csrf"), path="/oauth")

    reply = client.post(
        "/oauth/login",
        data={
            "step": "consent",
            "txn": field(page_one.text, "txn"),
            "csrf": field(foreign.text, "csrf"),
        },
        follow_redirects=False,
    )

    assert reply.status_code == 403


def test_deny_sends_access_denied_and_closes_request(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    info = register(client)
    started = authorize(client, info, pkce()[1])
    page = sign_in(client, started.headers["location"])

    reply = consent(client, page, step="deny")

    assert reply.status_code == 302
    assert reply.headers["location"].startswith(REDIRECT)
    query = parse_qs(urlsplit(reply.headers["location"]).query)
    assert query["error"] == ["access_denied"]
    assert query["state"] == ["state-1"]
    assert "code" not in query
    with sessions() as session:
        assert session.scalar(select(models.McpOauthCode)) is None


def test_pending_request_expires(client: TestClient, sessions: sessionmaker[Session]) -> None:
    info = register(client)
    started = authorize(client, info, pkce()[1])
    with sessions.begin() as session:
        session.execute(
            update(models.McpOauthCode).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    assert client.get(started.headers["location"]).status_code == 400


# ---------------------------------------------------------------------------
# Хранение
# ---------------------------------------------------------------------------
def test_database_holds_no_token_code_or_refresh_in_clear(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    info = register(client)
    verifier, challenge = pkce()
    started = authorize(client, info, challenge)
    txn = parse_qs(urlsplit(started.headers["location"]).query)["txn"][0]
    page = sign_in(client, started.headers["location"])
    done = consent(client, page)
    code = parse_qs(urlsplit(done.headers["location"]).query)["code"][0]
    pair = exchange(client, info, code, verifier).json()
    refreshed = refresh(client, info, pair["refresh_token"]).json()
    secrets_in_play = [
        txn,
        code,
        pair["access_token"],
        pair["refresh_token"],
        refreshed["access_token"],
        refreshed["refresh_token"],
        field(page.text, "csrf"),
    ]

    with sessions() as session:
        dump = []
        for table in ("mcp_oauth_codes", "mcp_oauth_tokens"):
            rows = session.execute(text(f"select * from {table}")).mappings().all()
            assert rows, table
            dump.extend(str(value) for row in rows for value in row.values())
        hashes = {
            *session.scalars(select(models.McpOauthToken.token_hash)),
            *session.scalars(select(models.McpOauthCode.code_hash)),
        }

    blob = "\n".join(dump)
    for secret in secrets_in_play:
        assert secret not in blob
    assert hashlib.sha256(code.encode()).hexdigest() in hashes
    assert hashlib.sha256(pair["access_token"].encode()).hexdigest() in hashes
    assert hashlib.sha256(refreshed["refresh_token"].encode()).hexdigest() in hashes
    assert all(len(item) == 64 for item in hashes)


# ---------------------------------------------------------------------------
# Реестр клиентов
# ---------------------------------------------------------------------------
def test_unfinished_clients_are_swept_on_next_registration(
    client: TestClient, sessions: sessionmaker[Session]
) -> None:
    done = register(client)
    tokens(client, done)
    stale = register(client)
    fresh = register(client)
    old = datetime.now(UTC) - timedelta(days=2)
    with sessions.begin() as session:
        session.execute(
            update(models.McpOauthClient)
            .where(models.McpOauthClient.client_id.in_([done["client_id"], stale["client_id"]]))
            .values(created_at=old)
        )

    register(client)

    with sessions() as session:
        left = set(session.scalars(select(models.McpOauthClient.client_id)))
    assert done["client_id"] in left
    assert fresh["client_id"] in left
    assert stale["client_id"] not in left


def test_registration_is_capped_when_too_many_unfinished(
    client: TestClient, sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from kitchen.mcp import oauth

    monkeypatch.setattr(oauth, "MAX_UNFINISHED_CLIENTS", 2)
    register(client)
    register(client)

    reply = client.post("/register", json={"client_name": "x", "redirect_uris": [REDIRECT]})

    assert reply.status_code == 400


# ---------------------------------------------------------------------------
# Токен разработчика
# ---------------------------------------------------------------------------
def test_dev_token_is_not_accepted_outside_development(
    make_client: Callable[..., TestClient],
) -> None:
    with make_client(
        app_env="production", mcp_dev_token="dev-token", mcp_dev_user_id=str(PROFILE_ID)
    ) as client:
        assert mcp_status(client, "dev-token") == 401


def test_dev_token_still_works_in_development(make_client: Callable[..., TestClient]) -> None:
    with make_client(
        app_env="development", mcp_dev_token="dev-token", mcp_dev_user_id=str(PROFILE_ID)
    ) as client:
        assert mcp_status(client, "dev-token") == 200
        assert mcp_status(client, "other-token") == 401


def test_ids_are_uuid_strings(client: TestClient, sessions: sessionmaker[Session]) -> None:
    """Субъект токена в базе — id профиля, а не e-mail."""
    tokens(client, register(client))
    with sessions() as session:
        subjects = set(session.scalars(select(models.McpOauthToken.subject)))
    assert subjects == {PROFILE_ID}
    assert isinstance(next(iter(subjects)), uuid.UUID)
