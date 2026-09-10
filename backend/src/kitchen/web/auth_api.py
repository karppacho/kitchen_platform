"""Вход, продление и выход.

Почему не напрямую в Supabase, как написано в первой редакции ТЗ: GoTrue
слушает 127.0.0.1:8000, наружу его никто не проксирует, а CSP
``default-src 'self'`` запретил бы браузеру такой запрос и при доступном
адресе. Поэтому в GoTrue ходит бэкенд, а браузер получает httpOnly-куки и
токена не видит вовсе.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr

from kitchen.config import Settings
from kitchen.web.api import Me
from kitchen.web.auth import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    AuthError,
    SessionDep,
    decode_token,
    get_settings,
    load_user,
)

router = APIRouter(prefix="/api/auth", tags=["вход"])

# Продление живёт месяц. Дольше — значит держать пропуск в браузере кухни
# бессрочно; короче — заставлять шефа вводить пароль каждую неделю.
REFRESH_MAX_AGE = 30 * 24 * 3600
GOTRUE_TIMEOUT = 10.0


class LoginBody(BaseModel):
    email: EmailStr
    password: str


@dataclass(frozen=True, slots=True)
class Tokens:
    access: str
    refresh: str
    expires_in: int


def get_http(request: Request) -> httpx.Client:
    client: httpx.Client = request.app.state.http
    return client


HttpDep = Annotated[httpx.Client, Depends(get_http)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def token_request(
    client: httpx.Client,
    settings: Settings,
    grant_type: str,
    payload: dict[str, str],
    denied: str,
) -> Tokens:
    """Сходить в GoTrue за парой токенов.

    Отказ по существу (400/401/403) и недоступность службы (всё остальное)
    разделены намеренно: спутать их значит заставить человека перебирать
    пароли, когда лежит Supabase.
    """
    try:
        reply = client.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
            params={"grant_type": grant_type},
            json=payload,
            headers={"apikey": settings.supabase_anon_key.get_secret_value()},
            timeout=GOTRUE_TIMEOUT,
        )
    except httpx.HTTPError as error:
        raise _unavailable() from error

    if reply.status_code in (400, 401, 403):
        raise AuthError(denied)
    if reply.status_code != 200:
        raise _unavailable()

    # На 200 GoTrue обязана прислать JSON-объект, но «обязана» — не «пришлёт»:
    # nginx-заглушка, пустое тело или обрыв на полпути дают то же самое 200 с
    # мусором. Разбор тела — тоже часть недоступности службы, а не наша
    # внутренняя ошибка.
    try:
        body = reply.json()
    except ValueError as error:
        raise _unavailable() from error
    if not isinstance(body, dict):
        raise _unavailable()

    access, refresh = body.get("access_token"), body.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str):
        raise _unavailable()
    expires = body.get("expires_in")
    return Tokens(access, refresh, expires if isinstance(expires, int) else 3600)


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail="Служба входа не отвечает"
    )


def _misconfigured() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Вход настроен неверно: обратитесь к администратору",
    )


def set_cookies(response: Response, tokens: Tokens, settings: Settings) -> None:
    """Пути у кук разные и это осознанно.

    Продление уходит на сервер только при обращении к самим ручкам входа,
    а не в каждом запросе за списком блюд. Статика кук не получает вовсе —
    она лежит вне /api.
    """
    secure = settings.session_cookie_secure
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access,
        max_age=tokens.expires_in,
        path="/api",
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh,
        max_age=REFRESH_MAX_AGE,
        path="/api/auth",
        httponly=True,
        secure=secure,
        samesite="strict",
    )


def _whoami(session: object, tokens: Tokens, settings: Settings) -> Me:
    try:
        payload = decode_token(tokens.access, settings)
    except AuthError as error:
        # GoTrue приняла пару логин/пароль и выдала токен — значит пароль
        # верный. Если наша проверка подписи всё равно падает, разошлись
        # секреты (SUPABASE_JWT_SECRET), а не логин с паролем: 401 отправил
        # бы шефа перебирать пароли, хотя чинить нужно не пароль, а конфиг.
        raise _misconfigured() from error
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise AuthError("токен недействителен")
    user = load_user(session, subject)  # type: ignore[arg-type]
    return Me(email=user.email, display_name=user.display_name, roles=sorted(user.roles))


@router.post("/login", response_model=Me)
def login(
    body: LoginBody,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    http: HttpDep,
) -> Me:
    tokens = token_request(
        http,
        settings,
        "password",
        {"email": body.email, "password": body.password},
        denied="Неверная почта или пароль",
    )
    me = _whoami(session, tokens, settings)
    set_cookies(response, tokens, settings)
    return me
