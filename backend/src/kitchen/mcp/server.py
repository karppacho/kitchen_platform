"""Сборка MCP-сервера платформы.

Файл не меняется при добавлении инструментов и входа: инструменты
регистрируют ``tools_read`` и ``tools_cards`` (получают ``McpDeps``), доступ
решает ``auth.build_access``. Здесь — только склейка и защита транспорта.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from kitchen.mcp.auth import build_access
from kitchen.mcp.deps import McpDeps
from kitchen.mcp.tools_cards import register_card_tools
from kitchen.mcp.tools_read import register_read_tools

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.applications import Starlette

    from kitchen.config import Settings

SERVER_NAME = "Kitchen Platform"
# Путь внутри приложения SDK. Приложение монтируется в корень, чтобы не было
# редиректа 307 с /mcp на /mcp/ (клиенты его не любят).
MCP_PATH = "/mcp"


def build_mcp(sessions: sessionmaker[Session], settings: Settings) -> MCPServer:
    access = build_access(settings, sessions)
    server = MCPServer(
        SERVER_NAME,
        auth=access.auth_settings,
        token_verifier=access.token_verifier,
        auth_server_provider=access.auth_server_provider,
    )
    deps = McpDeps(sessions=sessions, settings=settings)
    register_read_tools(server, deps)
    register_card_tools(server, deps)
    access.install(server)
    return server


def build_mcp_app(server: MCPServer, settings: Settings) -> Starlette:
    """ASGI-приложение Streamable HTTP без состояния; Host проверяется по списку."""
    return server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.mcp_allowed_hosts),
            allowed_origins=[f"https://{host}" for host in settings.mcp_allowed_hosts],
        ),
    )
