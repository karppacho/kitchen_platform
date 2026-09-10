"""Вход: куки, GoTrue, коды отказов.

Дублёры рукописные, как и в ``tests/conftest.py``. GoTrue подменяется
``httpx.MockTransport`` — это механизм самого httpx, поэтому подделка
ведёт себя как настоящий клиент, а не как наши ожидания о нём.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable

import httpx
import jwt
from fastapi.testclient import TestClient

from kitchen.config import Settings
from kitchen.db import models
from kitchen.web import auth
from kitchen.web.app import create_app

SECRET = "секрет-подписи-для-тестов"
PROFILE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")

_DEFAULT = object()


def make_token(subject: uuid.UUID = PROFILE_ID, minutes: int = 60) -> str:
    """Токен, подписанный тем же секретом, что проверяет приложение."""
    return jwt.encode(
        {
            "sub": str(subject),
            "aud": "authenticated",
            "exp": dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=minutes),
        },
        SECRET,
        algorithm="HS256",
    )


def make_profile(*, active: bool = True, roles: tuple[str, ...] = ("chef",)) -> models.Profile:
    profile = models.Profile(
        id=PROFILE_ID, email="chef@example.com", display_name="Алексей", is_active=active
    )
    profile.roles = [models.UserRole(profile_id=PROFILE_ID, role_code=code) for code in roles]
    return profile


class FakeSession:
    """Дублёр сессии базы.

    ``load_user`` спрашивает у сессии ровно один ``scalar`` — подделывать
    здесь больше нечего, и мок-библиотека была бы тяжелее задачи.
    """

    def __init__(self, profile: models.Profile | None) -> None:
        self._profile = profile

    def scalar(self, _statement: object) -> models.Profile | None:
        return self._profile


def make_client(
    *,
    profile: models.Profile | object | None = _DEFAULT,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> TestClient:
    settings = Settings(  # type: ignore[call-arg]
        app_env="test",
        supabase_url="http://supabase.test",
        supabase_anon_key="anon-key-test",
        supabase_jwt_secret=SECRET,
        session_cookie_secure=True,
    )
    app = create_app(settings)
    if handler is not None:
        app.state.http = httpx.Client(transport=httpx.MockTransport(handler))
    chosen = make_profile() if profile is _DEFAULT else profile
    app.dependency_overrides[auth.get_session] = lambda: FakeSession(chosen)  # type: ignore[arg-type]
    return TestClient(app)


# ---------------------------------------------------------------------------
# Токен из куки
# ---------------------------------------------------------------------------
def test_cookie_is_accepted_as_token() -> None:
    """Фронтенд токена не видит: он приходит только httpOnly-кукой."""
    client = make_client()
    client.cookies.set(auth.ACCESS_COOKIE, make_token())

    reply = client.get("/api/me")

    assert reply.status_code == 200
    assert reply.json()["email"] == "chef@example.com"


def test_header_wins_over_cookie() -> None:
    """Явно переданный токен должен побеждать.

    Иначе отладка через curl с чужой кукой в браузере даёт необъяснимый
    результат: спрашиваешь одним, отвечают про другого.
    """
    other = uuid.UUID("22222222-2222-2222-2222-222222222222")
    client = make_client()
    client.cookies.set(auth.ACCESS_COOKIE, make_token(subject=other, minutes=-5))

    reply = client.get("/api/me", headers={"Authorization": f"Bearer {make_token()}"})

    assert reply.status_code == 200, "протухшая кука не должна отменять годный заголовок"


def test_without_token_gives_401() -> None:
    assert make_client().get("/api/me").status_code == 401


# ---------------------------------------------------------------------------
# Вход
# ---------------------------------------------------------------------------
GOOD = {
    "access_token": make_token(),
    "refresh_token": "refresh-token-1",
    "expires_in": 3600,
    "token_type": "bearer",
}


def gotrue(status: int = 200, body: dict[str, object] | None = None):
    """Дублёр GoTrue: отвечает тем, что скажут, и запоминает запрос."""

    def handler(request: httpx.Request) -> httpx.Response:
        handler.seen = request  # type: ignore[attr-defined]
        return httpx.Response(status, json=body if body is not None else GOOD)

    return handler


def cookies_of(reply: httpx.Response) -> dict[str, str]:
    """Заголовки Set-Cookie по имени куки. Флаги проверяем строкой: они
    часть контракта с браузером, и их потеря не видна ни по одному телу."""
    return {item.split("=", 1)[0]: item for item in reply.headers.get_list("set-cookie")}


def test_login_sets_both_cookies_httponly() -> None:
    handler = gotrue()
    reply = make_client(handler=handler).post(
        "/api/auth/login", json={"email": "chef@example.com", "password": "пароль"}
    )

    assert reply.status_code == 200
    jar = cookies_of(reply)

    access = jar[auth.ACCESS_COOKIE]
    assert "HttpOnly" in access, "иначе XSS уносит доступ"
    assert "Secure" in access
    assert "SameSite=strict" in access.replace("SameSite=Strict", "SameSite=strict")
    assert "Path=/api" in access

    refresh = jar[auth.REFRESH_COOKIE]
    assert "HttpOnly" in refresh
    assert "Path=/api/auth" in refresh, "продление не должно ездить в каждом запросе"


def test_login_answers_with_profile() -> None:
    """Тело — то же, что у /api/me: иначе фронтенд делает лишний круг."""
    body = make_client(handler=gotrue()).post(
        "/api/auth/login", json={"email": "chef@example.com", "password": "пароль"}
    ).json()

    assert body == {"email": "chef@example.com", "display_name": "Алексей", "roles": ["chef"]}


def test_login_passes_anon_key_to_gotrue() -> None:
    handler = gotrue()
    make_client(handler=handler).post(
        "/api/auth/login", json={"email": "chef@example.com", "password": "пароль"}
    )

    assert handler.seen.headers["apikey"] == "anon-key-test"  # type: ignore[attr-defined]
    assert handler.seen.url.params["grant_type"] == "password"  # type: ignore[attr-defined]


def test_wrong_password_gives_401_and_no_cookies() -> None:
    reply = make_client(handler=gotrue(400, {"error": "invalid_grant"})).post(
        "/api/auth/login", json={"email": "chef@example.com", "password": "не тот"}
    )

    assert reply.status_code == 401
    assert reply.headers.get_list("set-cookie") == []


def test_login_does_not_say_which_half_was_wrong() -> None:
    """Разница «нет такого пользователя» и «пароль не тот» — подсказка тому,
    кто подбирает. Наружу она не выносится."""
    unknown = make_client(handler=gotrue(400, {"error": "invalid_grant"})).post(
        "/api/auth/login", json={"email": "нет@example.com", "password": "пароль"}
    )
    wrong = make_client(handler=gotrue(400, {"error": "invalid_grant"})).post(
        "/api/auth/login", json={"email": "chef@example.com", "password": "не тот"}
    )

    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_gotrue_unreachable_gives_502_not_401() -> None:
    """Иначе шеф при лежащем Supabase будет перебирать пароли."""

    def dead(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("соединение отвергнуто")

    reply = make_client(handler=dead).post(
        "/api/auth/login", json={"email": "chef@example.com", "password": "пароль"}
    )

    assert reply.status_code == 502


def test_login_without_profile_gives_403() -> None:
    """Учётка в Supabase есть, а в нашей таблице нет — недоделка
    администратора, а не ошибка входа."""
    reply = make_client(handler=gotrue(), profile=None).post(
        "/api/auth/login", json={"email": "chef@example.com", "password": "пароль"}
    )

    assert reply.status_code == 403
