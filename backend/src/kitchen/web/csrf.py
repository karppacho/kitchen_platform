"""Защита от подделки запросов (CSRF).

Кука сессии стоит с SameSite=Strict, и совсем чужой сайт её к своему
запросу не приложит. Но «сайт» для SameSite — весь домен: страница на
соседнем поддомене, а в части браузеров и http-версия нашего же адреса,
для неё своя, и POST оттуда придёт к нам с кукой шефа. Эти страницы нам
не подконтрольны. Поэтому SameSite не хватает, и убирать эту защиту в
расчёте на неё нельзя.

Два замка на каждый изменяющий запрос с кукой сессии:

1. Заголовок ``X-Kitchen-Csrf: 1``. Форма его поставить не может, а
   скрипт с другого адреса (соседний поддомен — тоже другой адрес) —
   только спросив разрешения у сервера (предварительный запрос CORS), и
   чужим сервер не разрешает.
2. ``Origin`` — пустой, свой или из ``cors_origins``. Свой определяется
   по ``Host`` и ``X-Forwarded-Proto``, которые ставит nginx: так свой
   сайт работает и при пустом ``CORS_ORIGINS`` на сервере.

Правило общее, без списка путей: новая изменяющая ручка защищена сама,
забыть её нельзя. Запрос с Bearer-токеном правило не трогает — токен в
заголовке браузер сам не приложит, им пользуются скрипты и curl.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi.security.utils import get_authorization_scheme_param
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse

from kitchen.web.auth import ACCESS_COOKIE, REFRESH_COOKIE

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("kitchen.web")

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
        if scope["type"] == "http":
            request = HTTPConnection(scope)
            reason = self.rejection_reason(request)
            if reason is not None:
                # Только нужное для разбора: ни кук, ни токенов, ни прочих
                # заголовков. Путь, Origin и Host (из него — свой адрес)
                # присылает клиент: через %r, чтобы перевод строки в них не
                # подделал строку журнала, и обрезанными — чтобы огромный
                # заголовок не раздул журнал.
                log.warning(
                    "запрос отклонён защитой от подделки: %s %r — %s; Origin %r, свой адрес %r",
                    scope["method"],
                    _clip(request.url.path),
                    reason,
                    _clip(request.headers.get("origin")),
                    _clip(_show(own_origin(request))),
                )
                response = JSONResponse({"detail": REJECTED}, status_code=403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def rejection_reason(self, request: HTTPConnection) -> str | None:
        """Почему запрос отклонить; ``None`` — пропустить."""
        if request.scope["method"].upper() not in CHANGING_METHODS:
            return None
        if not any(name in request.cookies for name in SESSION_COOKIES):
            # Без куки подделывать нечего: вход, например, куки не требует.
            return None
        if _has_bearer(request.headers.get("authorization")):
            return None
        if request.headers.get(CSRF_HEADER) != "1":
            return "нет заголовка"

        origin = request.headers.get("origin")
        if not origin:
            # Старые браузеры и не-браузеры Origin не шлют; заголовок уже
            # проверен выше.
            return None
        parsed = parse_origin(origin)
        if parsed is None or (parsed not in self.trusted and parsed != own_origin(request)):
            return "чужой Origin"
        return None


def _show(origin: Origin | None) -> str:
    if origin is None:
        return "не определён"
    scheme, host, port = origin
    return f"{scheme}://{host}:{port}"


_LOG_FIELD_LIMIT = 200
"""Сколько знаков присланного клиентом поля идёт в журнал. Для разбора
хватает с запасом: адрес и путь платформы короче."""


def _clip(value: str | None) -> str | None:
    """Поле для журнала — не длиннее :data:`_LOG_FIELD_LIMIT` знаков и отметка, что обрезано."""
    if value is None or len(value) <= _LOG_FIELD_LIMIT:
        return value
    return value[:_LOG_FIELD_LIMIT] + "…"
