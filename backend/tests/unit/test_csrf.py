"""Защита от подделки запросов: заголовок и Origin.

SameSite=Strict не даёт приложить куку совсем чужому сайту, но соседний
поддомен (а в части браузеров и http-версия нашего адреса) для неё свой:
его страница пошлёт нам POST с кукой шефа. Поэтому кука не доказывает, что
запрос отправила наша страница. Доказывает заголовок ``X-Kitchen-Csrf``:
страница с другого адреса не может его поставить, не спросив сервер
(предварительный запрос CORS), а сервер чужим не разрешает. Origin —
второй замок: он ловит запрос, даже если первый ослабнет.

Правило касается только изменяющих запросов с кукой сессии и без
Bearer-токена. Токен в заголовке браузер сам не приложит, поэтому
подделать такой запрос чужая страница не может.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from kitchen.web import auth
from tests.unit.test_web_auth import CSRF, LOGIN, gotrue, make_client, make_token

REJECTED = "Запрос отклонён — обновите страницу"


def with_session(cors_origins: tuple[str, ...] = ("http://localhost:5173",)) -> TestClient:
    """Клиент, у которого в браузере лежит кука сессии."""
    client = make_client(cors_origins=cors_origins)
    client.cookies.set(auth.ACCESS_COOKIE, make_token())
    return client


# ---------------------------------------------------------------------------
# Заголовок
# ---------------------------------------------------------------------------
def test_logout_with_cookie_without_header_is_rejected() -> None:
    """Долг «подделка выхода»: форма на соседнем поддомене — для SameSite
    это свой сайт, и кука уйдёт — могла одним POST выкинуть шефа."""
    reply = with_session().post("/api/auth/logout")

    assert reply.status_code == 403
    assert reply.json() == {"detail": REJECTED}
    assert reply.headers.get_list("set-cookie") == [], "до ручки выхода запрос дойти не должен"


def test_logout_with_cookie_and_header_passes() -> None:
    reply = with_session().post("/api/auth/logout", headers=CSRF)

    assert reply.status_code == 204


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_changing_method_needs_header(method: str) -> None:
    """Правило общее, без списка путей: будущие ручки защищены сами."""
    reply = with_session().request(method, "/api/auth/logout")

    assert reply.status_code == 403
    assert reply.json() == {"detail": REJECTED}


@pytest.mark.parametrize("value", ["", "0", "true"])
def test_header_must_be_exactly_one(value: str) -> None:
    reply = with_session().post("/api/auth/logout", headers={"X-Kitchen-Csrf": value})

    assert reply.status_code == 403


def test_refresh_cookie_alone_counts_as_session() -> None:
    """Продление едет только с кукой продления — её тоже нельзя подделать.

    Иначе чужая страница могла бы дёргать продление, и каждое такое
    продление тратило бы попытку из лимита nginx на вход."""
    handler = gotrue()
    client = make_client(handler=handler)
    client.cookies.set(auth.REFRESH_COOKIE, "refresh-token-1", path="/api/auth")

    reply = client.post("/api/auth/refresh")

    assert reply.status_code == 403
    assert handler.calls == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Origin
# ---------------------------------------------------------------------------
def test_foreign_origin_is_rejected() -> None:
    reply = with_session().post(
        "/api/auth/logout", headers={**CSRF, "Origin": "https://evil.example"}
    )

    assert reply.status_code == 403
    assert reply.json() == {"detail": REJECTED}


def test_null_origin_is_rejected() -> None:
    """``Origin: null`` шлют песочницы и перенаправления — своим он быть не может."""
    reply = with_session().post("/api/auth/logout", headers={**CSRF, "Origin": "null"})

    assert reply.status_code == 403


def test_origin_from_cors_list_passes() -> None:
    reply = with_session(cors_origins=("http://localhost:5173",)).post(
        "/api/auth/logout", headers={**CSRF, "Origin": "http://localhost:5173"}
    )

    assert reply.status_code == 204


@pytest.mark.parametrize(
    ("host", "proto", "origin", "status"),
    [
        # Свой сайт за nginx: схема — из X-Forwarded-Proto, не из запроса
        # nginx → api, который идёт по http.
        ("kitchen.example", "https", "https://kitchen.example", 204),
        ("kitchen.example", "https", "http://kitchen.example", 403),
        # Порт — часть происхождения: другой порт — другой сайт.
        ("kitchen.example", "https", "https://kitchen.example:8443", 403),
        ("kitchen.example:8443", "https", "https://kitchen.example:8443", 204),
        # Порт по умолчанию браузер в Origin не пишет.
        ("kitchen.example:443", "https", "https://kitchen.example", 204),
        # Разработка без nginx: схема — самого запроса.
        ("localhost:8080", None, "http://localhost:8080", 204),
        ("kitchen.example", "https", "https://other.example", 403),
    ],
)
def test_own_origin_passes_even_with_empty_cors_list(
    host: str, proto: str | None, origin: str, status: int
) -> None:
    """Если на сервере CORS_ORIGINS пуст, свой сайт всё равно работает.

    Иначе после выкладки с пустым списком шеф не смог бы даже выйти."""
    headers = {**CSRF, "Host": host, "Origin": origin}
    if proto is not None:
        headers["X-Forwarded-Proto"] = proto

    reply = with_session(cors_origins=()).post("/api/auth/logout", headers=headers)

    assert reply.status_code == status


# ---------------------------------------------------------------------------
# Что правило не трогает
# ---------------------------------------------------------------------------
def test_bearer_without_header_passes() -> None:
    """Скрипты и curl ходят с токеном: его браузер сам не приложит."""
    reply = with_session().post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {make_token()}"}
    )

    assert reply.status_code == 204


def test_non_bearer_authorization_does_not_count() -> None:
    """Пропускает только Bearer: с другим заголовком вход идёт по куке.

    Проверка входа берёт токен из заголовка только при схеме Bearer, иначе
    — из куки. Значит, и защита должна снимать проверку только тогда,
    когда кука действительно не используется."""
    reply = with_session().post("/api/auth/logout", headers={"Authorization": "Basic Zm9vOmJhcg=="})

    assert reply.status_code == 403


def test_get_is_not_affected() -> None:
    reply = with_session().get("/api/me", headers={"Origin": "https://evil.example"})

    assert reply.status_code == 200


def test_login_without_cookies_is_not_affected() -> None:
    reply = make_client(handler=gotrue()).post("/api/auth/login", json=LOGIN)

    assert reply.status_code == 200


# ---------------------------------------------------------------------------
# Журнал
# ---------------------------------------------------------------------------
def ours(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Записи приложения, без чужих библиотек."""
    return [r for r in caplog.records if r.name.startswith("kitchen.")]


def test_rejection_without_header_is_logged_without_cookie(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Отказ виден в журнале сервера: на приёмке иначе искать причину 403
    пришлось бы по заголовкам в браузере. Кука сессии в журнал не попадает."""
    client = with_session()
    token = client.cookies[auth.ACCESS_COOKIE]

    with caplog.at_level(logging.WARNING, logger="kitchen.web"):
        client.post("/api/auth/logout")

    assert [r.levelno for r in ours(caplog)] == [logging.WARNING], "ровно одна строка"
    assert "нет заголовка" in caplog.text
    assert "/api/auth/logout" in caplog.text
    assert token not in caplog.text


def test_rejection_by_origin_logs_both_addresses(caplog: pytest.LogCaptureFixture) -> None:
    """Для разбора нужны оба адреса: пришедший и тот, что сервер счёл своим."""
    client = with_session()
    token = client.cookies[auth.ACCESS_COOKIE]
    headers = {
        **CSRF,
        "Origin": "https://evil.example",
        "Host": "kitchen.example",
        "X-Forwarded-Proto": "https",
        "Authorization": "Basic c2VrcmV0OnBhcm9s",
    }

    with caplog.at_level(logging.WARNING, logger="kitchen.web"):
        client.post("/api/auth/logout", headers=headers)

    assert "чужой Origin" in caplog.text
    assert "https://evil.example" in caplog.text
    assert "https://kitchen.example" in caplog.text
    assert token not in caplog.text
    assert "c2VrcmV0OnBhcm9s" not in caplog.text, "прочие заголовки в журнал не пишутся"


def test_passed_request_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="kitchen.web"):
        with_session().post("/api/auth/logout", headers=CSRF)

    assert ours(caplog) == []


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
def test_cors_allows_the_header() -> None:
    """В разработке фронтенд может ходить с другого порта: предварительный
    запрос с нашим заголовком должен быть разрешён, иначе браузер не
    отправит сам запрос."""
    reply = make_client().options(
        "/api/auth/logout",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-kitchen-csrf",
        },
    )

    assert reply.status_code == 200
    assert "x-kitchen-csrf" in reply.headers["access-control-allow-headers"].lower()


def test_rejection_is_readable_by_allowed_origin() -> None:
    """Отказ несёт заголовки CORS: иначе страница с разрешённого адреса
    увидела бы «нет связи» вместо понятного текста."""
    reply = with_session().post("/api/auth/logout", headers={"Origin": "http://localhost:5173"})

    assert reply.status_code == 403
    assert reply.headers["access-control-allow-origin"] == "http://localhost:5173"
