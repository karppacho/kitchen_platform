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
        supabase_anon_key="ключ-anon",
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
