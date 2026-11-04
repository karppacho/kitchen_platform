"""Вход по паролю через GoTrue для страницы согласия OAuth.

Сам запрос и разбор ответа — общие с сайтом, в ``kitchen.gotrue``; здесь только
перевод его отказов в ``LoginError`` (текст для человека и код ответа страницы)
и фабрика клиента, которую тесты подменяют.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from kitchen import gotrue
from kitchen.gotrue import GOTRUE_TIMEOUT, MISCONFIGURED, UNAVAILABLE

if TYPE_CHECKING:
    from kitchen.config import Settings

DENIED = "Неверная почта или пароль"

__all__ = [
    "DENIED",
    "MISCONFIGURED",
    "UNAVAILABLE",
    "LoginError",
    "make_http_client",
    "password_login",
]


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
    try:
        tokens = gotrue.token_request(
            client, settings, "password", {"email": email, "password": password}, denied=DENIED
        )
    except gotrue.GoTrueError as error:
        # Как на сайте: только отказ по существу — 401, остальное — 502.
        raise LoginError(error.message, 401 if error.kind == "denied" else 502) from error
    return tokens.access
