"""Интеграция с настоящим Postgres.

Эти тесты не запускаются по умолчанию: им нужна живая база. В CI её даёт
service-контейнер, локально — `make test-int` при поднятом Supabase.

Проверяется машинерия миграций, а не схема: содержимое ревизий стерегут
другие тесты. Здесь важно, что `upgrade` обратим. Необратимая миграция
обнаруживается на боевой базе, где второго шанса нет.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


def _url() -> str:
    """Строка подключения для миграций.

    Alembic ходит прямым соединением, а не через пулер в transaction-режиме:
    миграции держат одну сессию от начала до конца.
    """
    url = os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("нет DATABASE_URL_DIRECT — интеграционные тесты пропущены")
    return url


@pytest.fixture
def alembic_config() -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", _url())
    return config


def test_database_is_reachable() -> None:
    engine = create_engine(_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar() == 1


def test_migrations_are_reversible(alembic_config: Config) -> None:
    """upgrade → downgrade → upgrade на одной и той же базе.

    Ревизия, которая не переживает этот цикл, не готова к выкладке: откат
    боевой базы — не то место, где выясняют, что `downgrade` не написан.
    """
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


def test_alembic_has_single_head(alembic_config: Config) -> None:
    """Две головы в графе — дефект слияния, а не «потом разберёмся».

    Обнаруженный на сервере, он означает, что `upgrade head` не знает, куда
    идти, и деплой встаёт посреди выкладки.
    """
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(alembic_config).get_heads()
    assert len(heads) <= 1, f"голов в графе миграций: {len(heads)} — {heads}"
