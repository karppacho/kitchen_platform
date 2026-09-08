"""${message}

Ревизия: ${up_revision}
Предыдущая: ${down_revision | comma,n}
Создана: ${create_date}

Перед слиянием — чек-лист (его же проверяет агент migration-guard):

  * downgrade работает? Прогон upgrade → downgrade → upgrade на непустой базе.
  * Данные не теряются? Если теряются — объяснить здесь, почему это безопасно.
  * Совместимо со старым кодом? Между применением миграции и рестартом
    процесса есть окно, в котором старый код работает с новой схемой.
    Колонка добавляется nullable, заполняется, и только потом NOT NULL.
  * Индекс на живой таблице — CREATE INDEX CONCURRENTLY в autocommit_block.
  * У каждого внешнего ключа есть индекс? Postgres их сам не создаёт.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
