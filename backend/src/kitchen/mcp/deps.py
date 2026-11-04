"""Зависимости инструментов MCP: база, настройки, пользователь с ролями.

Инструмент получает ``McpDeps`` при регистрации и берёт из него всё нужное;
сам ничего не создаёт и сервер не правит.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mcp.server.mcpserver.exceptions import ToolError

from kitchen import identity
from kitchen.mcp.auth import current_subject

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from kitchen.config import Settings
    from kitchen.identity import CurrentUser

NOT_SIGNED_IN = "нужен вход"


@dataclass(frozen=True, slots=True)
class McpDeps:
    """Что нужно инструментам.

    ``subject`` — откуда взять id профиля текущего вызова; по умолчанию из
    токена запроса, тест подставляет своё.
    """

    sessions: sessionmaker[Session]
    settings: Settings
    subject: Callable[[], str | None] = current_subject

    def user(self, *roles: str) -> CurrentUser:
        """Пользователь вызова с ролями; при ``roles`` — ещё и проверка «нужна одна из».

        Отказ — ``ToolError`` с тем же текстом, что видит человек на сайте
        («нужна роль: …», «доступ отключён»): клиент покажет его модели и человеку.
        """
        subject = self.subject()
        if subject is None:
            raise ToolError(NOT_SIGNED_IN)
        try:
            with self.sessions() as session:
                user = identity.load_user(session, subject)
            return identity.require_roles(user, *roles) if roles else user
        except identity.IdentityError as error:
            raise ToolError(error.detail) from error
