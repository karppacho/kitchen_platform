"""Настройки приложения.

Всё приходит из окружения; в коде нет ни одного значения секрета. Образец
переменных — `.env.example` в корне репозитория.
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Ошибка проверки печатает входное значение, а у проверки всей модели
        # (порог «устарело» против интервала) это словарь всех настроек вместе
        # с секретами. Сейчас его усечённые края безопасны случайно — по
        # порядку полей; в лог контейнера входные значения не пускаем вовсе.
        hide_input_in_errors=True,
    )

    # --- База ---------------------------------------------------------------
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:6543/postgres"
    # Alembic ходит прямым соединением: миграции с пулером в transaction mode
    # конфликтуют.
    database_url_direct: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres"

    # --- Supabase -----------------------------------------------------------
    supabase_url: str = "http://127.0.0.1:8000"
    supabase_anon_key: SecretStr = SecretStr("")
    # Обходит все проверки прав. Только бэкенд, никогда фронтенд, никогда логи.
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_jwt_secret: SecretStr = SecretStr("")

    # --- Google -------------------------------------------------------------
    google_credentials_path: Path = Path("service_account.json")
    sheets_id_kitchen: str = ""
    sheets_id_competitors: str = ""
    sheets_id_ingredient_cards: str = ""
    sheets_id_tastings: str = ""

    # Таймауты обязательны: у google-auth их по умолчанию нет вообще, и
    # зависший запрос вешает воркер молча. Числа взяты из kitchen_bot, где
    # они появились после реального зависания.
    google_connect_timeout: int = 10
    google_read_timeout: int = 60
    google_refresh_timeout: int = 15

    # Синхронизация «лист → база»: воркер раз в столько секунд читает книги.
    # Не чаще раза в минуту: меньшее число — почти наверняка минуты вместо
    # секунд, и цикл без передышки съедал бы квоту Google.
    sync_interval_seconds: int = Field(default=300, ge=60)
    # Данные старше этого — полоса «не обновляются» на сайте: три пропущенных цикла.
    # Строго больше интервала (проверка ниже).
    sync_stale_after_seconds: int = 900

    # --- LLM ----------------------------------------------------------------
    # Только polza.ai: прямой доступ к OpenAI и Anthropic из РФ закрыт.
    polza_api_key: SecretStr = SecretStr("")
    polza_base_url: str = "https://api.polza.ai/v1"
    llm_model: str = "gpt-4o-mini"
    llm_daily_budget_rub: int = 300

    # --- Приложение ---------------------------------------------------------
    app_env: str = "development"
    # Проставляется деплоем, отдаётся в /healthz. Так «какая версия на
    # сервере» перестаёт быть вопросом.
    app_version: str = "dev"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    session_cookie_secure: bool = True

    @property
    def google_timeout(self) -> tuple[int, int]:
        return (self.google_connect_timeout, self.google_read_timeout)

    @model_validator(mode="after")
    def check_sync_thresholds(self) -> Self:
        # Порог не длиннее интервала — полоса «не обновляются» горела бы между
        # обычными циклами, и её скоро перестали бы замечать.
        if self.sync_stale_after_seconds <= self.sync_interval_seconds:
            msg = (
                f"SYNC_STALE_AFTER_SECONDS ({self.sync_stale_after_seconds}) должен быть "
                f"больше SYNC_INTERVAL_SECONDS ({self.sync_interval_seconds}): иначе полоса "
                "«не обновляются» горит между обычными циклами"
            )
            raise ValueError(msg)
        return self


def load_settings() -> Settings:
    """Собрать настройки.

    Отдельной функцией, а не модульным синглтоном: глобальный объект,
    создаваемый на импорте, невозможно подменить в тесте, не трогая
    порядок импортов.
    """
    return Settings()
