"""MCP OAuth: клиенты, коды, токены

Ревизия: см. ниже
Создана: 2026-11-04

Три новые таблицы собственного OAuth 2.1 для MCP (ADR-0004):
`mcp_oauth_clients` (клиенты, зарегистрированные сами), `mcp_oauth_codes`
(запрос на вход и одноразовый код, 10 минут) и `mcp_oauth_tokens`
(access и refresh). Номер запроса, код, access и refresh хранятся только
хешами sha256.

Чек-лист (его же проверяет агент migration-guard):

  * **downgrade работает.** Ревизия только создаёт таблицы; откат сносит их
    с индексами (сначала токены и коды, потом клиентов — из-за ключей).
  * **Данные.** До ревизии таблиц не было — терять нечего. Откат стирает
    выданные доступы: подключённые Claude и ChatGPT придётся подключить
    заново. Профили и всё остальное откат не трогает. Откатывать имеет смысл
    только вместе с кодом MCP.
  * **Совместимо со старым кодом.** Старый код таблиц не знает и не пишет в
    них; воркер переноса листов их не касается.
  * **CREATE INDEX CONCURRENTLY не нужен.** Индексы создаются вместе с пустыми
    таблицами — блокировать нечего; ключи на `profiles` держат ссылаемую
    таблицу лишь мгновение.
  * **У каждого внешнего ключа есть индекс.** `client_id` и `subject` в
    `mcp_oauth_codes` и `mcp_oauth_tokens` — по индексу на каждый. Все ключи
    ON DELETE CASCADE: удалили клиента или профиль — их доступы уходят.
  * **NOT NULL — только у колонок с серверным значением по умолчанию**
    (кроме ключей и колонок, которые пишет сам код при вставке).
  * **Ревизия не импортирует модели приложения.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4b7e1c9a52d3"
down_revision: str | None = "de79870a0f0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_clients",
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("client_secret", sa.Text(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_index("ix_mcp_oauth_clients_created_at", "mcp_oauth_clients", ["created_at"])

    op.create_table(
        "mcp_oauth_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("redirect_uri_explicit", sa.Boolean(), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), server_default="", nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("client_state", sa.Text(), nullable=True),
        sa.Column("subject", sa.Uuid(), nullable=True),
        sa.Column("form_key_hash", sa.String(length=64), nullable=True),
        sa.Column("login_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["mcp_oauth_clients.client_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_mcp_oauth_codes_client_id", "mcp_oauth_codes", ["client_id"])
    op.create_index("ix_mcp_oauth_codes_subject", "mcp_oauth_codes", ["subject"])
    op.create_index("ix_mcp_oauth_codes_expires_at", "mcp_oauth_codes", ["expires_at"])

    op.create_table(
        "mcp_oauth_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.Uuid(), nullable=False),
        sa.Column("scopes", sa.Text(), server_default="", nullable=False),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind in ('access', 'refresh')", name="ck_mcp_oauth_tokens_kind"),
        sa.ForeignKeyConstraint(["client_id"], ["mcp_oauth_clients.client_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_mcp_oauth_tokens_family_id", "mcp_oauth_tokens", ["family_id"])
    op.create_index("ix_mcp_oauth_tokens_client_id", "mcp_oauth_tokens", ["client_id"])
    op.create_index("ix_mcp_oauth_tokens_subject", "mcp_oauth_tokens", ["subject"])
    op.create_index("ix_mcp_oauth_tokens_expires_at", "mcp_oauth_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_table("mcp_oauth_tokens")
    op.drop_table("mcp_oauth_codes")
    op.drop_table("mcp_oauth_clients")
