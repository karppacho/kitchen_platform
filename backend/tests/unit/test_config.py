"""Границы настроек синхронизации: ошибку в .env ловит старт, а не шеф на сайте.

`_env_file=None` — тест не читает backend/.env разработчика; проверяемые
значения заданы явно и берут верх над окружением.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from kitchen.config import Settings


def test_sync_interval_not_shorter_than_a_minute() -> None:
    """«5» вместо «300» — почти наверняка минуты вместо секунд: цикл шёл бы без
    передышки и съедал квоту Google — 60 запросов в минуту на все скрипты."""
    with pytest.raises(ValidationError, match="sync_interval_seconds"):
        Settings(_env_file=None, sync_interval_seconds=59)

    assert Settings(_env_file=None, sync_interval_seconds=60).sync_interval_seconds == 60


def test_stale_threshold_longer_than_interval() -> None:
    """Порог «устарело» не длиннее интервала — полоса «не обновляются» горела бы
    между обычными циклами."""
    with pytest.raises(ValidationError, match="SYNC_STALE_AFTER_SECONDS"):
        Settings(_env_file=None, sync_interval_seconds=300, sync_stale_after_seconds=300)

    longer = Settings(_env_file=None, sync_interval_seconds=300, sync_stale_after_seconds=301)
    assert longer.sync_stale_after_seconds == 301


def test_settings_errors_hide_input_values() -> None:
    """Ошибка настроек уходит в лог контейнера. Входных значений в ней нет: у
    проверки всей модели это был бы словарь всех настроек вместе с секретами."""
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, llm_daily_budget_rub="SEKRET")

    assert "llm_daily_budget_rub" in str(caught.value)
    assert "SEKRET" not in str(caught.value)


def test_sync_defaults() -> None:
    """Раз в пять минут; «устарело» — три пропущенных цикла."""
    fields = Settings.model_fields
    assert fields["sync_interval_seconds"].default == 300
    assert fields["sync_stale_after_seconds"].default == 900
