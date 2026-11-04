"""Инструменты карточек и «Сверки» (задача M4). Пока заглушка: M4 заполняет только этот файл."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from kitchen.mcp.deps import McpDeps


def register_card_tools(server: MCPServer, deps: McpDeps) -> None:
    """Зарегистрировать инструменты записи (без ``readOnlyHint``) на ``server``."""
