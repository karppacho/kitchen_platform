"""Запрос токена у GoTrue — одна копия для сайта и MCP.

Общий для ``kitchen.web.auth_api`` (вход и продление сессии сайта) и
``kitchen.mcp`` (вход на странице согласия OAuth). Стоит вне слоёв и ничего не
знает про FastAPI: отказы — ``GoTrueError`` с видом и текстом для человека, а
в HTTP-ответы их превращает каждый транспорт сам. URL, ключ, тайм-аут и разбор
ответа живут только здесь, чтобы две копии не разошлись.

Три исхода разделены намеренно: спутать любые два значит заставить человека
перебирать пароли, когда чинить нужно не пароль.

- 400 — отказ по существу. Так GoTrue отвечает и на неверную пару
  логин/пароль, и на протухший или уже использованный refresh-токен
  (``invalid_grant``). Вид ``denied``.
- 401 и 403 — это не GoTrue, а шлюз Supabase перед ней: так он отвечает на
  пустой или неверный ``SUPABASE_ANON_KEY``. Поломка конфигурации, вид
  ``misconfigured``.
- всё остальное, обрыв, таймаут и мусор в теле — служба недоступна, вид
  ``unavailable``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import httpx

if TYPE_CHECKING:
    from kitchen.config import Settings

GOTRUE_TIMEOUT = 10.0
UNAVAILABLE = "Служба входа не отвечает"
MISCONFIGURED = "Вход настроен неверно: обратитесь к администратору"

Kind = Literal["denied", "misconfigured", "unavailable"]


class GoTrueError(Exception):
    """Вход в GoTrue не удался; ``kind`` — что именно, ``message`` — текст для человека."""

    def __init__(self, kind: Kind, message: str) -> None:
        super().__init__(message)
        self.kind: Kind = kind
        self.message = message


@dataclass(frozen=True, slots=True)
class Tokens:
    access: str
    refresh: str
    expires_in: int


def token_request(
    client: httpx.Client,
    settings: Settings,
    grant_type: str,
    payload: dict[str, str],
    denied: str,
) -> Tokens:
    """Сходить в GoTrue за парой токенов; ``denied`` — текст отказа по существу (400)."""
    anon_key = settings.supabase_anon_key.get_secret_value()
    if not anon_key:
        # Идти в сеть незачем: шлюз ответит 401, а ответ заранее известен.
        raise GoTrueError("misconfigured", MISCONFIGURED)
    try:
        reply = client.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
            params={"grant_type": grant_type},
            json=payload,
            headers={"apikey": anon_key},
            timeout=GOTRUE_TIMEOUT,
        )
    except httpx.HTTPError as error:
        raise GoTrueError("unavailable", UNAVAILABLE) from error

    if reply.status_code == 400:
        raise GoTrueError("denied", denied)
    if reply.status_code in (401, 403):
        raise GoTrueError("misconfigured", MISCONFIGURED)
    if reply.status_code != 200:
        raise GoTrueError("unavailable", UNAVAILABLE)

    # На 200 GoTrue обязана прислать JSON-объект, но «обязана» — не «пришлёт»:
    # nginx-заглушка, пустое тело или обрыв на полпути дают то же самое 200 с
    # мусором. Разбор тела — тоже часть недоступности службы, а не наша
    # внутренняя ошибка.
    try:
        body = reply.json()
    except ValueError as error:
        raise GoTrueError("unavailable", UNAVAILABLE) from error
    if not isinstance(body, dict):
        raise GoTrueError("unavailable", UNAVAILABLE)

    access, refresh = body.get("access_token"), body.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str):
        raise GoTrueError("unavailable", UNAVAILABLE)
    expires = body.get("expires_in")
    return Tokens(access, refresh, expires if isinstance(expires, int) else 3600)
