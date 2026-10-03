"""Общее для интеграционных тестов: база с чистой схемой на каждый тест."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from kitchen.db.session import make_session_factory
from tests.integration.test_database import BACKEND, _url

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker


def _forget_sheet_writes(url: str) -> None:
    """Стереть журнал записей в лист перед откатом схемы.

    Откат ревизии действия `fill` отказывает, пока в журнале есть записи
    `fill` со статусом `failed` или `pending`: на бою в них снимок листа для
    ручного восстановления. В тестовой базе это мусор прошлого теста — его
    не разбирают, а стирают, иначе один такой тест уронил бы все следующие.
    """
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            if inspect(connection).has_table("sheet_writes"):
                connection.execute(text("delete from sheet_writes"))
    finally:
        engine.dispose()


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    url = _url()
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    # Чистая база на каждый тест: импорт идёт одной транзакцией, и остатки
    # прошлого прогона сделали бы результат зависящим от порядка тестов.
    _forget_sheet_writes(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield make_session_factory(url)
    _forget_sheet_writes(url)
    command.downgrade(config, "base")
