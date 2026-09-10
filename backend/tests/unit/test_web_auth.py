"""Вход: куки, GoTrue, коды отказов.

Дублёры рукописные, как и в ``tests/conftest.py``. GoTrue подменяется
``httpx.MockTransport`` — это механизм самого httpx, поэтому подделка
ведёт себя как настоящий клиент, а не как наши ожидания о нём.
"""

from __future__ import annotations

import datetime as dt
import json
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


def attrs_of(cookie: str) -> set[str]:
    """Атрибуты Set-Cookie как точные токены, а не подстроки.

    Подстрочная проверка ``"Path=/api" in cookie`` прошла бы и при
    ``Path=/api/auth`` — именно ту склейку путей, которую спека запрещает
    отдельным абзацем.
    """
    return set(cookie.split("; "))


def test_login_sets_both_cookies_httponly() -> None:
    handler = gotrue()
    reply = make_client(handler=handler).post(
        "/api/auth/login", json={"email": "chef@example.com", "password": "пароль"}
    )

    assert reply.status_code == 200
    jar = cookies_of(reply)

    access = attrs_of(jar[auth.ACCESS_COOKIE])
    assert "HttpOnly" in access, "иначе XSS уносит доступ"
    assert "Secure" in access
    assert any(a.casefold() == "samesite=strict" for a in access)
    assert "Path=/api" in access, "не /api/auth — иначе кука едет в каждый запрос"

    refresh = attrs_of(jar[auth.REFRESH_COOKIE])
    assert "HttpOnly" in refresh
    assert "Secure" in refresh
    assert any(a.casefold() == "samesite=strict" for a in refresh)
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
    кто подбирает. Наружу она не выносится.

    Настоящий GoTrue отвечает на эти два случая разными телами — дублёры
    здесь тоже разные, иначе тест проверяет не наш код, а то, что дублёр
    сказал одно и то же дважды."""
    unknown = make_client(
        handler=gotrue(400, {"error_description": "User not found"})
    ).post("/api/auth/login", json={"email": "нет@example.com", "password": "пароль"})
    wrong = make_client(
        handler=gotrue(400, {"error_description": "Invalid login credentials"})
    ).post("/api/auth/login", json={"email": "chef@example.com", "password": "не тот"})

    assert unknown.json()["detail"] == wrong.json()["detail"] == "Неверная почта или пароль"


def test_gotrue_unreachable_gives_502_not_401() -> None:
    """Иначе шеф при лежащем Supabase будет перебирать пароли."""

    def dead(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("соединение отвергнуто")

    reply = make_client(handler=dead).post(
        "/api/auth/login", json={"email": "chef@example.com", "password": "пароль"}
    )

    assert reply.status_code == 502


def test_wrong_signing_secret_gives_502_not_401() -> None:
    """SUPABASE_JWT_SECRET у нас разошёлся с тем, чем подписывает GoTrue.

    Пара логин/пароль верна, GoTrue её приняла и выдала токен — но нашей
    проверке подписи он не пройдёт. Это наша поломка конфигурации, а не
    чужой пароль: 401 здесь отправил бы шефа перебирать пароли, хотя
    перебирать нечего."""
    bad_token = jwt.encode(
        {
            "sub": str(PROFILE_ID),
            "aud": "authenticated",
            "exp": dt.datetime.now(tz=dt.UTC) + dt.timedelta(minutes=60),
        },
        "другой-секрет-подписи-для-проверки",
        algorithm="HS256",
    )
    body = {**GOOD, "access_token": bad_token}
    reply = make_client(handler=gotrue(200, body)).post(
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


# ---------------------------------------------------------------------------
# Продление и выход
# ---------------------------------------------------------------------------
def test_refresh_replaces_both_cookies() -> None:
    """GoTrue вращает refresh-токены при использовании.

    Сохранить старый значит получить отказ при следующем продлении — и
    выкинуть шефа на форму входа посреди работы.
    """
    rotated = dict(GOOD, refresh_token="refresh-token-2")
    client = make_client(handler=gotrue(200, rotated))
    client.cookies.set(auth.REFRESH_COOKIE, "refresh-token-1", path="/api/auth")

    reply = client.post("/api/auth/refresh")

    assert reply.status_code == 200
    jar = cookies_of(reply)
    assert "refresh-token-2" in jar[auth.REFRESH_COOKIE]
    assert auth.ACCESS_COOKIE in jar


def test_refresh_sends_the_cookie_to_gotrue() -> None:
    """Продлеваем именно тем, что лежит в куке, а не пустотой."""
    handler = gotrue()
    client = make_client(handler=handler)
    client.cookies.set(auth.REFRESH_COOKIE, "refresh-token-1", path="/api/auth")

    client.post("/api/auth/refresh")

    assert handler.seen.url.params["grant_type"] == "refresh_token"  # type: ignore[attr-defined]
    otpravleno = json.loads(handler.seen.content)  # type: ignore[attr-defined]
    assert otpravleno == {"refresh_token": "refresh-token-1"}


def test_refresh_without_cookie_gives_401() -> None:
    assert make_client(handler=gotrue()).post("/api/auth/refresh").status_code == 401


def test_refresh_rejected_by_gotrue_gives_401() -> None:
    # "протухший" из брифа — тоже значение куки, едущее в HTTP-заголовок:
    # та же причина заменить на ASCII, что и у refresh-token-1/2.
    client = make_client(handler=gotrue(400, {"error": "invalid_grant"}))
    client.cookies.set(auth.REFRESH_COOKIE, "refresh-expired", path="/api/auth")

    assert client.post("/api/auth/refresh").status_code == 401


def test_logout_clears_both_cookies() -> None:
    """Гашение — на тех же путях, на которых кука была поставлена.

    ``cookies_of`` ключует только по имени: погашение не на том пути
    оставило бы в браузере отдельную живую куку под тем же именем. Пути
    проверяем через ``attrs_of``, как и в тесте входа.
    """
    reply = make_client().post("/api/auth/logout")

    assert reply.status_code == 204
    assert reply.content == b"", "204 не должен нести тела"
    jar = cookies_of(reply)

    access = attrs_of(jar[auth.ACCESS_COOKIE])
    assert "Max-Age=0" in access
    assert "Path=/api" in access

    refresh = attrs_of(jar[auth.REFRESH_COOKIE])
    assert "Max-Age=0" in refresh
    assert "Path=/api/auth" in refresh
