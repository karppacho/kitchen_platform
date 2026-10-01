"""Клиент Google-таблиц: один вход на запрос, свои таймауты у переноса.

Отправка карточки пишет строку и сразу переносит книгу в базу. Писатель
ждёт Google по обычным таймаутам, перенос — по коротким: повар ждёт ответа,
а строка уже в листе. Ключ, сессия и токен при этом одни — второй вход в
Google на каждую отправку незачем. Сеть здесь не нужна: google-auth и
gspread подменены и только запоминают, с чем их создали.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kitchen.sync.client import GspreadClient


class _Session:
    created = 0

    def __init__(self, credentials: object, refresh_timeout: int) -> None:
        _Session.created += 1
        self.credentials = credentials
        self.refresh_timeout = refresh_timeout
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
    monkeypatch.setattr(
        "google.oauth2.service_account.Credentials.from_service_account_file",
        lambda path, scopes: ("ключ", path),
    )
    monkeypatch.setattr("google.auth.transport.requests.AuthorizedSession", _Session)
    monkeypatch.setattr("gspread.Client", _Gspread)


def test_view_shares_the_login_but_not_the_timeouts(offline: None) -> None:
    client = GspreadClient(Path("key.json"), timeout=(10, 60), refresh_timeout=15)
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
