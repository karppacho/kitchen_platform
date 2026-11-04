"""Провайдер OAuth 2.1 для MCP: клиенты, коды, токены в базе.

Реализует интерфейс SDK ``OAuthAuthorizationServerProvider``; маршруты
``/authorize``, ``/token``, ``/register``, ``/revoke`` и метаданные создаёт
сам SDK. Что здесь решаем мы:

* **Только хеши.** Номер запроса на вход, код, access и refresh в базе — sha256
  от случайных 256 бит; открытое значение видит только клиент. Поэтому
  простого sha256 достаточно: подбирать нечего.
* **Код одноразовый.** Погашается условным ``UPDATE ... WHERE used_at IS NULL``:
  два одновременных обмена не пройдут оба. Повтор погашенного кода — признак
  кражи, и выданное по нему отзывается.
* **Продление вращается.** Каждый обмен refresh выдаёт новую пару, старый
  refresh мёртв; предъявленный повторно гасит всю цепочку (семью токенов).
* **Белый список адресов возврата** (``redirect_allowed``): проверяется при
  регистрации и при каждом чтении клиента — адрес, убранный из списка позже,
  перестаёт работать у уже зарегистрированных, а ошибка не уходит на него.
* **Токен привязан к ресурсу** (RFC 8707): чужой ``resource`` отклоняется.

База синхронная, провайдер асинхронный — каждое обращение уходит в поток.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlsplit

import anyio.to_thread
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    IdentityAssertionParams,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from sqlalchemy import delete, func, select, update

from kitchen.db import models

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.auth.provider import TokenVerifier
    from sqlalchemy.orm import Session, sessionmaker

    from kitchen.config import Settings

T = TypeVar("T")

CODE_TTL = timedelta(minutes=10)
ACCESS_TTL = timedelta(hours=1)
# Месяц, как продление сессии сайта: дольше значит держать доступ ИИ бессрочно.
REFRESH_TTL = timedelta(days=30)
# Клиент, который сутки не дошёл до токенов, — мусор открытой регистрации.
UNFINISHED_CLIENT_TTL = timedelta(days=1)
# Потолок «висящих» регистраций: открытая DCR не должна раздувать таблицу.
MAX_UNFINISHED_CLIENTS = 500
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Адрес страницы входа внутри приложения; относительный, чтобы работать за любым прокси.
LOGIN_PATH = "/oauth/login"


def digest(value: str) -> str:
    """Хеш для хранения: всё секретное лежит в базе только в таком виде."""
    return hashlib.sha256(value.encode()).hexdigest()


def new_secret() -> str:
    """256 бит случайности: для номера запроса, кода, access и refresh."""
    return secrets.token_urlsafe(32)


def redirect_allowed(uri: str, settings: Settings) -> bool:
    """Можно ли возвращать человека на этот адрес (белый список настройки + loopback)."""
    try:
        parts = urlsplit(uri)
        port = parts.port  # заодно проверяет, что порт — число
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    # Адрес с логином (``https://claude.ai@evil.example/``), фрагментом или
    # ``..`` в пути — приём обхода префиксной проверки.
    if parts.username is not None or parts.password is not None or parts.fragment:
        return False
    if ".." in parts.path.split("/"):
        return False
    if (
        settings.mcp_allow_loopback_redirects
        and parts.scheme == "http"
        and parts.hostname in _LOOPBACK_HOSTS
        and (port is None or port > 0)
    ):
        return True
    return any(
        uri.startswith(rule[:-1]) if rule.endswith("*") else uri == rule
        for rule in settings.mcp_redirect_allowlist
    )


def canonical_resource(url: str) -> str:
    return url.rstrip("/").lower()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class KitchenOAuthProvider:
    """OAuth-сервер платформы. ``dev_verifier`` — токен разработчика (только в разработке)."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        settings: Settings,
        *,
        resource_url: str,
        dev_verifier: TokenVerifier | None = None,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.resource_url = resource_url
        self._dev_verifier = dev_verifier

    # --- служебное ----------------------------------------------------------
    async def run(self, work: Callable[[Session], T]) -> T:
        """Выполнить ``work`` в потоке одной транзакцией (коммит при выходе без ошибки)."""

        def call() -> T:
            with self.sessions.begin() as session:
                return work(session)

        return await anyio.to_thread.run_sync(call)

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)

    # --- клиенты ------------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        def work(session: Session) -> OAuthClientInformationFull | None:
            row = session.get(models.McpOauthClient, client_id)
            if row is None:
                return None
            meta: dict[str, Any] = dict(row.metadata_json)
            # Адреса вне белого списка клиент «не знает»: ни код, ни ошибка на
            # них не уйдут, даже если список сузили после регистрации.
            meta["redirect_uris"] = [
                uri
                for uri in meta.get("redirect_uris") or []
                if redirect_allowed(str(uri), self.settings)
            ]
            return OAuthClientInformationFull.model_validate(
                {**meta, "client_id": row.client_id, "client_secret": row.client_secret}
            )

        return await self.run(work)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(uri) for uri in client_info.redirect_uris or []]
        if not uris:
            raise RegistrationError("invalid_redirect_uri", "нужен хотя бы один redirect_uri")
        for uri in uris:
            if not redirect_allowed(uri, self.settings):
                raise RegistrationError(
                    "invalid_redirect_uri", f"адрес возврата не разрешён платформой: {uri}"
                )

        def work(session: Session) -> bool:
            now = self.now()
            self._sweep(session, now)
            unfinished = session.scalar(
                select(func.count())
                .select_from(models.McpOauthClient)
                .where(models.McpOauthClient.completed_at.is_(None))
            )
            if (unfinished or 0) >= MAX_UNFINISHED_CLIENTS:
                return False
            session.add(
                models.McpOauthClient(
                    client_id=client_info.client_id,
                    client_secret=client_info.client_secret,
                    metadata_json=client_info.model_dump(
                        mode="json", exclude={"client_id", "client_secret"}, exclude_none=True
                    ),
                )
            )
            return True

        if not await self.run(work):
            raise RegistrationError(
                "invalid_client_metadata",
                "слишком много незавершённых регистраций, повторите позже",
            )

    @staticmethod
    def _sweep(session: Session, now: datetime) -> None:
        """Убрать мусор: незавершённых клиентов старше суток, просроченные коды и токены."""
        session.execute(
            delete(models.McpOauthClient).where(
                models.McpOauthClient.completed_at.is_(None),
                models.McpOauthClient.created_at < now - UNFINISHED_CLIENT_TTL,
            )
        )
        session.execute(
            delete(models.McpOauthCode).where(
                models.McpOauthCode.expires_at < now - UNFINISHED_CLIENT_TTL
            )
        )
        session.execute(
            delete(models.McpOauthToken).where(
                models.McpOauthToken.expires_at < now - UNFINISHED_CLIENT_TTL
            )
        )

    # --- /authorize ---------------------------------------------------------
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not redirect_allowed(str(params.redirect_uri), self.settings):
            raise AuthorizeError("invalid_request", "адрес возврата не разрешён платформой")
        if params.resource and canonical_resource(params.resource) != canonical_resource(
            self.resource_url
        ):
            raise AuthorizeError("invalid_target", "токены выдаются только для этой платформы")
        txn = new_secret()

        def work(session: Session) -> None:
            session.add(
                models.McpOauthCode(
                    code_hash=digest(txn),
                    client_id=client.client_id,
                    redirect_uri=str(params.redirect_uri),
                    redirect_uri_explicit=params.redirect_uri_provided_explicitly,
                    code_challenge=params.code_challenge,
                    scopes=" ".join(params.scopes or []),
                    resource=self.resource_url,
                    client_state=params.state,
                    expires_at=self.now() + CODE_TTL,
                )
            )

        await self.run(work)
        return f"{LOGIN_PATH}?txn={txn}"

    # --- коды ---------------------------------------------------------------
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        def work(session: Session) -> AuthorizationCode | None:
            row = session.scalar(
                select(models.McpOauthCode).where(
                    models.McpOauthCode.code_hash == digest(authorization_code),
                    models.McpOauthCode.client_id == client.client_id,
                    models.McpOauthCode.issued_at.is_not(None),
                )
            )
            if row is None or row.subject is None:
                return None
            if row.used_at is not None:
                # Код уже обменян и предъявлен снова: выданное по нему отзываем.
                self._revoke_family(session, row.id)
                return None
            return AuthorizationCode(
                code=authorization_code,
                scopes=row.scopes.split(),
                expires_at=_utc(row.expires_at).timestamp(),
                client_id=row.client_id,
                code_challenge=row.code_challenge,
                redirect_uri=AnyUrl(row.redirect_uri),
                redirect_uri_provided_explicitly=row.redirect_uri_explicit,
                resource=row.resource,
                subject=str(row.subject),
            )

        return await self.run(work)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        def work(session: Session) -> OAuthToken | None:
            now = self.now()
            # Погашение одним условным UPDATE: гонка двух обменов выдаст токены одному.
            spent = session.execute(
                update(models.McpOauthCode)
                .where(
                    models.McpOauthCode.code_hash == digest(authorization_code.code),
                    models.McpOauthCode.client_id == client.client_id,
                    models.McpOauthCode.issued_at.is_not(None),
                    models.McpOauthCode.used_at.is_(None),
                    models.McpOauthCode.subject.is_not(None),
                )
                .values(used_at=now)
                .returning(
                    models.McpOauthCode.id,
                    models.McpOauthCode.subject,
                    models.McpOauthCode.scopes,
                    models.McpOauthCode.resource,
                )
            ).one_or_none()
            if spent is None:
                return None
            session.execute(
                update(models.McpOauthClient)
                .where(
                    models.McpOauthClient.client_id == client.client_id,
                    models.McpOauthClient.completed_at.is_(None),
                )
                .values(completed_at=now)
            )
            return self._issue_pair(
                session,
                family_id=spent.id,
                client_id=client.client_id,
                subject=spent.subject,
                scopes=spent.scopes,
                resource=spent.resource,
                now=now,
            )

        issued = await self.run(work)
        if issued is None:
            raise TokenError("invalid_grant", "код уже использован или недействителен")
        return issued

    # --- refresh ------------------------------------------------------------
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        def work(session: Session) -> RefreshToken | None:
            row = session.scalar(
                select(models.McpOauthToken).where(
                    models.McpOauthToken.token_hash == digest(refresh_token),
                    models.McpOauthToken.kind == "refresh",
                    models.McpOauthToken.client_id == client.client_id,
                )
            )
            if row is None or row.revoked_at is not None:
                return None
            if row.used_at is not None:
                # Старый refresh предъявлен снова: кто-то из двоих чужой — гасим цепочку.
                self._revoke_family(session, row.family_id)
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=row.client_id,
                scopes=row.scopes.split(),
                expires_at=int(_utc(row.expires_at).timestamp()),
                resource=row.resource,
                subject=str(row.subject),
            )

        return await self.run(work)

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        def work(session: Session) -> OAuthToken | None:
            now = self.now()
            spent = session.execute(
                update(models.McpOauthToken)
                .where(
                    models.McpOauthToken.token_hash == digest(refresh_token.token),
                    models.McpOauthToken.kind == "refresh",
                    models.McpOauthToken.client_id == client.client_id,
                    models.McpOauthToken.used_at.is_(None),
                    models.McpOauthToken.revoked_at.is_(None),
                    models.McpOauthToken.expires_at > now,
                )
                .values(used_at=now)
                .returning(
                    models.McpOauthToken.family_id,
                    models.McpOauthToken.subject,
                    models.McpOauthToken.scopes,
                    models.McpOauthToken.resource,
                )
            ).one_or_none()
            if spent is None:
                return None
            return self._issue_pair(
                session,
                family_id=spent.family_id,
                client_id=client.client_id,
                subject=spent.subject,
                scopes=" ".join(scopes) if scopes else spent.scopes,
                resource=spent.resource,
                now=now,
            )

        issued = await self.run(work)
        if issued is None:
            raise TokenError("invalid_grant", "refresh-токен уже использован или недействителен")
        return issued

    # --- проверка и отзыв ---------------------------------------------------
    async def load_access_token(self, token: str) -> AccessToken | None:
        if self._dev_verifier is not None:
            dev = await self._dev_verifier.verify_token(token)
            if dev is not None:
                return dev.model_copy(update={"resource": self.resource_url})

        def work(session: Session) -> AccessToken | None:
            row = session.scalar(
                select(models.McpOauthToken).where(
                    models.McpOauthToken.token_hash == digest(token),
                    models.McpOauthToken.kind == "access",
                    models.McpOauthToken.revoked_at.is_(None),
                    models.McpOauthToken.expires_at > self.now(),
                )
            )
            if row is None:
                return None
            return AccessToken(
                token=token,
                client_id=row.client_id,
                scopes=row.scopes.split(),
                expires_at=int(_utc(row.expires_at).timestamp()),
                resource=row.resource,
                subject=str(row.subject),
            )

        return await self.run(work)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        def work(session: Session) -> None:
            family = session.scalar(
                select(models.McpOauthToken.family_id).where(
                    models.McpOauthToken.token_hash == digest(token.token)
                )
            )
            if family is not None:
                self._revoke_family(session, family)

        await self.run(work)

    async def exchange_identity_assertion(
        self, client: OAuthClientInformationFull, params: IdentityAssertionParams
    ) -> OAuthToken:
        """Корпоративный вход по ID-JAG (SEP-990) не поддерживаем: он выключен в настройках."""
        raise TokenError("unsupported_grant_type", "этот способ входа не поддерживается")

    # --- внутреннее ---------------------------------------------------------
    @staticmethod
    def _revoke_family(session: Session, family_id: uuid.UUID) -> None:
        session.execute(
            update(models.McpOauthToken)
            .where(
                models.McpOauthToken.family_id == family_id,
                models.McpOauthToken.revoked_at.is_(None),
            )
            .values(revoked_at=func.now())
        )

    @staticmethod
    def _issue_pair(
        session: Session,
        *,
        family_id: uuid.UUID,
        client_id: str,
        subject: uuid.UUID,
        scopes: str,
        resource: str | None,
        now: datetime,
    ) -> OAuthToken:
        access, refresh = new_secret(), new_secret()
        for kind, value, ttl in (
            ("access", access, ACCESS_TTL),
            ("refresh", refresh, REFRESH_TTL),
        ):
            session.add(
                models.McpOauthToken(
                    token_hash=digest(value),
                    kind=kind,
                    family_id=family_id,
                    client_id=client_id,
                    subject=subject,
                    scopes=scopes,
                    resource=resource,
                    created_at=now,
                    expires_at=now + ttl,
                )
            )
        return OAuthToken(
            access_token=access,
            expires_in=int(ACCESS_TTL.total_seconds()),
            scope=scopes or None,
            refresh_token=refresh,
        )
