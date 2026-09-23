"""Общее для интеграционных тестов: база с чистой схемой на каждый тест."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config

from kitchen.db.session import make_session_factory
from tests.integration.test_database import BACKEND, _url

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    url = _url()
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    # Чистая база на каждый тест: импорт идёт одной транзакцией, и остатки
    # прошлого прогона сделали бы результат зависящим от порядка тестов.
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield make_session_factory(url)
    command.downgrade(config, "base")
