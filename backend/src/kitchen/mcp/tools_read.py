"""Инструменты чтения (задача M2). Пока заглушка: M2 заполняет только этот файл."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from kitchen.mcp.deps import McpDeps


def register_read_tools(server: MCPServer, deps: McpDeps) -> None:
    """Зарегистрировать инструменты чтения на ``server`` (с ``readOnlyHint``)."""
