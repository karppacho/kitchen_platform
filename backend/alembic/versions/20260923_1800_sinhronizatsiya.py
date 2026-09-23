"""Синхронизация: отметка удаления и состояние книг

Ревизия: см. ниже
Создана: 2026-09-23

Колонка `removed_at` у пяти таблиц, приезжающих из листов, и таблица
`sync_state` — строка на книгу (Google-таблицу).

Чек-лист (его же проверяет агент migration-guard):

  * **downgrade работает.** Колонки и таблица удаляются целиком.
  * **Данные не теряются.** Новые колонки пустые; при откате теряются только
    отметки удаления и состояние синхронизации — их восстановит первый же
    цикл воркера.
  * **Совместимо со старым кодом.** Старый код колонку и таблицу не читает;
    выкладка применяет миграцию раньше, чем перезапускает код.
  * **CREATE INDEX CONCURRENTLY не нужен.** Индексов нет: таблицы по полторы
    сотни строк, фильтр `removed_at is null` дешевле индекса.
  * **Внешних ключей нет.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f3c2a9e5d41"
down_revision: str | None = "0d2ff156b9bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("ingredients", "ingredient_cards", "packaging", "cooking_methods", "dishes")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "sync_state",
        sa.Column("book", sa.String(length=32), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("read_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("problem", sa.Text(), nullable=True),
        sa.Column("problem_since", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("book"),
    )


def downgrade() -> None:
    op.drop_table("sync_state")
    for table in reversed(_TABLES):
        op.drop_column(table, "removed_at")
