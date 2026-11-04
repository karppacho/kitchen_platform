"""Вход, продление и выход.

Почему не напрямую в Supabase, как написано в первой редакции ТЗ: GoTrue
слушает 127.0.0.1:8000, наружу его никто не проксирует, а CSP
``default-src 'self'`` запретил бы браузеру такой запрос и при доступном
адресе. Поэтому в GoTrue ходит бэкенд, а браузер получает httpOnly-куки и
токена не видит вовсе.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr

from kitchen import gotrue
from kitchen.config import Settings
from kitchen.gotrue import GOTRUE_TIMEOUT as GOTRUE_TIMEOUT
from kitchen.gotrue import Tokens
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


class LoginBody(BaseModel):
    email: EmailStr
    password: str


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
    """Сходить в GoTrue за парой токенов (запрос и разбор — ``kitchen.gotrue``).

    Исходы переводятся в ответы так: отказ по существу — 401 (только он),
    поломка настройки и недоступность службы — 502 с разными текстами.
    """
    try:
        return gotrue.token_request(client, settings, grant_type, payload, denied)
    except gotrue.GoTrueError as error:
        if error.kind == "denied":
            raise AuthError(error.message) from error
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=error.message
        ) from error


def _misconfigured() -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=gotrue.MISCONFIGURED)


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


@router.post("/refresh", response_model=Me)
def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    http: HttpDep,
) -> Me:
    """Продлить сессию по куке.

    Обе куки переставляются, а не одна: GoTrue вращает refresh-токены, и
    старый после использования недействителен.
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise AuthError("сессия не найдена")
    tokens = token_request(
        http,
        settings,
        "refresh_token",
        {"refresh_token": token},
        denied="Сессия истекла, войдите заново",
    )
    me = _whoami(session, tokens, settings)
    set_cookies(response, tokens, settings)
    return me


@router.post("/logout")
def logout() -> Response:
    """Гасит куки и всё.

    В GoTrue не ходим: отзыв сессии на его стороне здесь ничего не даёт, а
    лишний сетевой вызов на выходе — лишняя точка отказа.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(ACCESS_COOKIE, path="/api")
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return response
