"""Вход по паролю через GoTrue — тем же путём, что ``POST /api/auth/login`` сайта.

Код ``web/auth_api.py`` сюда не импортируется: ``kitchen.web`` стоит выше
``kitchen.mcp`` в слоях, а его ``token_request`` ещё и говорит на языке FastAPI
(``HTTPException``). Поэтому запрос тот же, исходы те же, тексты те же, а
исключение — своё. Три исхода разведены намеренно, как на сайте: отказ по
существу (400), поломка настройки (шлюз ответил 401/403 или нет ключа) и
недоступность службы — спутать их значит заставить человека перебирать пароли,
когда чинить нужно не пароль.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from kitchen.config import Settings

GOTRUE_TIMEOUT = 10.0
DENIED = "Неверная почта или пароль"
UNAVAILABLE = "Служба входа не отвечает"
MISCONFIGURED = "Вход настроен неверно: обратитесь к администратору"


class LoginError(Exception):
    """Вход не удался; ``message`` — текст для человека, ``status`` — код ответа страницы."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def make_http_client() -> httpx.Client:
    """Клиент для GoTrue; тесты подменяют эту функцию на клиент с ``MockTransport``."""
    return httpx.Client(timeout=GOTRUE_TIMEOUT)


def password_login(client: httpx.Client, settings: Settings, email: str, password: str) -> str:
    """Войти по паре почта/пароль и вернуть access-токен GoTrue (его ``sub`` — id профиля)."""
    anon_key = settings.supabase_anon_key.get_secret_value()
    if not anon_key:
        # Идти в сеть незачем: шлюз ответит 401, а ответ заранее известен.
        raise LoginError(MISCONFIGURED, 502)
    try:
        reply = client.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
            params={"grant_type": "password"},
            json={"email": email, "password": password},
            headers={"apikey": anon_key},
            timeout=GOTRUE_TIMEOUT,
        )
    except httpx.HTTPError as error:
        raise LoginError(UNAVAILABLE, 502) from error

    if reply.status_code == 400:
        raise LoginError(DENIED, 401)
    if reply.status_code in (401, 403):
        raise LoginError(MISCONFIGURED, 502)
    if reply.status_code != 200:
        raise LoginError(UNAVAILABLE, 502)
    try:
        body = reply.json()
    except ValueError as error:
        raise LoginError(UNAVAILABLE, 502) from error
    access = body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(access, str):
        raise LoginError(UNAVAILABLE, 502)
    return access
