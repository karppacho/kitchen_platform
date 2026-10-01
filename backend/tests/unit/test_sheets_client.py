"""Клиент Google-таблиц: один вход на запрос, свои таймауты у переноса.

Отправка карточки пишет строку и сразу переносит книгу в базу. Писатель
ждёт Google по обычным таймаутам, перенос — по коротким: повар ждёт ответа,
а строка уже в листе. Ключ, сессия и токен при этом одни — второй вход в
Google на каждую отправку незачем. Сеть здесь не нужна: google-auth и
gspread подменены и только запоминают, с чем их создали.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import ClassVar

import pytest
import requests

from kitchen.config import Settings
from kitchen.sync.client import GspreadClient


class _Session:
    created = 0
    options: ClassVar[list[dict[str, object]]] = []
    """С чем создавали сессии — всё, кроме учётки."""

    def __init__(self, credentials: object, **options: object) -> None:
        _Session.created += 1
        _Session.options.append(options)
        self.credentials = credentials
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Gspread:
    def __init__(self, auth: object, session: _Session) -> None:
        self.auth = auth
        self.session = session
        self.timeout: object = None
        self.opened: list[str] = []

    def set_timeout(self, timeout: object) -> None:
        self.timeout = timeout

    def open_by_key(self, key: str) -> tuple[str, object]:
        self.opened.append(key)
        return (key, self.timeout)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    _Session.created = 0
    _Session.options = []
    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        lambda path, scopes: ("ключ", path),
    )
    monkeypatch.setattr("google.auth.transport.requests.AuthorizedSession", _Session)
    monkeypatch.setattr("gspread.Client", _Gspread)


def test_view_shares_the_login_but_not_the_timeouts(offline: None) -> None:
    client = GspreadClient(Path("key.json"), timeout=(10, 60))
    view = client.with_timeout((5, 20))

    book = client.open("cards")
    quick = view.open("cards")

    assert book == ("cards", (10, 60))
    assert quick == ("cards", (5, 20)), "таблицу вид открывает своим клиентом и таймаутами"
    assert _Session.created == 1, "ключ, сессия и токен — одни"
    assert client._client().session is view._client().session  # type: ignore[attr-defined]


def test_close_closes_the_shared_session_from_either_side(offline: None) -> None:
    client = GspreadClient(Path("key.json"))
    view = client.with_timeout((5, 20))
    view.open("cards")
    session = view._client().session  # type: ignore[attr-defined]

    view.close()

    assert session.closed is True


def test_close_without_login_is_harmless(offline: None) -> None:
    """Запрос, не дошедший до Google (отказ до записи), закрывает пустой вход."""
    GspreadClient(Path("key.json")).with_timeout((5, 20)).close()

    assert _Session.created == 0


# ---------------------------------------------------------------------------
# Таймаут обновления токена
# ---------------------------------------------------------------------------
def test_session_gets_no_refresh_timeout(offline: None) -> None:
    """``refresh_timeout`` google-auth 2.57 только хранит и не применяет —
    передавать его незачем: он обещал бы то, чего нет."""
    GspreadClient(Path("key.json"), timeout=(10, 60)).open("cards")

    assert _Session.options == [{}]


def test_refresh_timeout_is_not_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настройки GOOGLE_REFRESH_TIMEOUT больше нет. Строка в старом .env
    сервера не ломает старт — лишнее в окружении настройки пропускают."""
    monkeypatch.setenv("GOOGLE_REFRESH_TIMEOUT", "15")

    assert "google_refresh_timeout" not in Settings.model_fields
    assert Settings(_env_file=None).google_timeout == (10, 60)


class _Credentials:
    """Учётка вместо ключа: запоминает, с каким таймаутом её просили обновить токен."""

    def __init__(self) -> None:
        self.refresh_timeouts: list[object] = []

    def before_request(
        self, request: object, method: str, url: str, headers: dict[str, str]
    ) -> None:
        self.refresh_timeouts.append(getattr(request, "keywords", {}).get("timeout"))
        headers["Authorization"] = "Bearer test-token"


class _Sheets(requests.adapters.BaseAdapter):
    """Sheets API вместо сети: на чтение сведений таблицы — пустая таблица."""

    def __init__(self) -> None:
        super().__init__()
        self.timeouts: list[object] = []

    def send(self, request: requests.PreparedRequest, **kwargs: object) -> requests.Response:
        self.timeouts.append(kwargs.get("timeout"))
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response.raw = io.BytesIO(
            json.dumps({"spreadsheetId": "cards", "properties": {"title": "Карточки"}}).encode()
        )
        response.request = request
        response.url = request.url or ""
        return response

    def close(self) -> None:
        """Соединений нет."""


def test_token_refresh_takes_the_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящие AuthorizedSession и gspread: токен обновляется с таймаутом
    самого запроса к таблице — его ставит ``set_timeout`` клиента. Он и
    закрывает зависшее обновление; отдельный таймаут обновления не нужен."""
    credentials = _Credentials()
    sheets = _Sheets()
    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        lambda path, scopes: credentials,
    )
    client = GspreadClient(Path("key.json"), timeout=(7, 42))
    _credentials, session = client._sign_in()
    session.mount("https://", sheets)

    client.open("cards")

    assert credentials.refresh_timeouts == [(7, 42)]
    assert sheets.timeouts == [(7, 42)]
