"""Журнал записей в Google-таблицы

Ревизия: см. ниже
Создана: 2026-10-01

Таблица `sheet_writes` — след каждой записи платформы в лист: куда писали,
снимок строк до записи, что отправили, кто, чем кончилось. Первая запись —
новая строка карточки ингредиента (docs/adr/0003).

Чек-лист (его же проверяет агент migration-guard):

  * **downgrade работает.** Ревизия только создаёт таблицу; откат сносит её
    вместе с индексами.
  * **Данные.** До ревизии таблицы не было — терять нечего. Откат стирает
    журнал записей: следы уже сделанных записей в лист пропадут, сам лист не
    меняется. Откатывать её имеет смысл только вместе с кодом писателя.
  * **Совместимо со старым кодом.** Старый код таблицу не знает; воркер,
    который переносит листы, пока идёт миграция, её не касается.
  * **CREATE INDEX CONCURRENTLY не нужен.** Индексы создаются вместе с пустой
    таблицей — блокировать нечего, живые таблицы не трогаются.
  * **У внешнего ключа есть индекс.** `actor_id` → `profiles` (ON DELETE SET
    NULL: удалённый человек не уносит след записи) — `ix_sheet_writes_actor_id`.
  * **Частичный уникальный индекс** по `request_key` среди открытых
    (`pending`, `verified`): повтор отправки не даёт второй строки, а
    неудачная попытка ключ освобождает.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5c1e8d4b7a26"
down_revision: str | None = "7f3c2a9e5d41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sheet_writes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("book", sa.String(length=32), nullable=False),
        sa.Column("sheet", sa.Text(), nullable=False),
        sa.Column("row", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("request_key", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('pending', 'verified', 'rolled_back', 'failed')",
            name="ck_sheet_writes_status",
        ),
        sa.CheckConstraint("action in ('append')", name="ck_sheet_writes_action"),
        sa.ForeignKeyConstraint(["actor_id"], ["profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sheet_writes_actor_id", "sheet_writes", ["actor_id"], unique=False)
    op.create_index(
        "ux_sheet_writes_open_request_key",
        "sheet_writes",
        ["request_key"],
        unique=True,
        postgresql_where=sa.text("status in ('pending', 'verified')"),
    )


def downgrade() -> None:
    op.drop_index("ux_sheet_writes_open_request_key", table_name="sheet_writes")
    op.drop_index("ix_sheet_writes_actor_id", table_name="sheet_writes")
    op.drop_table("sheet_writes")
