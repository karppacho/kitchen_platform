"""Основание для моделей SQLAlchemy.

Отдельным модулем, а не внутри models: на него ссылается alembic/env.py, и
импорт оттуда целого пакета моделей тянул бы за собой лишнее в момент, когда
миграции ещё только применяются.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общий предок моделей.

    Все таблицы объявляются от него, иначе Alembic их не увидит при
    автогенерации и молча выпустит пустую ревизию.
    """
