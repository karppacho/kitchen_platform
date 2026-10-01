"""Черновики карточек ингредиентов

Ревизия: см. ниже
Создана: 2026-10-01

Таблица `card_drafts` — черновик карточки, которую повар заводит мастером
на телефоне: поля карточки, КБЖУ, ответ «согласован?», id четырёх фото в
закрытой папке Drive, состояние распознавания этикетки и след отправки в
лист. Один активный черновик на повара.

Чек-лист (его же проверяет агент migration-guard):

  * **downgrade работает.** Ревизия только создаёт таблицу; откат сносит её
    вместе с индексами.
  * **Данные.** До ревизии таблицы не было — терять нечего. Откат стирает
    черновики: недописанные карточки поваров пропадут, а их фото останутся
    в закрытой папке Drive без ссылок. Отправленные карточки живут в листе и
    в `ingredient_cards` — их откат не трогает. Откатывать имеет смысл только
    вместе с кодом черновиков.
  * **Совместимо со старым кодом.** Старый код таблицу не знает; воркер,
    который переносит листы, пока идёт миграция, её не касается.
  * **CREATE INDEX CONCURRENTLY не нужен.** Индексы создаются вместе с пустой
    таблицей — блокировать нечего. Внешние ключи ссылаются на `profiles` и
    `sheet_writes`, но создание ключа на новой пустой таблице держит
    ссылаемую лишь мгновение.
  * **У каждого внешнего ключа есть индекс.** `owner_id` → `profiles` (ON
    DELETE CASCADE: удалили повара — его недописанное уходит с ним) —
    `ix_card_drafts_owner_id`; `sheet_write_id` → `sheet_writes` (ON DELETE
    SET NULL: журнал главнее черновика) — `ix_card_drafts_sheet_write_id`.
  * **Частичный уникальный индекс** `(owner_id) WHERE status = 'active'` —
    один активный черновик на повара и при гонке двух «Начать».
  * **NOT NULL — только у колонок с серверным значением по умолчанию**
    (кроме ключей): вставка без них не падает.
  * **Ревизия не импортирует модели приложения.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ff5c8d28b0a1"
down_revision: str | None = "e0e772e9630b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEXT = (
    "supplier",
    "category",
    "name",
    "label_name",
    "manufacturer",
    "composition",
    "shelf_life_sealed",
    "shelf_life_defrost",
    "shelf_life_after",
    "defrost_conditions",
    "description",
)
_NUTRIENTS = ("protein", "fat", "carbs", "kcal")
_PHOTOS = ("label_file_id", "package_file_id", "before_file_id", "after_file_id")


def upgrade() -> None:
    op.create_table(
        "card_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("step", sa.String(length=16), server_default="supplier", nullable=False),
        *(sa.Column(name, sa.Text(), server_default="", nullable=False) for name in _TEXT),
        *(sa.Column(name, sa.Numeric(precision=12, scale=3), nullable=True) for name in _NUTRIENTS),
        sa.Column("approval", sa.Text(), nullable=True),
        *(sa.Column(name, sa.Text(), nullable=True) for name in _PHOTOS),
        sa.Column("recognition_status", sa.String(length=16), nullable=True),
        sa.Column("recognition_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recognition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "recognition_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("submitted_row", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sheet_write_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('active', 'submitted', 'cancelled')", name="ck_card_drafts_status"
        ),
        sa.CheckConstraint("approval in ('Да', 'Отбракован')", name="ck_card_drafts_approval"),
        sa.CheckConstraint(
            "recognition_status in ('running', 'done', 'failed')",
            name="ck_card_drafts_recognition_status",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sheet_write_id"], ["sheet_writes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_card_drafts_owner_id", "card_drafts", ["owner_id"], unique=False)
    op.create_index(
        "ix_card_drafts_sheet_write_id", "card_drafts", ["sheet_write_id"], unique=False
    )
    op.create_index(
        "ux_card_drafts_active_owner",
        "card_drafts",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("ux_card_drafts_active_owner", table_name="card_drafts")
    op.drop_index("ix_card_drafts_sheet_write_id", table_name="card_drafts")
    op.drop_index("ix_card_drafts_owner_id", table_name="card_drafts")
    op.drop_table("card_drafts")
