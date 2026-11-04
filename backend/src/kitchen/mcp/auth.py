"""Доступ к MCP: кто пришёл с запросом.

Единственное место, которое знает, как проверяется вход. ``server.py`` лишь
вызывает :func:`build_access` и подставляет результат в ``MCPServer``. Сегодня
здесь временный вход «токен разработчика из настроек»; M3 заменит его
своим OAuth 2.1, правя только этот файл (провайдер, страницы входа через
``install``, субъект токена — id профиля).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, OAuthAuthorizationServerProvider, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server import MCPServer
    from sqlalchemy.orm import Session, sessionmaker

    from kitchen.config import Settings

# Кому выдан токен разработчика, в ``client_id``.
DEV_CLIENT_ID = "dev"
# Адрес издателя и ресурса в метаданных; настоящие значения придут с OAuth (M3).
# Токен разработчика по сети никому не выдаётся, адрес только заполняет схему SDK.
_LOCAL_URL = "http://127.0.0.1"


def _no_install(_server: MCPServer) -> None:
    """Навешивать на сервер нечего."""


@dataclass(frozen=True, slots=True)
class McpAccess:
    """Всё, что ``MCPServer`` берёт у проверки доступа.

    ``auth_settings`` и один из ``token_verifier`` / ``auth_server_provider``
    уходят в конструктор сервера; ``install`` — крючок для того, что нужно
    навесить на сервер (страницы входа через ``server.custom_route``).
    """

    auth_settings: AuthSettings
    token_verifier: TokenVerifier | None = None
    auth_server_provider: OAuthAuthorizationServerProvider[Any, Any, Any] | None = None
    install: Callable[[MCPServer], None] = _no_install


class DevTokenVerifier:
    """Пускает ровно один токен — из настроек. Пустая настройка не пускает никого."""

    def __init__(self, settings: Settings) -> None:
        self._token = settings.mcp_dev_token.get_secret_value()
        self._subject = settings.mcp_dev_user_id or None

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self._token or not token:
            return None
        # Сравнение за постоянное время: токен — секрет.
        if not hmac.compare_digest(token.encode(), self._token.encode()):
            return None
        return AccessToken(token=token, client_id=DEV_CLIENT_ID, scopes=[], subject=self._subject)


def build_access(settings: Settings, sessions: sessionmaker[Session]) -> McpAccess:
    """Выбрать проверку доступа. ``sessions`` понадобится OAuth (M3): токены в базе."""
    auth_settings = AuthSettings(
        issuer_url=AnyHttpUrl(_LOCAL_URL),
        resource_server_url=AnyHttpUrl(_LOCAL_URL),
        # Токен разработчика не привязан к ресурсу; с OAuth (M3) включить True.
        validate_token_resource=False,
    )
    return McpAccess(auth_settings=auth_settings, token_verifier=DevTokenVerifier(settings))


def current_subject() -> str | None:
    """Идентификатор профиля из токена текущего запроса; вне запроса — ``None``."""
    token = get_access_token()
    return token.subject if token else None
