"""Подключение к базе.

Приложение ходит через пулер в transaction-режиме, поэтому пул соединений
на своей стороне не нужен и вреден: два пула друг над другом дают
непредсказуемое число живых сессий. Отсюда `NullPool` — соединение берётся
и отдаётся, а держит их Supavisor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def make_engine(url: str, *, echo: bool = False) -> Engine:
    return create_engine(
        url,
        echo=echo,
        poolclass=NullPool,
        # Строки приезжают из Google как есть; ничего не додумываем.
        future=True,
    )


def make_session_factory(url: str, *, echo: bool = False) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(url, echo=echo), expire_on_commit=False)
