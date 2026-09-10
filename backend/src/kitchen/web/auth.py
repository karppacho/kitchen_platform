"""Кто вошёл и что ему можно.

Учётки, пароли и выдачу токенов ведёт Supabase Auth (GoTrue). Наше дело —
проверить подпись пришедшего токена и сопоставить его с профилем: роли
живут у нас, потому что они про предметную область, а не про вход.

Проверяем **подпись, а не наличие**. Токен без проверки — это утверждение
пользователя о самом себе; звучит очевидно, но именно так обычно и
появляется дыра.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from kitchen.config import Settings
from kitchen.db import models

# Settings и Session импортируются в РАНТАЙМЕ, а не под TYPE_CHECKING.
# Из-за `from __future__ import annotations` аннотации становятся строками,
# и FastAPI не может разрешить типы зависимостей. Симптом коварный: модуль
# импортируется нормально, а падает сборка приложения — PydanticUserError
# «is not fully defined».

# Токены GoTrue подписаны HS256 общим секретом. Список закрытый: принимать
# алгоритм из самого токена — классическая дыра, вплоть до `none`.
ALGORITHMS = ["HS256"]
AUDIENCE = "authenticated"

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Вошедший пользователь."""

    id: uuid.UUID
    email: str
    display_name: str
    roles: frozenset[str]

    def has(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))


class AuthError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def decode_token(token: str, settings: Settings) -> dict[str, object]:
    """Разобрать и проверить токен.

    Причина отказа наружу не уточняется: «подпись не сошлась» и «срок
    истёк» вместе рассказывают о нашем устройстве больше, чем нужно тому,
    кто подбирает.
    """
    secret = settings.supabase_jwt_secret.get_secret_value()
    if not secret:
        # Пустой секрет молча пропускал бы любые токены.
        raise AuthError("вход не настроен")
    try:
        payload: dict[str, object] = jwt.decode(
            token,
            secret,
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as error:
        raise AuthError("токен недействителен") from error
    return payload


def load_user(session: Session, subject: str) -> CurrentUser:
    """Профиль и роли по идентификатору из токена.

    Действующий токен без профиля — не ошибка пользователя, а недоделка
    администратора: учётка в Supabase есть, а в нашей таблице её не
    завели. Отвечаем 403, а не 401: входить он умеет, доступа нет.
    """
    try:
        profile_id = uuid.UUID(subject)
    except ValueError as error:
        raise AuthError("токен недействителен") from error

    profile = session.scalar(
        select(models.Profile)
        .options(selectinload(models.Profile.roles))
        .where(models.Profile.id == profile_id)
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="профиль не заведён — обратитесь к администратору",
        )
    if not profile.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="доступ отключён")

    return CurrentUser(
        id=profile.id,
        email=profile.email,
        display_name=profile.display_name,
        roles=frozenset(link.role_code for link in profile.roles),
    )


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
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> CurrentUser:
    if credentials is None:
        raise AuthError("нужен токен")
    payload = decode_token(credentials.credentials, settings)
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
        if not user.has(*roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"нужна роль: {', '.join(sorted(roles))}",
            )
        return user

    return guard
