"""Приложение FastAPI.

Пока здесь только проверка живости — но она нужна с самого начала: на неё
опирается смоук в ``scripts/deploy.sh``. Без сверки версии деплой не может
отличить «поднялось новое» от «старый процесс продолжает работать», а
именно это и случилось 19.08.2026.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Literal

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from kitchen import __version__
from kitchen.cards.drafts import CardsError
from kitchen.config import Settings, load_settings
from kitchen.db.journal import DbJournal
from kitchen.db.session import make_session_factory
from kitchen.llm.label import label_reader_from_settings
from kitchen.sync.client import GspreadClient
from kitchen.sync.cycle import SyncCycle, reader_from
from kitchen.sync.drive import drive_from_settings
from kitchen.sync.writer import CardSheetWriter, hold_limit
from kitchen.web.api import router
from kitchen.web.auth_api import GOTRUE_TIMEOUT
from kitchen.web.auth_api import router as auth_router
from kitchen.web.cards import cards_error
from kitchen.web.cards import router as cards_router
from kitchen.web.csrf import CSRF_HEADER, CsrfMiddleware

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session, sessionmaker

    from kitchen.llm.label import LabelReader


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
    app.include_router(auth_router)
    app.include_router(cards_router)
    # Отказы карточек — текстом для повара (и полем, которое подсветить).
    app.add_exception_handler(CardsError, cards_error)

    # Один клиент на приложение: httpx держит пул соединений, и создавать
    # его на каждый вход значит платить рукопожатием TLS за каждый вход.
    app.state.http = httpx.Client(timeout=GOTRUE_TIMEOUT)
    # Фото карточек. Ключ сервисного аккаунта читается при первом запросе к
    # Drive, а не здесь: без ключа приложение стартует, а загрузка фото
    # отвечает понятной ошибкой. Ручки берут клиент через зависимость
    # kitchen.web.cards.get_drive — тест подменяет её фальшивкой.
    app.state.drive = drive_from_settings(config)
    # Распознавание, запись в лист и перенос в базу — фабриками: ручки берут
    # их через зависимости kitchen.web.cards, тест подменяет фальшивками.
    app.state.label_reader = _once(lambda: label_reader_from_settings(config))
    app.state.card_writer = lambda: _card_writer(config, app.state.sessions)
    app.state.sync_cycle = lambda: SyncCycle(reader_from(config), app.state.sessions)

    # Порядок важен: добавленный последним оборачивает остальных. Защита
    # стоит внутри CORS, чтобы отказ с разрешённого адреса ушёл с
    # заголовками CORS — иначе браузер показал бы «нет связи» вместо текста.
    app.add_middleware(CsrfMiddleware, cors_origins=config.cors_origins)
    app.add_middleware(
        CORSMiddleware,
        # Явный список источников. Со звёздочкой браузер не пустит куки,
        # а с куками звёздочка запрещена спецификацией — так что «*» здесь
        # означало бы либо неработающий вход, либо дыру.
        allow_origins=config.cors_origins,
        allow_credentials=True,
        # PUT — загрузка фото карточки. Через nginx запрос свой и CORS не
        # нужен; без PUT ломалась бы разработка с другого адреса.
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", CSRF_HEADER],
    )

    @app.get("/healthz", response_model=Health)
    def healthz() -> Health:
        return Health(version=config.app_version, release=__version__, env=config.app_env)

    return app


def _once(build: Callable[[], LabelReader | None]) -> Callable[[], LabelReader | None]:
    """Чтец этикеток — один на процесс, созданный при первом распознавании.

    Он держит клиента polza.ai с пулом соединений: новый на каждый запрос —
    рукопожатие TLS на каждое распознавание и пул, который никто не закроет.
    Ключ не задан — ``None``, и так до перезапуска: ключ читается из окружения
    один раз.
    """
    lock = threading.Lock()
    made: list[LabelReader | None] = []

    def get() -> LabelReader | None:
        with lock:
            if not made:
                made.append(build())
            return made[0]

    return get


def _card_writer(config: Settings, sessions: sessionmaker[Session]) -> CardSheetWriter | None:
    """Писатель строки — свой на каждую отправку; ``None`` — книга карточек не
    настроена. Свой клиент Google: соединение gspread между потоками ручек не
    делится, а отправка — редкое действие."""
    if not config.sheets_id_ingredient_cards:
        return None
    client = GspreadClient(
        config.google_credentials_path,
        timeout=config.google_timeout,
        refresh_timeout=config.google_refresh_timeout,
    )
    return CardSheetWriter(
        client, config.sheets_id_ingredient_cards, DbJournal(sessions), hold=hold_limit(config)
    )


app = create_app()
