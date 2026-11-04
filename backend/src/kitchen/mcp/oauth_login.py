"""Страница ``/oauth/login``: вход паролем сайта и согласие.

Дальше сюда ``/authorize`` отправляет человека (переадресация с номером
запроса ``txn``). Два шага на одной странице: вход (почта и пароль уходят в
GoTrue тем же путём, что и на сайте) и согласие («Разрешить» / «Отказать»).

Защита формы от подделки — три замка сразу: кука ``kitchen_mcp_csrf`` (httpOnly,
SameSite=Strict, путь ``/oauth``), то же значение в скрытом поле и его хеш,
привязанный к запросу в базе. Чужая страница не знает значения; кука,
подброшенная от другого запроса, не сойдётся с привязкой. После входа ключ
меняется. Попыток пароля на один запрос — пять: дальше запрос «устарел»
(общий предел частоты — на nginx).
"""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import anyio.to_thread
from mcp.server.auth.provider import construct_redirect_uri
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from kitchen import identity
from kitchen.db import models
from kitchen.mcp import gotrue, pages
from kitchen.mcp.oauth import CODE_TTL, digest, new_secret

if TYPE_CHECKING:
    from mcp.server import MCPServer
    from sqlalchemy.orm import Session

    from kitchen.config import Settings
    from kitchen.mcp.oauth import KitchenOAuthProvider


CSRF_COOKIE = "kitchen_mcp_csrf"
MAX_LOGIN_ATTEMPTS = 5
STALE = "Запрос устарел — вернитесь в приложение и подключите платформу заново."
FORM_STALE = "Форма устарела — обновите страницу и попробуйте снова."
NEED_LOGIN = "Сначала войдите."
_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": pages.CSP,
}


class LoginPages:
    """Обработчик страницы; создаётся один на сервер и вешается через ``install``."""

    def __init__(self, provider: KitchenOAuthProvider, settings: Settings) -> None:
        self._provider = provider
        self._settings = settings
        self._http = gotrue.make_http_client()

    def install(self, server: MCPServer) -> None:
        server.custom_route("/oauth/login", methods=["GET", "POST"])(self.handle)

    async def handle(self, request: Request) -> Response:
        if request.method == "GET":
            return await self._show_login(request.query_params.get("txn", ""))
        form = await request.form()
        fields = {key: value for key, value in form.items() if isinstance(value, str)}
        return await self._post(request, fields)

    # --- GET ----------------------------------------------------------------
    async def _show_login(self, txn: str) -> Response:
        csrf = new_secret()

        def work(session: Session) -> tuple[str, str] | None:
            pending = self._pending(session, txn)
            if pending is None:
                return None
            pending.form_key_hash = digest(csrf)
            return self._client_name(session, pending.client_id), urlsplit(
                pending.redirect_uri
            ).netloc

        shown = await self._provider.run(work)
        if shown is None:
            return self._html(pages.error_page(STALE), 400)
        name, host = shown
        page = pages.login_page(txn=txn, csrf=csrf, client_name=name, error=None, host=host)
        return self._html(page, 200, csrf=csrf)

    # --- POST ---------------------------------------------------------------
    async def _post(self, request: Request, fields: dict[str, str]) -> Response:
        txn, csrf, step = fields.get("txn", ""), fields.get("csrf", ""), fields.get("step", "")
        cookie = request.cookies.get(CSRF_COOKIE, "")

        def check(session: Session) -> tuple[str, str, str | None] | Response:
            pending = self._pending(session, txn)
            if pending is None:
                return self._html(pages.error_page(STALE), 400)
            if not self._csrf_ok(cookie, csrf, pending.form_key_hash):
                return self._html(pages.error_page(FORM_STALE), 403)
            logged_in = None if pending.subject is None else str(pending.subject)
            return (
                self._client_name(session, pending.client_id),
                urlsplit(pending.redirect_uri).netloc,
                logged_in,
            )

        checked = await self._provider.run(check)
        if isinstance(checked, Response):
            return checked
        name, host, logged_in = checked
        if step == "login":
            return await self._login(fields, txn, csrf, name, host)
        if logged_in is None:
            return self._html(pages.error_page(NEED_LOGIN), 400)
        if step == "consent":
            return await self._consent(txn)
        if step == "deny":
            return await self._deny(txn)
        return self._html(pages.error_page(STALE), 400)

    async def _login(
        self, fields: dict[str, str], txn: str, csrf: str, name: str, host: str
    ) -> Response:
        def again(error: str, status: int) -> Response:
            page = pages.login_page(txn=txn, csrf=csrf, client_name=name, error=error, host=host)
            return self._html(page, status, csrf=csrf)

        try:
            access = await anyio.to_thread.run_sync(
                gotrue.password_login,
                self._http,
                self._settings,
                fields.get("email", ""),
                fields.get("password", ""),
            )
        except gotrue.LoginError as error:
            if error.status == 401:
                await self._provider.run(lambda session: self._count_failure(session, txn))
            return again(error.message, error.status)

        new_csrf = new_secret()

        def sign(session: Session) -> tuple[str, str] | None:
            try:
                payload = identity.decode_token(access, self._settings)
                user = identity.load_user(session, str(payload.get("sub")))
            except identity.AccessDeniedError as error:
                return "denied", error.detail
            except identity.IdentityError:
                # GoTrue выдала токен, а наша проверка подписи падает: разошлись секреты.
                return "config", gotrue.MISCONFIGURED
            pending = self._pending(session, txn)
            if pending is None:
                return None
            pending.subject = user.id
            pending.form_key_hash = digest(new_csrf)
            return "ok", user.display_name or user.email

        signed = await self._provider.run(sign)
        if signed is None:
            return self._html(pages.error_page(STALE), 400)
        outcome, text = signed
        if outcome == "denied":
            return again(text, 403)
        if outcome == "config":
            return again(text, 502)
        page = pages.consent_page(txn=txn, csrf=new_csrf, client_name=name, person=text, host=host)
        return self._html(page, 200, csrf=new_csrf)

    async def _consent(self, txn: str) -> Response:
        code = new_secret()

        def work(session: Session) -> str | None:
            pending = self._pending(session, txn)
            if pending is None or pending.subject is None:
                return None
            now = self._provider.now()
            pending.code_hash = digest(code)
            pending.issued_at = now
            pending.expires_at = now + CODE_TTL
            pending.form_key_hash = None
            return construct_redirect_uri(
                pending.redirect_uri, code=code, state=pending.client_state
            )

        target = await self._provider.run(work)
        if target is None:
            return self._html(pages.error_page(STALE), 400)
        return self._redirect(target)

    async def _deny(self, txn: str) -> Response:
        def work(session: Session) -> str | None:
            pending = self._pending(session, txn)
            if pending is None:
                return None
            target = construct_redirect_uri(
                pending.redirect_uri, error="access_denied", state=pending.client_state
            )
            session.delete(pending)
            return target

        target = await self._provider.run(work)
        if target is None:
            return self._html(pages.error_page(STALE), 400)
        return self._redirect(target)

    # --- внутреннее ---------------------------------------------------------
    def _pending(self, session: Session, txn: str) -> models.McpOauthCode | None:
        """Незавершённый запрос по номеру: не истёк, код ещё не выдан, попытки остались."""
        if not txn:
            return None
        return session.scalar(
            select(models.McpOauthCode)
            .where(
                models.McpOauthCode.code_hash == digest(txn),
                models.McpOauthCode.issued_at.is_(None),
                models.McpOauthCode.expires_at > self._provider.now(),
                models.McpOauthCode.login_attempts < MAX_LOGIN_ATTEMPTS,
            )
            .with_for_update()
        )

    def _count_failure(self, session: Session, txn: str) -> None:
        pending = self._pending(session, txn)
        if pending is not None:
            pending.login_attempts += 1

    @staticmethod
    def _client_name(session: Session, client_id: str) -> str:
        row = session.get(models.McpOauthClient, client_id)
        name = row.metadata_json.get("client_name") if row else None
        return name if isinstance(name, str) and name else "приложение"

    @staticmethod
    def _csrf_ok(cookie: str, field: str, bound_hash: str | None) -> bool:
        if not cookie or not field or not bound_hash:
            return False
        same = hmac.compare_digest(cookie.encode(), field.encode())
        bound = hmac.compare_digest(digest(field).encode(), bound_hash.encode())
        return same and bound

    def _html(self, body: str, status: int, *, csrf: str | None = None) -> Response:
        response = HTMLResponse(body, status_code=status, headers=_HEADERS)
        if csrf is not None:
            response.set_cookie(
                CSRF_COOKIE,
                csrf,
                max_age=int(CODE_TTL.total_seconds()),
                path="/oauth",
                httponly=True,
                secure=self._settings.session_cookie_secure,
                samesite="strict",
            )
        return response

    def _redirect(self, target: str) -> Response:
        response = RedirectResponse(target, status_code=302, headers=_HEADERS)
        response.delete_cookie(CSRF_COOKIE, path="/oauth")
        return response
