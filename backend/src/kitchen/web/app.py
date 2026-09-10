"""Приложение FastAPI.

Пока здесь только проверка живости — но она нужна с самого начала: на неё
опирается смоук в ``scripts/deploy.sh``. Без сверки версии деплой не может
отличить «поднялось новое» от «старый процесс продолжает работать», а
именно это и случилось 19.08.2026.
"""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from kitchen import __version__
from kitchen.config import Settings, load_settings
from kitchen.db.session import make_session_factory
from kitchen.web.api import router


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    """Хеш коммита, выложенного на сервер.

    Проставляется деплоем через APP_VERSION. Смоук сверяет это значение с
    тем, что он только что выложил: несовпадение означает, что процесс не
    перезапустился, и деплой откатывается.
    """
    release: str
    """Версия пакета — меняется реже коммита, удобна человеку."""
    env: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Собрать приложение.

    Фабрикой, а не модульным синглтоном: приложение с настройками, взятыми
    на импорте, невозможно поднять в тесте с другой конфигурацией.
    """
    config = settings or load_settings()

    app = FastAPI(
        title="Kitchen Platform",
        version=__version__,
        # Схему наружу не отдаём: она подробно описывает внутреннее
        # устройство, а пользователей у нас двое и им она не нужна.
        openapi_url="/openapi.json" if config.app_env == "development" else None,
    )

    # Состояние приложения: настройки и фабрика сессий. Через request,
    # а не через модульные глобалы, — иначе тест не сможет поднять
    # приложение с другой конфигурацией, не трогая порядок импортов.
    app.state.settings = config
    app.state.sessions = make_session_factory(config.database_url)

    app.include_router(router)

    app.add_middleware(
        CORSMiddleware,
        # Явный список источников. Со звёздочкой браузер не пустит куки,
        # а с куками звёздочка запрещена спецификацией — так что «*» здесь
        # означало бы либо неработающий вход, либо дыру.
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/healthz", response_model=Health)
    def healthz() -> Health:
        return Health(version=config.app_version, release=__version__, env=config.app_env)

    return app


app = create_app()
