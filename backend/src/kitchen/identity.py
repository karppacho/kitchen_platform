"""Кто вошёл и какие у него роли — без привязки к транспорту.

Учётки, пароли и выдачу токенов ведёт Supabase Auth (GoTrue). Наше дело —
проверить подпись пришедшего токена и сопоставить его с профилем: роли
живут у нас, потому что они про предметную область, а не про вход.

Модуль общий для сайта (``kitchen.web.auth``) и MCP (``kitchen.mcp``), поэтому
стоит ниже обоих и ничего не знает про FastAPI: отказы — свои исключения,
а в HTTP-ответы их превращает тонкая обёртка в каждом транспорте. Тексты
отказов те же, что видит человек на сайте.

Проверяем **подпись, а не наличие**. Токен без проверки — это утверждение
пользователя о самом себе; звучит очевидно, но именно так обычно и
появляется дыра.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from kitchen.config import Settings
from kitchen.db import models

# Токены GoTrue подписаны HS256 общим секретом. Список закрытый: принимать
# алгоритм из самого токена — классическая дыра, вплоть до `none`.
ALGORITHMS = ["HS256"]
AUDIENCE = "authenticated"


class IdentityError(Exception):
    """Отказ с текстом для человека."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class TokenError(IdentityError):
    """Не представился: токена нет, он не годится или не настроен вход (401)."""


class AccessDeniedError(IdentityError):
    """Представился, но нельзя: нет профиля, отключён, не хватает роли (403).

    401 и 403 не смешиваем: иначе пользователь гадает, что чинить.
    """


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Вошедший пользователь."""

    id: uuid.UUID
    email: str
    display_name: str
    roles: frozenset[str]

    def has(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))


def decode_token(token: str, settings: Settings) -> dict[str, object]:
    """Разобрать и проверить токен.

    Причина отказа наружу не уточняется: «подпись не сошлась» и «срок
    истёк» вместе рассказывают о нашем устройстве больше, чем нужно тому,
    кто подбирает.
    """
    secret = settings.supabase_jwt_secret.get_secret_value()
    if not secret:
        # Пустой секрет молча пропускал бы любые токены.
        raise TokenError("вход не настроен")
    try:
        payload: dict[str, object] = jwt.decode(
            token,
            secret,
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as error:
        raise TokenError("токен недействителен") from error
    return payload


def load_user(session: Session, subject: str) -> CurrentUser:
    """Профиль и роли по идентификатору из токена.

    Действующий токен без профиля — не ошибка пользователя, а недоделка
    администратора: учётка в Supabase есть, а в нашей таблице её не
    завели. Отказ ``AccessDeniedError`` (403), а не ``TokenError`` (401): входить
    он умеет, доступа нет.
    """
    try:
        profile_id = uuid.UUID(subject)
    except ValueError as error:
        raise TokenError("токен недействителен") from error

    profile = session.scalar(
        select(models.Profile)
        .options(selectinload(models.Profile.roles))
        .where(models.Profile.id == profile_id)
    )
    if profile is None:
        raise AccessDeniedError("профиль не заведён — обратитесь к администратору")
    if not profile.is_active:
        raise AccessDeniedError("доступ отключён")

    return CurrentUser(
        id=profile.id,
        email=profile.email,
        display_name=profile.display_name,
        roles=frozenset(link.role_code for link in profile.roles),
    )


def require_roles(user: CurrentUser, *roles: str) -> CurrentUser:
    """Пропустить, если у пользователя есть одна из ролей, иначе ``AccessDeniedError``."""
    if not user.has(*roles):
        raise AccessDeniedError(f"нужна роль: {', '.join(sorted(roles))}")
    return user
