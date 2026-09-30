"""Защита от подделки запросов (CSRF).

Браузер сам прикладывает куки к запросу, откуда бы тот ни пришёл: форма
или картинка на чужом сайте отправит на наш адрес POST вместе с кукой
сессии шефа. Поэтому кука сама по себе не доказывает, что запрос
отправила наша страница. SameSite=Strict закрывает большую часть
случаев, но это настройка браузера, а не наша проверка.

Два замка на каждый изменяющий запрос с кукой сессии:

1. Заголовок ``X-Kitchen-Csrf: 1``. Форма его поставить не может, а
   скрипт с чужой страницы — только спросив разрешения у сервера
   (предварительный запрос CORS), и чужим сервер не разрешает.
2. ``Origin`` — пустой, свой или из ``cors_origins``. Свой определяется
   по ``Host`` и ``X-Forwarded-Proto``, которые ставит nginx: так свой
   сайт работает и при пустом ``CORS_ORIGINS`` на сервере.

Правило общее, без списка путей: новая изменяющая ручка защищена сама,
забыть её нельзя. Запрос с Bearer-токеном правило не трогает — токен в
заголовке браузер сам не приложит, им пользуются скрипты и curl.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi.security.utils import get_authorization_scheme_param
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

from kitchen.web.auth import ACCESS_COOKIE, REFRESH_COOKIE

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.types import ASGIApp, Receive, Scope, Send

CSRF_HEADER = "X-Kitchen-Csrf"
REJECTED = "Запрос отклонён — обновите страницу"

CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Продление ездит только с кукой продления — она тоже сессия.
SESSION_COOKIES = (ACCESS_COOKIE, REFRESH_COOKIE)

_DEFAULT_PORTS = {"http": 80, "https": 443}

Origin = tuple[str, str, int]
"""Происхождение: схема, хост, порт. Порт — всегда числом: браузер не пишет
в Origin порт по умолчанию, а ``Host`` за прокси может его содержать."""


def parse_origin(value: str) -> Origin | None:
    """Разобрать происхождение. ``None`` — не http(s)-адрес, в том числе
    ``null``, который шлют песочницы и перенаправления."""
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    return scheme, host, port if port is not None else _DEFAULT_PORTS[scheme]


def own_origin(request: HTTPConnection) -> Origin | None:
    """Своё происхождение — адрес, по которому браузер к нам пришёл.

    Схема — из ``X-Forwarded-Proto``: от nginx до приложения запрос идёт по
    http, и схема самого запроса сказала бы «http» про сайт на https.
    Заголовок подделать может только тот, кто ходит мимо браузера, — но у
    него нет чужой куки, и защищать тут нечего.
    """
    host = request.headers.get("host")
    if not host:
        return None
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    scheme = forwarded or request.url.scheme
    return parse_origin(f"{scheme}://{host}")


def _has_bearer(authorization: str | None) -> bool:
    """Тот же разбор, что у проверки входа: только при Bearer-токене кука
    не используется. С любым другим заголовком вход идёт по куке — значит,
    и защита нужна."""
    scheme, credentials = get_authorization_scheme_param(authorization)
    return scheme.lower() == "bearer" and bool(credentials)


class CsrfMiddleware:
    """Отклоняет изменяющие запросы с кукой сессии без наших признаков.

    Чистое ASGI, а не ``BaseHTTPMiddleware``: тело запроса нам не нужно,
    а лишняя обёртка вокруг потоков ответа — лишняя точка отказа.
    """

    def __init__(self, app: ASGIApp, cors_origins: Iterable[str] = ()) -> None:
        self.app = app
        self.trusted = frozenset(
            origin for origin in map(parse_origin, cors_origins) if origin is not None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.forged(HTTPConnection(scope)):
            response = JSONResponse({"detail": REJECTED}, status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def forged(self, request: HTTPConnection) -> bool:
        if request.scope["method"].upper() not in CHANGING_METHODS:
            return False
        if not any(name in request.cookies for name in SESSION_COOKIES):
            # Без куки подделывать нечего: вход, например, куки не требует.
            return False
        if _has_bearer(request.headers.get("authorization")):
            return False
        if request.headers.get(CSRF_HEADER) != "1":
            return True

        origin = request.headers.get("origin")
        if not origin:
            # Старые браузеры и не-браузеры Origin не шлют; заголовок уже
            # проверен выше.
            return False
        parsed = parse_origin(origin)
        return parsed is None or (parsed not in self.trusted and parsed != own_origin(request))
