"""Подключение к базе — без базы: параметры перехватываются до сети."""

from __future__ import annotations

import pytest
from sqlalchemy import event

from kitchen.db.session import make_session_factory


class Intercepted(Exception):  # noqa: N818 — не ошибка, а остановка до сети
    """Подключение остановлено: параметры уже видны, в сеть не пошли."""


def _connect_params() -> dict[str, object]:
    """С чем фабрика сессий на самом деле открыла бы соединение.

    Смотрим в событие `do_connect` — туда приходят параметры, уже собранные
    SQLAlchemy из адреса и `connect_args`, ровно те, что получил бы psycopg.
    """
    sessions = make_session_factory("postgresql+psycopg://kitchen@db.invalid:5432/kitchen")
    engine = sessions.kw["bind"]
    seen: dict[str, object] = {}

    @event.listens_for(engine, "do_connect")
    def intercept(dialect, record, cargs, cparams) -> None:
        seen.update(cparams)
        raise Intercepted

    try:
        with pytest.raises(Intercepted):
            engine.connect()
    finally:
        engine.dispose()
    return seen


def test_connection_does_not_wait_forever() -> None:
    """Без пределов воркер висел бы вечно: на подключении к базе, которая не
    отвечает, и на соединении, чей собеседник пропал без FIN (перезагрузка
    хоста, обрыв сети). Контейнер при этом running — смоук не заметит."""
    params = _connect_params()

    assert params["connect_timeout"] == 10
    assert (
        params["keepalives"],
        params["keepalives_idle"],
        params["keepalives_interval"],
        params["keepalives_count"],
    ) == (1, 30, 10, 3)


def test_prepared_statements_stay_off() -> None:
    """Пулер в transaction-режиме: подготовленные выражения — `_pg3_0 already
    exists` на втором прогоне (журнал разборов, 10.09.2026)."""
    assert _connect_params()["prepare_threshold"] is None
