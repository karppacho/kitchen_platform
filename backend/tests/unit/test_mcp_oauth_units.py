"""OAuth для MCP без базы: белый список адресов, вход через GoTrue, страницы."""

from __future__ import annotations

import httpx
import pytest

from kitchen.config import Settings
from kitchen.mcp import gotrue, pages
from kitchen.mcp.oauth import redirect_allowed
from kitchen.mcp.server import build_mcp
from tests.unit.test_web_auth import SECRET, make_token


def _settings(**over: object) -> Settings:
    return Settings(app_env="test", **over)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "https://chatgpt.com/connector/oauth/abc123",
        "http://localhost:54321/callback",
        "http://127.0.0.1:8080/cb",
        "http://[::1]:9000/cb",
    ],
)
def test_allowed_redirects(uri: str) -> None:
    assert redirect_allowed(uri, _settings())


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example/callback",
        "https://claude.ai.evil.example/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callback/../x",
        "https://claude.ai@evil.example/api/mcp/auth_callback",
        "http://claude.ai/api/mcp/auth_callback",
        "https://chatgpt.com/connector/oauthx",
        "https://localhost:1/cb",
        "http://localhost.evil.example/cb",
        "javascript:alert(1)",
        "",
    ],
)
def test_foreign_redirects(uri: str) -> None:
    assert not redirect_allowed(uri, _settings())


def test_loopback_can_be_switched_off() -> None:
    assert not redirect_allowed(
        "http://localhost:1/cb", _settings(mcp_allow_loopback_redirects=False)
    )


def test_allowlist_comes_from_settings() -> None:
    settings = _settings(mcp_redirect_allowlist=["https://other.example/cb"])
    assert redirect_allowed("https://other.example/cb", settings)
    assert not redirect_allowed("https://claude.ai/api/mcp/auth_callback", settings)


# ---------------------------------------------------------------------------
# GoTrue
# ---------------------------------------------------------------------------
def _login(status: int, body: object = None, anon: str = "anon") -> str:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/v1/token"
        assert request.url.params["grant_type"] == "password"
        assert request.headers["apikey"] == "anon"
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = _settings(
        supabase_url="http://supabase.test", supabase_anon_key=anon, supabase_jwt_secret=SECRET
    )
    return gotrue.password_login(client, settings, "a@b.c", "pw")


def test_login_returns_access_token() -> None:
    token = make_token()
    assert _login(200, {"access_token": token, "refresh_token": "r"}) == token


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (400, "Неверная почта или пароль"),
        (401, "Вход настроен неверно"),
        (403, "Вход настроен неверно"),
        (500, "Служба входа не отвечает"),
    ],
)
def test_login_failures_keep_site_texts(status: int, text: str) -> None:
    with pytest.raises(gotrue.LoginError) as error:
        _login(status, {})
    assert text in error.value.message


@pytest.mark.parametrize("body", [[], {"x": 1}, {"access_token": 5}, None])
def test_login_garbage_is_unavailable(body: object) -> None:
    with pytest.raises(gotrue.LoginError) as error:
        _login(200, body)
    assert "Служба входа не отвечает" in error.value.message


def test_login_without_anon_key_does_not_go_to_network() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("в сеть ходить нельзя")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(gotrue.LoginError) as error:
        gotrue.password_login(client, _settings(), "a@b.c", "pw")
    assert "Вход настроен неверно" in error.value.message


def test_login_network_error_is_unavailable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет связи")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = _settings(supabase_anon_key="anon")
    with pytest.raises(gotrue.LoginError) as error:
        gotrue.password_login(client, settings, "a@b.c", "pw")
    assert "Служба входа не отвечает" in error.value.message


# ---------------------------------------------------------------------------
# Страницы
# ---------------------------------------------------------------------------
def test_pages_escape_everything_from_outside() -> None:
    html = pages.login_page(
        txn='"><script>', csrf="c<", client_name="<i>x</i>", error="<b>e</b>", host="h"
    )
    assert "<script>" not in html
    assert "<i>x</i>" not in html
    assert "<b>e</b>" not in html
    assert "&lt;i&gt;x&lt;/i&gt;" in html


def test_consent_page_has_both_buttons() -> None:
    html = pages.consent_page(txn="t", csrf="c", client_name="Claude", person="Алексей", host="h")
    assert 'value="consent"' in html
    assert 'value="deny"' in html


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------
def test_mcp_outside_development_requires_public_url() -> None:
    """Без адреса OAuth не собрать, а токен разработчика вне разработки не пускает:
    сервер не должен подняться открытым или глухим молча."""
    with pytest.raises(RuntimeError, match="MCP_PUBLIC_URL"):
        build_mcp(None, _settings(mcp_enabled=True))  # type: ignore[arg-type]


def test_public_url_loses_trailing_slash() -> None:
    assert _settings(mcp_public_url="https://x.test/").mcp_public_url == "https://x.test"
