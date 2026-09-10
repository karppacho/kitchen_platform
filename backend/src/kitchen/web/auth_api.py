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
from urllib.parse import quote

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
    # httpx по умолчанию требует ascii в значениях заголовков. Настоящий
    # ключ Supabase — JWT, он всегда ascii, но кодировку задаём явно, а не
    # полагаемся на автоопределение httpx по всему набору заголовков.
    headers = httpx.Headers(
        {"apikey": settings.supabase_anon_key.get_secret_value()}, encoding="utf-8"
    )
    try:
        reply = client.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
            params={"grant_type": grant_type},
            json=payload,
            headers=headers,
            timeout=GOTRUE_TIMEOUT,
        )
    except httpx.HTTPError as error:
        raise _unavailable() from error

    if reply.status_code in (400, 401, 403):
        raise AuthError(denied)
    if reply.status_code != 200:
        raise _unavailable()

    body = reply.json()
    access, refresh = body.get("access_token"), body.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str):
        raise _unavailable()
    expires = body.get("expires_in")
    return Tokens(access, refresh, expires if isinstance(expires, int) else 3600)


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail="Служба входа не отвечает"
    )


def set_cookies(response: Response, tokens: Tokens, settings: Settings) -> None:
    """Пути у кук разные и это осознанно.

    Продление уходит на сервер только при обращении к самим ручкам входа,
    а не в каждом запросе за списком блюд. Статика кук не получает вовсе —
    она лежит вне /api.

    Значения percent-encode: GoTrue отдаёт токены как непрозрачную строку,
    а cookie-октет (RFC 6265) допускает не любой байт. Настоящий JWT и
    настоящий refresh-токен состоят из символов, которые quote() не тронет,
    так что для них это no-op; кодирование — просто страховка от того, чего
    мы у поставщика не контролируем.
    """
    secure = settings.session_cookie_secure
    response.set_cookie(
        ACCESS_COOKIE,
        quote(tokens.access, safe=""),
        max_age=tokens.expires_in,
        path="/api",
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        quote(tokens.refresh, safe=""),
        max_age=REFRESH_MAX_AGE,
        path="/api/auth",
        httponly=True,
        secure=secure,
        samesite="strict",
    )


def _whoami(session: object, tokens: Tokens, settings: Settings) -> Me:
    payload = decode_token(tokens.access, settings)
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
