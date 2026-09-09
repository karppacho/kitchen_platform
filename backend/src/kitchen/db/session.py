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
        connect_args={
            # ОБЯЗАТЕЛЬНО при работе через пулер в transaction-режиме.
            #
            # psycopg3 сам готовит повторяющиеся запросы и даёт им имена
            # вида `_pg3_0`. В transaction-режиме соединение под нами
            # меняется между транзакциями, и подготовленное выражение
            # оказывается либо неизвестным, либо уже занятым:
            # `DuplicatePreparedStatement: prepared statement "_pg3_0"
            # already exists`.
            #
            # Коварство в том, что отказ не сразу: первый импорт в пустую
            # базу прошёл целиком, а второй упал — executemany на обновлениях
            # включает подготовку там, где вставки обходились без неё.
            "prepare_threshold": None,
        },
        future=True,
    )


def make_session_factory(url: str, *, echo: bool = False) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(url, echo=echo), expire_on_commit=False)
