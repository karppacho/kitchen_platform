"""Журнал вызовов модели

Ревизия: см. ниже
Создана: 2026-10-01

Таблица `llm_calls` — строка на каждый вызов модели через polza.ai, удачный
или нет: когда, зачем, какая модель и версия промпта, кто вызвал, чем
кончилось, сколько стоило, сколько токенов и времени. Из неё считаются
дневной бюджет (сутки по Москве) и лимит распознаваний на повара.

Чек-лист (его же проверяет агент migration-guard):

  * **downgrade работает.** Ревизия только создаёт таблицу; откат сносит её
    вместе с индексами.
  * **Данные не теряются.** Терять нечего: таблицы до этой ревизии не было.
    При откате теряется журнал трат за прошедшие дни — бюджет текущих суток
    начнётся с нуля.
  * **Совместимо со старым кодом.** Старый код к таблице не обращается.
  * **CREATE INDEX CONCURRENTLY не нужен.** Индексы создаются вместе с
    пустой таблицей, блокировать нечего.
  * **У внешнего ключа есть индекс.** `profile_id` → `profiles.id`,
    `ON DELETE SET NULL`: повара удалили — траты остались.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e0e772e9630b"
down_revision: str | None = "5c1e8d4b7a26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("cost_rub", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_calls_created_at"), "llm_calls", ["created_at"], unique=False)
    op.create_index(op.f("ix_llm_calls_profile_id"), "llm_calls", ["profile_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_calls_profile_id"), table_name="llm_calls")
    op.drop_index(op.f("ix_llm_calls_created_at"), table_name="llm_calls")
    op.drop_table("llm_calls")
