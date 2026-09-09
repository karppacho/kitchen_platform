"""Окружение Alembic.

Строка подключения берётся из настроек, а не из alembic.ini: пароль не
должен лежать в файле, который попадает в git.

Используется **прямое** соединение с Postgres (порт 5432), а не пулер
Supavisor в transaction mode (6543). Миграции выполняют DDL и рассчитывают
на одну сессию от начала до конца; transaction-режим пулера этого не
гарантирует, и падения будут редкими и невоспроизводимыми.

Почему alembic.ini написан по-английски
---------------------------------------
configparser читает ini в кодировке системы. На Windows это cp1251, и любая
кириллица в alembic.ini роняет и саму команду `alembic`, и все тесты
миграций с UnicodeDecodeError. В CI на Linux этого не видно — там UTF-8 по
умолчанию, — поэтому ошибка обнаруживается только у разработчика и выглядит
необъяснимой. Русские пояснения живут здесь: Python читает исходники как
UTF-8 всегда.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from kitchen.config import load_settings

# Импорт ради побочного эффекта: модели регистрируются в Base.metadata.
# Без него автогенерация выпустит ПУСТУЮ ревизию — молча и с видом успеха.
from kitchen.db import models as _models  # noqa: F401
from kitchen.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", load_settings().database_url_direct)

# Модели импортируются здесь, чтобы автогенерация их увидела. Без импорта
# Alembic выпустит пустую ревизию — молча и с видом успеха.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Сгенерировать SQL без подключения к базе."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Применить миграции к базе."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Изменение типа колонки должно попадать в ревизию, а не
            # пропадать: сужение типа — потеря данных.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
