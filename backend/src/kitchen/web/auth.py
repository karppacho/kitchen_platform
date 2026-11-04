"""Кто вошёл и что ему можно — обёртка FastAPI.

Проверка токена, профиль и роли живут ниже, в ``kitchen.identity`` (их же
использует MCP). Здесь — только то, что про HTTP: откуда взять токен (заголовок
или кука), как превратить отказ в 401/403 и зависимости для ручек.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from kitchen import identity
from kitchen.config import Settings
from kitchen.identity import CurrentUser

# Settings и Session импортируются в РАНТАЙМЕ, а не под TYPE_CHECKING.
# Из-за `from __future__ import annotations` аннотации становятся строками,
# и FastAPI не может разрешить типы зависимостей. Симптом коварный: модуль
# импортируется нормально, а падает сборка приложения — PydanticUserError
# «is not fully defined».

# Имена кук. Фронтенд их не читает — они httpOnly, — но бэкенд и тесты
# должны называть их из одного места.
ACCESS_COOKIE = "kp_access"
REFRESH_COOKIE = "kp_refresh"

bearer = HTTPBearer(auto_error=False)

__all__ = [
    "ACCESS_COOKIE",
    "REFRESH_COOKIE",
    "AuthError",
    "CurrentUser",
    "CurrentUserDep",
    "SessionDep",
    "current_user",
    "decode_token",
    "get_session",
    "get_settings",
    "load_user",
    "require",
]


class AuthError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def _http_error(error: identity.IdentityError) -> HTTPException:
    """401 — не представился, 403 — представился, но нельзя."""
    if isinstance(error, identity.AccessDeniedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error.detail)
    return AuthError(error.detail)


def decode_token(token: str, settings: Settings) -> dict[str, object]:
    """Проверка токена из ``identity`` с отказом в виде HTTP-ошибки."""
    try:
        return identity.decode_token(token, settings)
    except identity.IdentityError as error:
        raise _http_error(error) from error


def load_user(session: Session, subject: str) -> CurrentUser:
    """Профиль и роли из ``identity`` с отказом в виде HTTP-ошибки."""
    try:
        return identity.load_user(session, subject)
    except identity.IdentityError as error:
        raise _http_error(error) from error


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    """Сессия базы на время запроса.

    Открывается и закрывается вместе с запросом: держать её дольше значит
    занимать соединение пулера, пока никто ничего не спрашивает.
    """
    with request.app.state.sessions() as session:
        yield session


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> CurrentUser:
    """Кто пришёл: по заголовку или по куке.

    Заголовок проверяется первым и побеждает. Им пользуются curl, скрипты
    выдачи доступа и тесты; кука — способ браузера, которому токен в руки
    давать нельзя.
    """
    token = credentials.credentials if credentials else request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise AuthError("нужен токен")
    payload = decode_token(token, settings)
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise AuthError("токен недействителен")
    return load_user(session, subject)


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
SessionDep = Annotated[Session, Depends(get_session)]


def require(*roles: str) -> Callable[[CurrentUser], CurrentUser]:
    """Зависимость «нужна одна из этих ролей».

    Проверка ролей отделена от проверки токена намеренно: 401 значит «не
    представился», 403 — «представился, но нельзя». Смешивать их значит
    заставлять пользователя гадать, что чинить.
    """

    def guard(user: CurrentUserDep) -> CurrentUser:
        try:
            return identity.require_roles(user, *roles)
        except identity.IdentityError as error:
            raise _http_error(error) from error

    return guard
