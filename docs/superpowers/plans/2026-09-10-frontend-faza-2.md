# Фронтенд фазы 2 — план реализации

> **Для агентов-исполнителей:** ОБЯЗАТЕЛЬНАЯ ПОДСКИЛЛА — используйте
> superpowers:subagent-driven-development (рекомендуется) либо
> superpowers:executing-plans, чтобы выполнять план задача за задачей.
> Шаги размечены чекбоксами (`- [ ]`).

**Цель:** дать бренд-шефу рабочий веб-интерфейс — вход, справочник, блюда,
карточку блюда и сверку справочника — на живых данных, с телефона и с
ноутбука.

**Устройство:** React + TypeScript + Vite в каталоге `frontend/`, собирается
многоступенчатым Docker-образом и раздаётся тем же nginx, который проксирует
API. Вход идёт через три новые ручки бэкенда: GoTrue вызывается со стороны
сервера, браузер получает httpOnly-куки и токена не видит.

**Стек:** React 18, TypeScript 5.7, Vite 6, TanStack Query 5, react-router 6,
Vitest 2 + Testing Library + MSW 2. Бэкенд — FastAPI, httpx, pytest.

**Спека:** [2026-09-10-frontend-faza-2-design.md](../specs/2026-09-10-frontend-faza-2-design.md)
— план спорит со спекой, поэтому читать надо оба.

**Контракты ручек:** [docs/FRONTEND.md](../../FRONTEND.md), раздел 4. Ответы
там настоящие, снятые с боевой базы. Моки обязаны повторять их дословно.

## Общие ограничения

Действуют на каждую задачу, повторять в них не буду.

- **Общаемся и пишем по-русски.** Подписи, тексты ошибок, имена веток,
  сообщения коммитов, комментарии в коде — всё на русском.
- **Node не ниже 22**, npm 10. На сервер Node не ставится: он живёт только
  в ступени сборки Docker-образа.
- **Никаких CSS-фреймворков с готовой темой** — ни Bootstrap, ни Material
  UI, ни Tailwind. Плотные таблицы с числами они делают плохо.
- **Никаких глобальных стейт-менеджеров** (Redux, MobX, Zustand). Серверное
  состояние — TanStack Query, состояние экрана — адрес и `useState`.
- **Никаких внешних источников в рантайме.** CSP `default-src 'self'`.
  Шрифты пакетом `@fontsource/*`, иконки — свои SVG, эмодзи в интерфейсе
  запрещены.
- **Деньги и веса приходят строками** и строками же остаются. Арифметики
  над ними на фронтенде нет вообще: `parseFloat` над ценой — дефект.
- **Числа вправо, цифры табличные** (`font-variant-numeric: tabular-nums`),
  разделитель дробной части — запятая, единицы явные: `₽`, `г`, `%`, `шт`.
- **Минимальная ширина 360 px.** Горизонтальной прокрутки всей страницы
  быть не должно ни на одном экране.
- **Ручек записи нет.** Кнопки действий на сверке присутствуют, но не
  работают. Эндпоинты не выдумывать.
- **Светлая тема одна.** Тёмной не делаем.
- Палитра: фон `#f2f2ef`, поверхность `#fbfbf9`, текст `#1a1b1d`,
  приглушённый `#6b6e73`, граница `#e2e2de`, акцент `#2f5ecb`, внимание
  `#9a6400`, ошибка `#b3261e`. Один акцент и два семантических, больше нет.
- Каждая задача заканчивается коммитом. Ветка одна на всю работу:
  `frontend-faza-2`.
- Бэкендовые проверки запускаются из `backend/`: `uv run pytest`,
  `uv run mypy src`, `uv run ruff check .`. Фронтендовые — из `frontend/`.

---

## Раскладка файлов

**Бэкенд**

| Файл | За что отвечает |
|---|---|
| `backend/src/kitchen/web/auth_api.py` | создаётся: три ручки входа, вызов GoTrue, куки |
| `backend/src/kitchen/web/auth.py` | правится: `current_user` принимает токен из куки |
| `backend/src/kitchen/web/app.py` | правится: клиент httpx в состоянии, подключение роутера |
| `backend/tests/unit/test_web_auth.py` | создаётся: дублёр GoTrue, дублёр сессии, все тесты входа |

**Фронтенд** — каталог `frontend/`, файлы перечислены в задачах 4–13.

**Инфраструктура**

| Файл | За что отвечает |
|---|---|
| `infra/nginx/Dockerfile` | создаётся: сборка фронтенда и образ раздачи |
| `.dockerignore` | создаётся: не тащить `.venv` и `node_modules` в контекст |
| `infra/nginx/kitchen-platform.conf` | правится: статика на `/`, API на `/api/` |
| `infra/docker-compose.yml` | правится: `nginx` собирается, а не тянется |
| `.github/workflows/ci.yml` | правится: задача `frontend` |
| `Makefile` | правится: цели `front-*`, они же в `check` |

---

## Задача 1: `current_user` принимает токен из куки

**Файлы:**
- Правка: `backend/src/kitchen/web/auth.py`
- Тест: `backend/tests/unit/test_web_auth.py` (создать)

**Интерфейсы:**
- Отдаёт наружу: константы `ACCESS_COOKIE = "kp_access"`,
  `REFRESH_COOKIE = "kp_refresh"` в `kitchen.web.auth`; на них опирается
  задача 2. Хелперы `make_token`, `make_profile`, `FakeSession`,
  `make_client` в тест-файле — ими пользуются задачи 2 и 3.

- [ ] **Шаг 1: Написать падающие тесты**

Создать `backend/tests/unit/test_web_auth.py`:

```python
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
    profile: models.Profile | None = None,
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
    app.dependency_overrides[auth.get_session] = lambda: FakeSession(
        make_profile() if profile is None else profile
    )
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
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd backend && uv run pytest tests/unit/test_web_auth.py -v`
Ожидается: FAIL — `AttributeError: module 'kitchen.web.auth' has no attribute 'ACCESS_COOKIE'`.

- [ ] **Шаг 3: Поправить `auth.py`**

Добавить константы рядом с `AUDIENCE`:

```python
# Имена кук. Фронтенд их не читает — они httpOnly, — но бэкенд и тесты
# должны называть их из одного места.
ACCESS_COOKIE = "kp_access"
REFRESH_COOKIE = "kp_refresh"
```

Заменить `current_user` целиком:

```python
def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> CurrentUser:
    """Кто пришёл: по заголовку или по куке.

    Заголовок проверяется первым и побеждает. Им пользуются curl, скрипты
    выдачи доступа и тесты; кука — способ браузера, которому токен в руки
    давать нельзя.
    """
    token = credentials.credentials if credentials else request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise AuthError("нужен токен")
    payload = decode_token(token, settings)
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise AuthError("токен недействителен")
    return load_user(session, subject)
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `cd backend && uv run pytest tests/unit/test_web_auth.py -v`
Ожидается: PASS, три теста.

- [ ] **Шаг 5: Проверить, что ничего не сломалось**

Запуск: `cd backend && uv run pytest && uv run mypy src && uv run ruff check .`
Ожидается: всё зелёное. Существующие тесты с заголовком обязаны продолжать
проходить — заголовок мы не трогали.

- [ ] **Шаг 6: Коммит**

```bash
git add backend/src/kitchen/web/auth.py backend/tests/unit/test_web_auth.py
git commit -m "Токен принимается и из куки, но заголовок побеждает"
```

---

## Задача 2: `POST /api/auth/login`

**Файлы:**
- Создать: `backend/src/kitchen/web/auth_api.py`
- Правка: `backend/src/kitchen/web/app.py`
- Правка: `backend/tests/unit/test_web_auth.py`

**Интерфейсы:**
- Берёт из задачи 1: `auth.ACCESS_COOKIE`, `auth.REFRESH_COOKIE`,
  `auth.load_user`, `auth.decode_token`, `auth.AuthError`, хелперы тестов.
- Отдаёт наружу: `kitchen.web.auth_api.router` с префиксом `/api/auth`;
  функцию `token_request(client, settings, grant_type, payload, denied) -> Tokens`
  и `set_cookies(response, tokens, settings)` — ими пользуется задача 3.
  `app.state.http: httpx.Client`.

- [ ] **Шаг 1: Написать падающие тесты**

Дописать в конец `backend/tests/unit/test_web_auth.py`:

```python
# ---------------------------------------------------------------------------
# Вход
# ---------------------------------------------------------------------------
GOOD = {
    "access_token": make_token(),
    "refresh_token": "refresh-первый",
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

    assert handler.seen.headers["apikey"] == "ключ-anon"  # type: ignore[attr-defined]
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
```

Внимание: `make_client(profile=None)` по умолчанию подставляет профиль.
Чтобы проверить его отсутствие, в шаге 3 поменяйте сигнатуру хелпера на
`profile: models.Profile | None = ...` с часовым:

```python
_DEFAULT = object()


def make_client(
    *,
    profile: models.Profile | None | object = _DEFAULT,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> TestClient:
    ...
    chosen = make_profile() if profile is _DEFAULT else profile
    app.dependency_overrides[auth.get_session] = lambda: FakeSession(chosen)  # type: ignore[arg-type]
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd backend && uv run pytest tests/unit/test_web_auth.py -v`
Ожидается: FAIL — 404 на `/api/auth/login`, ручки нет.

- [ ] **Шаг 3: Написать `auth_api.py`**

```python
"""Вход, продление и выход.

Почему не напрямую в Supabase, как написано в первой редакции ТЗ: GoTrue
слушает 127.0.0.1:8000, наружу его никто не проксирует, а CSP
``default-src 'self'`` запретил бы браузеру такой запрос и при доступном
адресе. Поэтому в GoTrue ходит бэкенд, а браузер получает httpOnly-куки и
токена не видит вовсе.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr

from kitchen.config import Settings
from kitchen.web.api import Me
from kitchen.web.auth import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    AuthError,
    SessionDep,
    decode_token,
    get_settings,
    load_user,
)

router = APIRouter(prefix="/api/auth", tags=["вход"])

# Продление живёт месяц. Дольше — значит держать пропуск в браузере кухни
# бессрочно; короче — заставлять шефа вводить пароль каждую неделю.
REFRESH_MAX_AGE = 30 * 24 * 3600
GOTRUE_TIMEOUT = 10.0


class LoginBody(BaseModel):
    email: EmailStr
    password: str


@dataclass(frozen=True, slots=True)
class Tokens:
    access: str
    refresh: str
    expires_in: int


def get_http(request: Request) -> httpx.Client:
    client: httpx.Client = request.app.state.http
    return client


HttpDep = Annotated[httpx.Client, Depends(get_http)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def token_request(
    client: httpx.Client,
    settings: Settings,
    grant_type: str,
    payload: dict[str, str],
    denied: str,
) -> Tokens:
    """Сходить в GoTrue за парой токенов.

    Отказ по существу (400/401/403) и недоступность службы (всё остальное)
    разделены намеренно: спутать их значит заставить человека перебирать
    пароли, когда лежит Supabase.
    """
    try:
        reply = client.post(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/token",
            params={"grant_type": grant_type},
            json=payload,
            headers={"apikey": settings.supabase_anon_key.get_secret_value()},
            timeout=GOTRUE_TIMEOUT,
        )
    except httpx.HTTPError as error:
        raise _unavailable() from error

    if reply.status_code in (400, 401, 403):
        raise AuthError(denied)
    if reply.status_code != 200:
        raise _unavailable()

    body = reply.json()
    access, refresh = body.get("access_token"), body.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str):
        raise _unavailable()
    expires = body.get("expires_in")
    return Tokens(access, refresh, expires if isinstance(expires, int) else 3600)


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail="Служба входа не отвечает"
    )


def set_cookies(response: Response, tokens: Tokens, settings: Settings) -> None:
    """Пути у кук разные и это осознанно.

    Продление уходит на сервер только при обращении к самим ручкам входа,
    а не в каждом запросе за списком блюд. Статика кук не получает вовсе —
    она лежит вне /api.
    """
    secure = settings.session_cookie_secure
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access,
        max_age=tokens.expires_in,
        path="/api",
        httponly=True,
        secure=secure,
        samesite="strict",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh,
        max_age=REFRESH_MAX_AGE,
        path="/api/auth",
        httponly=True,
        secure=secure,
        samesite="strict",
    )


def _whoami(session: object, tokens: Tokens, settings: Settings) -> Me:
    payload = decode_token(tokens.access, settings)
    subject = payload.get("sub")
    if not isinstance(subject, str):
        raise AuthError("токен недействителен")
    user = load_user(session, subject)  # type: ignore[arg-type]
    return Me(email=user.email, display_name=user.display_name, roles=sorted(user.roles))


@router.post("/login", response_model=Me)
def login(
    body: LoginBody,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    http: HttpDep,
) -> Me:
    tokens = token_request(
        http,
        settings,
        "password",
        {"email": body.email, "password": body.password},
        denied="Неверная почта или пароль",
    )
    me = _whoami(session, tokens, settings)
    set_cookies(response, tokens, settings)
    return me
```

`EmailStr` требует `email-validator`. Добавить в `backend/pyproject.toml`
в `dependencies`: `"pydantic[email]>=2.10"` и выполнить
`cd backend && uv sync --all-extras --dev`.

- [ ] **Шаг 4: Подключить роутер и клиент в `app.py`**

В `create_app`, после `app.include_router(router)`:

```python
    from kitchen.web.auth_api import router as auth_router

    app.include_router(auth_router)

    # Один клиент на приложение: httpx держит пул соединений, и создавать
    # его на каждый вход значит платить рукопожатием TLS за каждый вход.
    app.state.http = httpx.Client(timeout=GOTRUE_TIMEOUT)
```

Импорты `httpx` и `from kitchen.web.auth_api import router as auth_router,
GOTRUE_TIMEOUT` поднять наверх файла — локальный импорт здесь только для
наглядности шага.

- [ ] **Шаг 5: Убедиться, что тесты проходят**

Запуск: `cd backend && uv run pytest tests/unit/test_web_auth.py -v`
Ожидается: PASS, десять тестов.

- [ ] **Шаг 6: Проверить весь бэкенд**

Запуск: `cd backend && uv run pytest && uv run mypy src && uv run ruff check . && uv run lint-imports`
Ожидается: всё зелёное. `lint-imports` обязан пройти: `web` вправе знать
про `httpx`, запрет на него стоит только у `domain`.

- [ ] **Шаг 7: Коммит**

```bash
git add backend/src/kitchen/web/auth_api.py backend/src/kitchen/web/app.py \
        backend/tests/unit/test_web_auth.py backend/pyproject.toml backend/uv.lock
git commit -m "Вход своей ручкой: GoTrue зовёт сервер, браузер получает куки"
```

---

## Задача 3: продление и выход

**Файлы:**
- Правка: `backend/src/kitchen/web/auth_api.py`
- Правка: `backend/tests/unit/test_web_auth.py`

**Интерфейсы:**
- Берёт из задачи 2: `token_request`, `set_cookies`, `Tokens`, `_whoami`.
- Отдаёт наружу: `POST /api/auth/refresh` → тело `Me`, `POST /api/auth/logout` → 204.

- [ ] **Шаг 1: Написать падающие тесты**

Дописать в `backend/tests/unit/test_web_auth.py`:

```python
# ---------------------------------------------------------------------------
# Продление и выход
# ---------------------------------------------------------------------------
def test_refresh_replaces_both_cookies() -> None:
    """GoTrue вращает refresh-токены при использовании.

    Сохранить старый значит получить отказ при следующем продлении — и
    выкинуть шефа на форму входа посреди работы.
    """
    rotated = dict(GOOD, refresh_token="refresh-второй")
    client = make_client(handler=gotrue(200, rotated))
    client.cookies.set(auth.REFRESH_COOKIE, "refresh-первый", path="/api/auth")

    reply = client.post("/api/auth/refresh")

    assert reply.status_code == 200
    jar = cookies_of(reply)
    assert "refresh-второй" in jar[auth.REFRESH_COOKIE]
    assert auth.ACCESS_COOKIE in jar


def test_refresh_sends_the_cookie_to_gotrue() -> None:
    """Продлеваем именно тем, что лежит в куке, а не пустотой."""
    handler = gotrue()
    client = make_client(handler=handler)
    client.cookies.set(auth.REFRESH_COOKIE, "refresh-первый", path="/api/auth")

    client.post("/api/auth/refresh")

    assert handler.seen.url.params["grant_type"] == "refresh_token"  # type: ignore[attr-defined]
    otpravleno = json.loads(handler.seen.content)  # type: ignore[attr-defined]
    assert otpravleno == {"refresh_token": "refresh-первый"}


def test_refresh_without_cookie_gives_401() -> None:
    assert make_client(handler=gotrue()).post("/api/auth/refresh").status_code == 401


def test_refresh_rejected_by_gotrue_gives_401() -> None:
    client = make_client(handler=gotrue(400, {"error": "invalid_grant"}))
    client.cookies.set(auth.REFRESH_COOKIE, "протухший", path="/api/auth")

    assert client.post("/api/auth/refresh").status_code == 401


def test_logout_clears_both_cookies() -> None:
    reply = make_client().post("/api/auth/logout")

    assert reply.status_code == 204
    jar = cookies_of(reply)
    assert 'Max-Age=0' in jar[auth.ACCESS_COOKIE]
    assert 'Max-Age=0' in jar[auth.REFRESH_COOKIE]
```

Наверх файла добавить `import json`.

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd backend && uv run pytest tests/unit/test_web_auth.py -k "refresh or logout" -v`
Ожидается: FAIL — 404, ручек нет.

- [ ] **Шаг 3: Дописать ручки в `auth_api.py`**

```python
@router.post("/refresh", response_model=Me)
def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    http: HttpDep,
) -> Me:
    """Продлить сессию по куке.

    Обе куки переставляются, а не одна: GoTrue вращает refresh-токены, и
    старый после использования недействителен.
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise AuthError("сессия не найдена")
    tokens = token_request(
        http,
        settings,
        "refresh_token",
        {"refresh_token": token},
        denied="Сессия истекла, войдите заново",
    )
    me = _whoami(session, tokens, settings)
    set_cookies(response, tokens, settings)
    return me


@router.post("/logout")
def logout() -> Response:
    """Гасит куки и всё.

    В GoTrue не ходим: отзыв сессии на его стороне здесь ничего не даёт, а
    лишний сетевой вызов на выходе — лишняя точка отказа.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(ACCESS_COOKIE, path="/api")
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    return response
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `cd backend && uv run pytest tests/unit/test_web_auth.py -v`
Ожидается: PASS, пятнадцать тестов.

- [ ] **Шаг 5: Проверить весь бэкенд**

Запуск: `cd backend && uv run pytest && uv run mypy src && uv run ruff check . && uv run lint-imports`
Ожидается: зелёное.

- [ ] **Шаг 6: Коммит**

```bash
git add backend/src/kitchen/web/auth_api.py backend/tests/unit/test_web_auth.py
git commit -m "Продление вращает обе куки, выход их гасит"
```

---

## Задача 4: каркас фронтенда

**Файлы:**
- Создать: `frontend/package.json`, `frontend/vite.config.ts`,
  `frontend/tsconfig.json`, `frontend/tsconfig.node.json`,
  `frontend/eslint.config.js`, `frontend/index.html`,
  `frontend/.gitignore`, `frontend/src/main.tsx`, `frontend/src/App.tsx`,
  `frontend/src/styles/tokens.css`, `frontend/src/styles/base.css`,
  `frontend/tests/setup.ts`, `frontend/tests/karkas.test.tsx`

**Интерфейсы:**
- Отдаёт наружу: `setViewport(px: number): void` из `tests/setup.ts` —
  им пользуются задачи 6, 10, 11. Переменные оформления из `tokens.css`.

- [ ] **Шаг 1: Создать `package.json`**

```json
{
  "name": "kitchen-frontend",
  "private": true,
  "type": "module",
  "engines": { "node": ">=22" },
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "types": "tsc --noEmit",
    "lint": "eslint src tests",
    "test": "vitest run"
  },
  "dependencies": {
    "@fontsource/ibm-plex-mono": "^5.1.0",
    "@fontsource/ibm-plex-sans": "^5.1.0",
    "@tanstack/react-query": "^5.62.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.1.0",
    "@testing-library/user-event": "^14.5.2",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "eslint": "^9.16.0",
    "eslint-plugin-react-hooks": "^5.1.0",
    "jsdom": "^25.0.1",
    "msw": "^2.6.8",
    "typescript": "^5.7.2",
    "typescript-eslint": "^8.18.0",
    "vite": "^6.0.3",
    "vitest": "^2.1.8"
  }
}
```

- [ ] **Шаг 2: Создать конфигурацию сборки**

`frontend/vite.config.ts`:

```ts
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// defineConfig берётся из vitest/config, а не из vite: иначе поле test
// не проходит проверку типов.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // В разработке фронт и API — разные порты. Прокси делает их одним
    // происхождением, иначе SameSite=Strict не отдаст куки.
    proxy: { '/api': { target: 'http://127.0.0.1:8080' } },
  },
  build: { outDir: 'dist' },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.ts', 'tests/**/*.test.tsx'],
  },
})
```

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noEmit": true,
    "skipLibCheck": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src", "tests", "vite.config.ts"]
}
```

`frontend/.gitignore`:

```
node_modules/
dist/
```

`frontend/eslint.config.js`:

```js
import js from '@eslint/js'
import hooks from 'eslint-plugin-react-hooks'
import ts from 'typescript-eslint'

export default ts.config(
  js.configs.recommended,
  ...ts.configs.recommended,
  {
    plugins: { 'react-hooks': hooks },
    rules: {
      ...hooks.configs.recommended.rules,
      // Деньги приходят строками и строками остаются: арифметика над ними
      // на фронтенде — дефект, а не стиль.
      'no-restricted-globals': ['error', 'parseFloat', 'parseInt'],
    },
  },
  { ignores: ['dist/'] },
)
```

- [ ] **Шаг 3: Создать точку входа и оформление**

`frontend/index.html`:

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Кухня — Тим Кук</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/src/styles/tokens.css`:

```css
/* Палитра снята с макета docs/design. Один акцент и два семантических —
   больше цветов рабочему инструменту не нужно. */
:root {
  --fon: #f2f2ef;
  --poverhnost: #fbfbf9;
  --tekst: #1a1b1d;
  --priglushyonnyy: #6b6e73;
  --granitsa: #e2e2de;
  --aktsent: #2f5ecb;
  --vnimanie: #9a6400;
  --oshibka: #b3261e;
  --oshibka-fon: #fbe9e7;

  --shag: 4px;
  --radius: 3px;
  --shrift: 'IBM Plex Sans', system-ui, sans-serif;
  --shrift-tsifry: 'IBM Plex Mono', ui-monospace, monospace;
}
```

`frontend/src/styles/base.css`:

```css
@import '@fontsource/ibm-plex-sans/400.css';
@import '@fontsource/ibm-plex-sans/500.css';
@import '@fontsource/ibm-plex-sans/600.css';
@import '@fontsource/ibm-plex-mono/400.css';
@import './tokens.css';

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--fon);
  color: var(--tekst);
  font: 14px/1.4 var(--shrift);
  /* Табличные цифры глобально: шеф весь день сравнивает столбцы, и цифры
     разной ширины делают это невозможным. */
  font-variant-numeric: tabular-nums;
}

/* Горизонтальная прокрутка всей страницы запрещена на любой ширине.
   Прокручиваться вправе только таблица внутри своего контейнера. */
html, body { overflow-x: hidden; }
```

`frontend/src/main.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { App } from './App'
import './styles/base.css'

const queries = new QueryClient({
  defaultOptions: {
    queries: {
      // Справочник и блюда меняются не чаще, чем шеф правит таблицу.
      staleTime: 60_000,
      // Повторять запрос, отвергнутый по правам, бессмысленно и вредно:
      // это выглядит как зависание.
      retry: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queries}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
```

`frontend/src/App.tsx` — временная заглушка, её заменит задача 8:

```tsx
export function App() {
  return <h1>Кухня</h1>
}
```

- [ ] **Шаг 4: Создать `tests/setup.ts`**

```ts
import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

let shirina = 1440

/** Задать ширину окна для проверки узкого и широкого вариантов. */
export function setViewport(px: number): void {
  shirina = px
  window.dispatchEvent(new Event('resize'))
}

// jsdom не умеет matchMedia вовсе. Подменяем разбором одного вида запроса —
// (min-width: NNNpx), — которым пользуется useWide. Слушатели вешаются на
// resize, поэтому setViewport перерисовывает компоненты по-настоящему.
beforeEach(() => {
  shirina = 1440
  window.matchMedia = ((query: string): MediaQueryList => {
    const min = Number(/min-width:\s*(\d+)px/.exec(query)?.[1] ?? 0)
    return {
      get matches() {
        return shirina >= min
      },
      media: query,
      onchange: null,
      addEventListener: (_: string, fn: EventListener) =>
        window.addEventListener('resize', fn),
      removeEventListener: (_: string, fn: EventListener) =>
        window.removeEventListener('resize', fn),
      dispatchEvent: () => false,
      addListener: () => {},
      removeListener: () => {},
    } as unknown as MediaQueryList
  }) as typeof window.matchMedia
})

afterEach(cleanup)
```

- [ ] **Шаг 5: Написать проверку каркаса**

`frontend/tests/karkas.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'

import { App } from '../src/App'
import { setViewport } from './setup'

test('приложение рисуется', () => {
  render(<App />)
  expect(screen.getByRole('heading')).toBeInTheDocument()
})

test('подмена ширины работает', () => {
  setViewport(360)
  expect(window.matchMedia('(min-width: 1080px)').matches).toBe(false)
  setViewport(1440)
  expect(window.matchMedia('(min-width: 1080px)').matches).toBe(true)
})
```

- [ ] **Шаг 6: Поставить зависимости и прогнать всё**

```bash
cd frontend && npm install
npm run types && npm run lint && npm run test && npm run build
```

Ожидается: `tsc` молчит, eslint молчит, два теста прошли, `dist/`
собрался. Файл `package-lock.json` появился — он обязан попасть в
коммит, иначе `npm ci` в образе и в CI работать не будет.

- [ ] **Шаг 7: Коммит**

```bash
git add frontend/
git commit -m "Каркас фронтенда: Vite, строгий TypeScript, Vitest, свои шрифты"
```

---

## Задача 5: `<Num>` — число, прочерк, единица

**Файлы:**
- Создать: `frontend/src/ui/Num.tsx`, `frontend/src/ui/num.css`
- Тест: `frontend/tests/num.test.tsx`

**Интерфейсы:**
- Отдаёт наружу: `formatNumber(value: string, fraction?: number): string`,
  `<Num value={string | null} unit?={string} fraction?={number} />`.
  Ими пользуются задачи 10–13.

- [ ] **Шаг 1: Написать падающие тесты**

`frontend/tests/num.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import { Num, formatNumber } from '../src/ui/Num'

describe('formatNumber', () => {
  test('разделитель дробной части — запятая', () => {
    expect(formatNumber('84.66', 2)).toBe('84,66')
  })

  test('тысячи разделяются неразрывным пробелом', () => {
    expect(formatNumber('1234.5', 1)).toBe('1 234,5')
  })

  test('без указания разрядности хвостовые нули убираются', () => {
    // Выход блюда приходит как "72.000" — показывать «72,000 г» незачем.
    expect(formatNumber('72.000')).toBe('72')
  })

  test('разрядность добивается нулями', () => {
    expect(formatNumber('100', 2)).toBe('100,00')
  })
})

describe('Num', () => {
  test('null даёт прочерк, а не ноль', () => {
    // У 14 блюд из 130 нет цены меню. Прочерк значит «посчитать не из
    // чего»; ноль в той же колонке значил бы «маржа ровно ноль».
    render(<Num value={null} unit="₽" />)
    const znak = screen.getByText('—')
    expect(znak).toHaveClass('num--pusto')
    expect(screen.queryByText(/0/)).not.toBeInTheDocument()
  })

  test('ноль прочерком не притворяется', () => {
    render(<Num value="0" unit="%" />)
    expect(screen.getByText('0 %')).not.toHaveClass('num--pusto')
  })

  test('единица показывается явно', () => {
    render(<Num value="84.66" fraction={2} unit="₽" />)
    expect(screen.getByText('84,66 ₽')).toBeInTheDocument()
  })
})
```

Внимание: между числом и единицей ставится неразрывный пробел. В тестах
он записан обычным пробелом намеренно — Testing Library нормализует
пробелы при поиске по тексту.

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd frontend && npm run test -- num`
Ожидается: FAIL — модуль `../src/ui/Num` не найден.

- [ ] **Шаг 3: Написать `Num.tsx`**

```tsx
import './num.css'

const NERAZRYVNYY = ' '

/**
 * Число из API — в вид, привычный шефу.
 *
 * Значения приходят строками, чтобы не потерять копейки на округлении
 * float. Здесь они строками и обрабатываются: ни одного parseFloat.
 */
export function formatNumber(value: string, fraction?: number): string {
  const minus = value.startsWith('-')
  const [tselaya = '0', drobnaya = ''] = value.replace('-', '').split('.')
  const znaki =
    fraction === undefined
      ? drobnaya.replace(/0+$/, '')
      : drobnaya.padEnd(fraction, '0').slice(0, fraction)
  const gruppy = tselaya.replace(/\B(?=(\d{3})+(?!\d))/g, NERAZRYVNYY)
  return `${minus ? '−' : ''}${gruppy}${znaki ? `,${znaki}` : ''}`
}

type Props = {
  value: string | null
  unit?: string
  fraction?: number
}

export function Num({ value, unit, fraction }: Props) {
  if (value === null || value === '') {
    // Прочерк приглушён и тоньше числа: он обязан быть отличим от нуля с
    // одного взгляда, иначе «маржи нет» и «маржа ноль» сливаются.
    return (
      <span className="num num--pusto" title="значения нет">
        —
      </span>
    )
  }
  return (
    <span className="num">
      {formatNumber(value, fraction)}
      {unit ? NERAZRYVNYY + unit : ''}
    </span>
  )
}
```

`frontend/src/ui/num.css`:

```css
.num {
  font-family: var(--shrift-tsifry);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.num--pusto {
  color: var(--priglushyonnyy);
  opacity: 0.55;
  font-family: var(--shrift);
}
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `cd frontend && npm run test -- num`
Ожидается: PASS, семь тестов.

- [ ] **Шаг 5: Коммит**

```bash
git add frontend/src/ui/Num.tsx frontend/src/ui/num.css frontend/tests/num.test.tsx
git commit -m "Число, прочерк и единица одним местом: ноль и «нет значения» не путаются"
```

---

## Задача 6: `<DataTable>` — таблица на широком, список на узком

**Файлы:**
- Создать: `frontend/src/ui/useWide.ts`, `frontend/src/ui/DataTable.tsx`,
  `frontend/src/ui/table.css`
- Тест: `frontend/tests/tablitsa.test.tsx`

**Интерфейсы:**
- Отдаёт наружу: `type Column<T>`, `<DataTable<T> columns rows rowKey
  empty rowClass? onOpen? />`, `useWide(): boolean`, константа
  `WIDE = '(min-width: 1080px)'`. Ими пользуются задачи 10, 11.

- [ ] **Шаг 1: Написать падающие тесты**

`frontend/tests/tablitsa.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'

import { DataTable, type Column } from '../src/ui/DataTable'
import { setViewport } from './setup'

type Blyudo = { id: string; nazvanie: string; kategoriya: string; uc: string }

const stroki: Blyudo[] = [
  { id: 'B003', nazvanie: 'Кетчуп', kategoriya: 'Соус-топпинг', uc: '5,52 ₽' },
]

const kolonki: Column<Blyudo>[] = [
  { key: 'nazvanie', title: 'Название', priority: 'always', render: (r) => r.nazvanie },
  { key: 'uc', title: 'Себестоимость', priority: 'always', align: 'right', render: (r) => r.uc },
  { key: 'kategoriya', title: 'Категория', priority: 'wide', render: (r) => r.kategoriya },
]

function narisovat() {
  return render(
    <DataTable
      columns={kolonki}
      rows={stroki}
      rowKey={(r) => r.id}
      empty="Ничего не найдено"
    />,
  )
}

test('на широком экране это настоящая таблица со всеми колонками', () => {
  setViewport(1440)
  narisovat()

  expect(screen.getByRole('table')).toBeInTheDocument()
  expect(screen.getByText('Соус-топпинг')).toBeInTheDocument()
})

test('на 360 px видно только главное', () => {
  // Ширина нужна одной задаче — сравнивать многое сразу. Посмотреть одно
  // блюдо узкому экрану не мешает, и второстепенное там только мешает.
  setViewport(360)
  narisovat()

  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(screen.getByText('Кетчуп')).toBeInTheDocument()
  expect(screen.getByText('5,52 ₽')).toBeInTheDocument()
  expect(screen.queryByText('Соус-топпинг')).not.toBeInTheDocument()
})

test('остальное раскрывается по тапу', async () => {
  setViewport(360)
  narisovat()

  await userEvent.click(screen.getByRole('button', { name: /подробнее/i }))

  expect(screen.getByText('Соус-топпинг')).toBeInTheDocument()
})

test('пустой ответ объясняется словами, а не пустотой', () => {
  render(
    <DataTable columns={kolonki} rows={[]} rowKey={(r) => r.id} empty="Ничего не найдено" />,
  )
  expect(screen.getByText('Ничего не найдено')).toBeInTheDocument()
})
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd frontend && npm run test -- tablitsa`
Ожидается: FAIL — модуль `../src/ui/DataTable` не найден.

- [ ] **Шаг 3: Написать `useWide.ts`**

```ts
import { useSyncExternalStore } from 'react'

/** Переключение между таблицей и списком. То же число стоит в table.css. */
export const WIDE = '(min-width: 1080px)'

export function useWide(): boolean {
  return useSyncExternalStore(
    (notify) => {
      const zapros = window.matchMedia(WIDE)
      zapros.addEventListener('change', notify)
      return () => zapros.removeEventListener('change', notify)
    },
    () => window.matchMedia(WIDE).matches,
    // При отрисовке на сервере ширины нет. У нас её нет никогда, но
    // useSyncExternalStore требует третий аргумент.
    () => true,
  )
}
```

- [ ] **Шаг 4: Написать `DataTable.tsx`**

```tsx
import { useState, type ReactNode } from 'react'

import './table.css'
import { useWide } from './useWide'

export type Column<T> = {
  key: string
  title: string
  align?: 'left' | 'right'
  /** always — видно и на телефоне; wide — только на широком и по тапу. */
  priority: 'always' | 'wide'
  render: (row: T) => ReactNode
}

type Props<T> = {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  empty: string
  rowClass?: (row: T) => string | undefined
  onOpen?: (row: T) => void
}

/**
 * Одна реализация на обе ширины.
 *
 * Два дерева разметки разъехались бы на первой же правке, и узкий вариант
 * тихо отстал бы от широкого — а он основной: боты были целиком
 * телефонными, и это надо было вернуть.
 */
export function DataTable<T>(props: Props<T>) {
  const { rows, empty } = props
  const wide = useWide()

  if (rows.length === 0) {
    return <p className="pusto">{empty}</p>
  }
  return wide ? <Shirokaya {...props} /> : <Uzkiy {...props} />
}

function Shirokaya<T>({ columns, rows, rowKey, rowClass, onOpen }: Props<T>) {
  return (
    <div className="tablitsa-obolochka">
      <table className="tablitsa">
        <thead>
          <tr>
            {columns.map((k) => (
              <th key={k.key} className={k.align === 'right' ? 'vpravo' : undefined}>
                {k.title}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={rowClass?.(row)}
              onClick={onOpen ? () => onOpen(row) : undefined}
              tabIndex={onOpen ? 0 : undefined}
              onKeyDown={
                onOpen
                  ? (event) => {
                      if (event.key === 'Enter') onOpen(row)
                    }
                  : undefined
              }
            >
              {columns.map((k) => (
                <td key={k.key} className={k.align === 'right' ? 'vpravo' : undefined}>
                  {k.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Uzkiy<T>({ columns, rows, rowKey, rowClass, onOpen }: Props<T>) {
  const glavnye = columns.filter((k) => k.priority === 'always')
  const ostalnye = columns.filter((k) => k.priority === 'wide')

  return (
    <ul className="spisok">
      {rows.map((row) => (
        <Kartochka
          key={rowKey(row)}
          row={row}
          glavnye={glavnye}
          ostalnye={ostalnye}
          klass={rowClass?.(row)}
          onOpen={onOpen}
        />
      ))}
    </ul>
  )
}

function Kartochka<T>({
  row,
  glavnye,
  ostalnye,
  klass,
  onOpen,
}: {
  row: T
  glavnye: Column<T>[]
  ostalnye: Column<T>[]
  klass?: string
  onOpen?: (row: T) => void
}) {
  const [raskryto, raskryt] = useState(false)

  return (
    <li className={klass}>
      <div className="spisok-glavnoe" onClick={onOpen ? () => onOpen(row) : undefined}>
        {glavnye.map((k) => (
          <span key={k.key} className={k.align === 'right' ? 'vpravo' : undefined}>
            {k.render(row)}
          </span>
        ))}
      </div>
      {ostalnye.length > 0 && (
        <button type="button" className="raskryt" onClick={() => raskryt(!raskryto)}>
          {raskryto ? 'Свернуть' : 'Подробнее'}
        </button>
      )}
      {raskryto && (
        <dl className="spisok-podrobno">
          {ostalnye.map((k) => (
            <div key={k.key}>
              <dt>{k.title}</dt>
              <dd>{k.render(row)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}
```

- [ ] **Шаг 5: Написать `table.css`**

```css
/* Плотность важнее воздуха: на экране должно помещаться 25–30 строк без
   прокрутки. Шеф открывает это сравнивать числа, а не любоваться. */
.tablitsa {
  width: 100%;
  border-collapse: collapse;
  background: var(--poverhnost);
}

.tablitsa th,
.tablitsa td {
  padding: calc(var(--shag) * 1.5) calc(var(--shag) * 2);
  border-bottom: 1px solid var(--granitsa);
  text-align: left;
}

.tablitsa th {
  position: sticky;
  top: 0;
  background: var(--poverhnost);
  font-weight: 600;
  color: var(--priglushyonnyy);
  white-space: nowrap;
}

.tablitsa .vpravo,
.spisok .vpravo {
  text-align: right;
}

/* Прокручиваться вправе только таблица, и только внутри своей обёртки.
   Страница целиком — никогда. */
.tablitsa-obolochka {
  overflow-x: auto;
}

.tablitsa tbody tr:hover {
  background: var(--fon);
}

/* Себестоимость выше цены меню. Почти всегда это перепутанная единица
   измерения, а не убыток, — поэтому подпись направляет к причине. */
.stroka--ubytok {
  background: var(--oshibka-fon);
}

.spisok {
  list-style: none;
  margin: 0;
  padding: 0;
}

.spisok > li {
  background: var(--poverhnost);
  border-bottom: 1px solid var(--granitsa);
  padding: calc(var(--shag) * 2);
}

.spisok-glavnoe {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--shag);
  align-items: baseline;
}

.spisok-glavnoe > span:first-child {
  /* Имена бывают по 53 символа — «Контейнер бумажный без крышки
     207х127х55 крафт/чёрный». Перенос обязателен, обрезка недопустима. */
  overflow-wrap: anywhere;
}

.raskryt {
  margin-top: var(--shag);
  padding: 0;
  border: 0;
  background: none;
  color: var(--aktsent);
  font: inherit;
  cursor: pointer;
}

.spisok-podrobno {
  margin: var(--shag) 0 0;
}

.spisok-podrobno > div {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--shag);
  padding: calc(var(--shag) / 2) 0;
}

.spisok-podrobno dt {
  color: var(--priglushyonnyy);
}

.spisok-podrobno dd {
  margin: 0;
}

.pusto {
  color: var(--priglushyonnyy);
  padding: calc(var(--shag) * 4);
}
```

- [ ] **Шаг 6: Убедиться, что тесты проходят**

Запуск: `cd frontend && npm run test -- tablitsa`
Ожидается: PASS, четыре теста.

- [ ] **Шаг 7: Коммит**

```bash
git add frontend/src/ui/ frontend/tests/tablitsa.test.tsx
git commit -m "Таблица и список — одна компонента: узкий вариант не отстанет"
```

---

## Задача 7: контракты и клиент API

**Файлы:**
- Создать: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`,
  `frontend/src/api/queries.ts`
- Тест: `frontend/tests/client.test.ts`

**Интерфейсы:**
- Отдаёт наружу: типы `Me`, `Ingredient`, `Dish`, `DishDetail`,
  `Component`, `Reconciliation`, `ReconciliationRow`, `Candidate`;
  `ApiError` с полем `status: number`; `api<T>(path, init?): Promise<T>`;
  хуки `useMe`, `useIngredients`, `useDishes`, `useDish`,
  `useReconciliation`. Ими пользуются задачи 8, 10–13.

- [ ] **Шаг 1: Написать `types.ts`**

Типы списаны с раздела 4 ТЗ дословно. Все числа — строки.

```ts
/** Контракты ручек. Числа приходят строками, чтобы не потерять копейки
 *  на округлении float, и строками остаются: арифметики над ними здесь
 *  нет и быть не должно. */

export type Me = {
  email: string
  display_name: string
  roles: string[]
}

export type Ingredient = {
  id: number
  /** Идентификатор из Google-таблицы. Шеф знает в лицо именно его. */
  legacy_id: string
  name: string
  category: string
  /** «кг», «шт», «л», «мл». Определяет смысл price_per_kg. */
  unit: string
  /** Строка из таблицы, а не перечисление: «активный» у ингредиентов,
   *  «активное» у блюд. Значения фильтра берутся из ответа. */
  status: string
  /** При unit «шт» это цена за штуку, несмотря на имя поля. */
  price_per_kg: string | null
  /** Только у штучных. У весовых null — это норма, а не пропуск. */
  weight_per_piece_g: string | null
  has_card: boolean
}

export type Dish = {
  legacy_id: string
  name: string
  category: string
  status: string
  price_menu: string | null
  uc_rub: string
  uc_percent: string | null
  margin_percent: string | null
  output_grams: string
  warnings: number
}

export type Component = {
  name: string
  /** «Короткое для айки». Бывает пустым — тогда показывается name. */
  short_name: string
  row_type: 'main' | 'packaging'
  unit: string
  net_weight_g: string
  /** У упаковки брутто нет: в выход блюда она не входит. */
  gross_weight_g: string | null
  price_per_unit: string | null
  cost_rub: string
  share_percent: string | null
}

export type DishDetail = Dish & {
  protein_g: string
  fat_g: string
  carbs_g: string
  kcal: string
  /** Доля веса состава с заполненным КБЖУ, 0–1. Ниже 0.5 цифрам верить нельзя. */
  kbju_coverage: string
  components: Component[]
  warning_texts: string[]
}

export type Candidate = {
  ingredient_id: number
  legacy_id: string
  name: string
  /** Степень похожести 0–1. Бэкенд пока отдаёт null. */
  score: number | null
}

export type LinkStatus = 'ambiguous' | 'candidate' | 'orphan'

export type ReconciliationRow = {
  card_id: number
  name: string
  link_status: LinkStatus
  supplier: string
  candidates: Candidate[]
}

export type Reconciliation = {
  total: number
  linked: number
  needs_human: number
  rows: ReconciliationRow[]
}
```

- [ ] **Шаг 2: Написать падающие тесты клиента**

`frontend/tests/client.test.ts`:

```ts
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, expect, test, vi } from 'vitest'

import { ApiError, api } from '../src/api/client'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

test('401 приводит к продлению и повтору запроса', async () => {
  let dano = false
  const prodleniya = vi.fn()

  server.use(
    http.get('/api/dishes', () => {
      if (!dano) return new HttpResponse(null, { status: 401 })
      return HttpResponse.json([{ legacy_id: 'B001' }])
    }),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      dano = true
      return HttpResponse.json({ email: 'chef@example.com' })
    }),
  )

  const otvet = await api<{ legacy_id: string }[]>('/dishes')

  expect(prodleniya).toHaveBeenCalledTimes(1)
  expect(otvet[0]!.legacy_id).toBe('B001')
})

test('второй 401 подряд не крутит цикл, а признаёт поражение', async () => {
  // Цикл «401 → продление → 401 → продление» при протухшем refresh-токене
  // крутился бы вечно и выглядел бы как зависание.
  const prodleniya = vi.fn()
  server.use(
    http.get('/api/dishes', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      return new HttpResponse(null, { status: 401 })
    }),
  )

  await expect(api('/dishes')).rejects.toMatchObject({ status: 401 })
  expect(prodleniya).toHaveBeenCalledTimes(1)
})

test('параллельные 401 дают одно продление, а не три', async () => {
  // Три запроса при открытии экрана не должны давать три продления, из
  // которых два отвергнутся вращением refresh-токена.
  let dano = false
  const prodleniya = vi.fn()
  server.use(
    http.get('/api/dishes', () =>
      dano ? HttpResponse.json([]) : new HttpResponse(null, { status: 401 }),
    ),
    http.get('/api/ingredients', () =>
      dano ? HttpResponse.json([]) : new HttpResponse(null, { status: 401 }),
    ),
    http.get('/api/me', () =>
      dano ? HttpResponse.json({}) : new HttpResponse(null, { status: 401 }),
    ),
    http.post('/api/auth/refresh', async () => {
      prodleniya()
      dano = true
      return HttpResponse.json({})
    }),
  )

  await Promise.all([api('/dishes'), api('/ingredients'), api('/me')])

  expect(prodleniya).toHaveBeenCalledTimes(1)
})

test('403 продлением не лечится и наверх идёт как есть', async () => {
  // 401 — «представьтесь», 403 — «представились, но нельзя». Отправлять на
  // форму входа при 403 значит гонять человека по кругу.
  const prodleniya = vi.fn()
  server.use(
    http.get('/api/dishes', () =>
      HttpResponse.json({ detail: 'нужна роль: chef' }, { status: 403 }),
    ),
    http.post('/api/auth/refresh', () => {
      prodleniya()
      return HttpResponse.json({})
    }),
  )

  await expect(api('/dishes')).rejects.toBeInstanceOf(ApiError)
  await expect(api('/dishes')).rejects.toMatchObject({
    status: 403,
    message: 'нужна роль: chef',
  })
  expect(prodleniya).not.toHaveBeenCalled()
})

test('обрыв сети даёт понятную ошибку, а не пустой экран', async () => {
  server.use(http.get('/api/dishes', () => HttpResponse.error()))

  await expect(api('/dishes')).rejects.toMatchObject({ status: 0 })
})
```

- [ ] **Шаг 3: Убедиться, что тесты падают**

Запуск: `cd frontend && npm run test -- client`
Ожидается: FAIL — модуль `../src/api/client` не найден.

- [ ] **Шаг 4: Написать `client.ts`**

```ts
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** Идущее продление. Общее на все запросы: три запроса при открытии
 *  экрана не должны давать три продления, из которых два отвергнутся
 *  вращением refresh-токена. */
let prodlenie: Promise<boolean> | null = null

function prodlit(): Promise<boolean> {
  prodlenie ??= fetch('/api/auth/refresh', { method: 'POST', credentials: 'include' })
    .then((otvet) => otvet.ok)
    .catch(() => false)
    .finally(() => {
      prodlenie = null
    })
  return prodlenie
}

async function poyasnenie(otvet: Response): Promise<string> {
  try {
    const telo = (await otvet.json()) as { detail?: unknown }
    if (typeof telo.detail === 'string') return telo.detail
  } catch {
    // Тело не JSON — бывает у 502 от nginx. Это не повод падать.
  }
  return 'Не удалось получить данные'
}

/**
 * Запрос к API.
 *
 * 401 лечится однократным продлением и повтором. Один раз, не в цикле:
 * при протухшем refresh-токене цикл крутился бы вечно и выглядел бы как
 * зависание. 403 продлением не лечится и уходит наверх как есть.
 */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const zapros = (): Promise<Response> =>
    fetch(`/api${path}`, { ...init, credentials: 'include' })

  let otvet: Response
  try {
    otvet = await zapros()
  } catch (oshibka) {
    throw new ApiError(0, 'Нет связи с сервером')
  }

  if (otvet.status === 401 && (await prodlit())) {
    try {
      otvet = await zapros()
    } catch {
      throw new ApiError(0, 'Нет связи с сервером')
    }
  }

  if (!otvet.ok) {
    throw new ApiError(otvet.status, await poyasnenie(otvet))
  }
  if (otvet.status === 204) {
    return undefined as T
  }
  return (await otvet.json()) as T
}
```

Переменная `oshibka` не используется — заменить на `catch {`.

- [ ] **Шаг 5: Написать `queries.ts`**

```ts
import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { api } from './client'
import type { Dish, DishDetail, Ingredient, Me, Reconciliation } from './types'

export function useMe(): UseQueryResult<Me> {
  return useQuery({ queryKey: ['me'], queryFn: () => api<Me>('/me') })
}

function stroka(params: Record<string, string>): string {
  const chistye = Object.entries(params).filter(([, v]) => v !== '')
  return chistye.length ? `?${new URLSearchParams(chistye).toString()}` : ''
}

export function useIngredients(search = '', status = ''): UseQueryResult<Ingredient[]> {
  return useQuery({
    queryKey: ['ingredients', search, status],
    queryFn: () => api<Ingredient[]>(`/ingredients${stroka({ search, status })}`),
  })
}

export function useDishes(search = '', status = ''): UseQueryResult<Dish[]> {
  return useQuery({
    queryKey: ['dishes', search, status],
    queryFn: () => api<Dish[]>(`/dishes${stroka({ search, status })}`),
  })
}

export function useDish(legacyId: string): UseQueryResult<DishDetail> {
  return useQuery({
    queryKey: ['dish', legacyId],
    queryFn: () => api<DishDetail>(`/dishes/${encodeURIComponent(legacyId)}`),
  })
}

export function useReconciliation(): UseQueryResult<Reconciliation> {
  return useQuery({
    queryKey: ['reconciliation'],
    queryFn: () => api<Reconciliation>('/reconciliation'),
  })
}
```

- [ ] **Шаг 6: Убедиться, что тесты проходят**

Запуск: `cd frontend && npm run test -- client && npm run types`
Ожидается: PASS, пять тестов; `tsc` молчит.

- [ ] **Шаг 7: Коммит**

```bash
git add frontend/src/api/ frontend/tests/client.test.ts
git commit -m "Клиент API: одно продление на все запросы, 403 наверх как есть"
```

---

## Задача 8: сессия, вход и защита маршрутов

**Файлы:**
- Создать: `frontend/src/auth/session.tsx`, `frontend/src/auth/LoginPage.tsx`,
  `frontend/src/auth/auth.css`
- Правка: `frontend/src/App.tsx`
- Тест: `frontend/tests/vhod.test.tsx`

**Интерфейсы:**
- Отдаёт наружу: `<SessionProvider>`, `useSession(): { me: Me | null;
  loading: boolean; login(email, password): Promise<void>; logout(): Promise<void> }`,
  `<LoginPage />`. Ими пользуются задачи 9–13.

- [ ] **Шаг 1: Написать падающие тесты**

`frontend/tests/vhod.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { App } from '../src/App'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat(putj = '/dishes') {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={[putj]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('без сессии показывается форма входа', async () => {
  server.use(
    http.get('/api/me', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
  )

  narisovat()

  expect(await screen.findByLabelText('Почта')).toBeInTheDocument()
  expect(screen.queryByText(/зарегистрироваться/i)).not.toBeInTheDocument()
  expect(screen.queryByText(/забыли пароль/i)).not.toBeInTheDocument()
})

test('неверная пара показывается текстом у формы, а не всплывашкой', async () => {
  server.use(
    http.get('/api/me', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/login', () =>
      HttpResponse.json({ detail: 'Неверная почта или пароль' }, { status: 401 }),
    ),
  )

  narisovat()
  await userEvent.type(await screen.findByLabelText('Почта'), 'chef@example.com')
  await userEvent.type(screen.getByLabelText('Пароль'), 'не тот')
  await userEvent.click(screen.getByRole('button', { name: 'Войти' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Неверная почта или пароль')
})

test('лежащая служба входа названа своим именем', async () => {
  // Иначе шеф при лежащем Supabase будет перебирать пароли.
  server.use(
    http.get('/api/me', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/refresh', () => new HttpResponse(null, { status: 401 })),
    http.post('/api/auth/login', () =>
      HttpResponse.json({ detail: 'Служба входа не отвечает' }, { status: 502 }),
    ),
  )

  narisovat()
  await userEvent.type(await screen.findByLabelText('Почта'), 'chef@example.com')
  await userEvent.type(screen.getByLabelText('Пароль'), 'пароль')
  await userEvent.click(screen.getByRole('button', { name: 'Войти' }))

  expect(await screen.findByRole('alert')).toHaveTextContent('Служба входа не отвечает')
})

test('403 форму входа не показывает', async () => {
  // Человек уже представился. Повторный вход вернёт ровно то же самое —
  // это круг, из которого он не выйдет.
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ detail: 'профиль не заведён — обратитесь к администратору' }, { status: 403 }),
    ),
  )

  narisovat()

  expect(await screen.findByText(/профиль не заведён/)).toBeInTheDocument()
  expect(screen.queryByLabelText('Почта')).not.toBeInTheDocument()
})

test('вошедший видит своё имя и роль в шапке', async () => {
  server.use(
    http.get('/api/me', () =>
      HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
    ),
    http.get('/api/dishes', () => HttpResponse.json([])),
  )

  narisovat()

  await waitFor(() => expect(screen.getByText('Алексей')).toBeInTheDocument())
  expect(screen.getByText(/бренд-шеф/i)).toBeInTheDocument()
})
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd frontend && npm run test -- vhod`
Ожидается: FAIL — формы входа нет, `App` рисует заглушку.

- [ ] **Шаг 3: Написать `session.tsx`**

```tsx
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'

import { ApiError, api } from '../api/client'
import type { Me } from '../api/types'

type Sostoyanie = {
  me: Me | null
  loading: boolean
  /** Отказ по правам. 401 сюда не попадает — он означает «покажи форму». */
  otkaz: string | null
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const Kontekst = createContext<Sostoyanie | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  const [otkaz, setOtkaz] = useState<string | null>(null)

  useEffect(() => {
    let zhiv = true
    api<Me>('/me')
      .then((profil) => zhiv && setMe(profil))
      .catch((oshibka: unknown) => {
        // 403 — не повод показывать форму: человек уже представился.
        if (zhiv && oshibka instanceof ApiError && oshibka.status === 403) {
          setOtkaz(oshibka.message)
        }
      })
      .finally(() => zhiv && setLoading(false))
    return () => {
      zhiv = false
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const profil = await api<Me>('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    setOtkaz(null)
    setMe(profil)
  }, [])

  const logout = useCallback(async () => {
    await api<void>('/auth/logout', { method: 'POST' })
    setMe(null)
  }, [])

  return (
    <Kontekst.Provider value={{ me, loading, otkaz, login, logout }}>{children}</Kontekst.Provider>
  )
}

export function useSession(): Sostoyanie {
  const znachenie = useContext(Kontekst)
  if (!znachenie) throw new Error('useSession вне SessionProvider')
  return znachenie
}

/** Названия ролей для шапки. Коды приходят с бэкенда, показывать их человеку
 *  незачем. */
export const ROLI: Record<string, string> = {
  chef: 'бренд-шеф',
  cook: 'повар',
  commerce: 'коммерческий отдел',
  developer: 'разработчик',
}
```

- [ ] **Шаг 4: Написать `LoginPage.tsx`**

```tsx
import { useState, type FormEvent } from 'react'

import { ApiError } from '../api/client'
import './auth.css'
import { useSession } from './session'

/**
 * Почта, пароль, кнопка.
 *
 * Ссылок «зарегистрироваться» и «забыли пароль» нет: доступ заводит
 * администратор скриптом grant_access.py, пароль сбрасывает он же.
 */
export function LoginPage() {
  const { login } = useSession()
  const [email, setEmail] = useState('')
  const [parol, setParol] = useState('')
  const [oshibka, setOshibka] = useState<string | null>(null)
  const [idyot, setIdyot] = useState(false)

  async function otpravit(event: FormEvent) {
    event.preventDefault()
    setOshibka(null)
    setIdyot(true)
    try {
      await login(email, parol)
    } catch (prichina: unknown) {
      setOshibka(
        prichina instanceof ApiError ? prichina.message : 'Не удалось войти',
      )
    } finally {
      setIdyot(false)
    }
  }

  return (
    <main className="vhod">
      <form className="vhod-forma" onSubmit={otpravit}>
        <h1>Кухня</h1>
        <label htmlFor="pochta">Почта</label>
        <input
          id="pochta"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <label htmlFor="parol">Пароль</label>
        <input
          id="parol"
          type="password"
          autoComplete="current-password"
          value={parol}
          onChange={(e) => setParol(e.target.value)}
          required
        />
        {/* Ошибку показываем текстом у формы, не всплывашкой: всплывашка
            уезжает раньше, чем человек успевает прочитать. */}
        {oshibka && (
          <p className="vhod-oshibka" role="alert">
            {oshibka}
          </p>
        )}
        <button type="submit" disabled={idyot}>
          Войти
        </button>
      </form>
    </main>
  )
}
```

`frontend/src/auth/auth.css`:

```css
.vhod {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: calc(var(--shag) * 4);
}

.vhod-forma {
  display: grid;
  gap: var(--shag);
  width: min(320px, 100%);
  background: var(--poverhnost);
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  padding: calc(var(--shag) * 6);
}

.vhod-forma h1 {
  margin: 0 0 calc(var(--shag) * 2);
  font-size: 20px;
}

.vhod-forma label {
  color: var(--priglushyonnyy);
}

.vhod-forma input {
  padding: calc(var(--shag) * 2);
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  font: inherit;
}

.vhod-forma button {
  margin-top: calc(var(--shag) * 2);
  padding: calc(var(--shag) * 2);
  border: 0;
  border-radius: var(--radius);
  background: var(--aktsent);
  color: #fff;
  font: inherit;
  cursor: pointer;
}

.vhod-oshibka {
  margin: 0;
  color: var(--oshibka);
}
```

- [ ] **Шаг 5: Переписать `App.tsx`**

```tsx
import { Navigate, Route, Routes } from 'react-router-dom'

import { LoginPage } from './auth/LoginPage'
import { SessionProvider, useSession } from './auth/session'
import { Layout } from './shell/Layout'

function Marshruty() {
  const { me, loading, otkaz } = useSession()

  if (loading) return <p className="zagruzka">Загрузка…</p>

  // Отказ по правам формы входа не показывает: человек уже представился,
  // и повторный вход вернёт ровно то же самое.
  if (otkaz) {
    return (
      <main className="otkaz" role="alert">
        <h1>Доступа нет</h1>
        <p>{otkaz}</p>
      </main>
    )
  }

  if (!me) return <LoginPage />

  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/dishes" replace />} />
      </Route>
    </Routes>
  )
}

export function App() {
  return (
    <SessionProvider>
      <Marshruty />
    </SessionProvider>
  )
}
```

Маршруты экранов дописывает задача 9 вместе с `Layout`. Чтобы задача 8
проверялась отдельно, временно создайте `frontend/src/shell/Layout.tsx`
минимальным:

```tsx
import { Outlet } from 'react-router-dom'

import { ROLI, useSession } from '../auth/session'

export function Layout() {
  const { me } = useSession()
  return (
    <>
      <header>
        <span>{me?.display_name}</span>
        <span>{me?.roles.map((kod) => ROLI[kod] ?? kod).join(', ')}</span>
      </header>
      <Outlet />
    </>
  )
}
```

- [ ] **Шаг 6: Убедиться, что тесты проходят**

Запуск: `cd frontend && npm run test -- vhod`
Ожидается: PASS, пять тестов.

Внимание к последнему тесту: он ждёт `/dishes`, а маршрут пока
перенаправляет на него и рисует пустоту. Проверяется только шапка — этого
достаточно, экран блюд появится в задаче 11.

- [ ] **Шаг 7: Коммит**

```bash
git add frontend/src/auth/ frontend/src/App.tsx frontend/src/shell/ frontend/tests/vhod.test.tsx
git commit -m "Вход, сессия и разделение 401 с 403"
```

---

## Задача 9: оболочка — шапка, меню, заглушки

**Файлы:**
- Правка: `frontend/src/shell/Layout.tsx`
- Создать: `frontend/src/shell/Nav.tsx`, `frontend/src/shell/shell.css`,
  `frontend/src/shell/razdely.ts`, `frontend/src/pages/Stub.tsx`,
  `frontend/src/ui/Icons.tsx`
- Правка: `frontend/src/App.tsx`
- Тест: `frontend/tests/obolochka.test.tsx`

**Интерфейсы:**
- Отдаёт наружу: `RAZDELY: Razdel[]` из `razdely.ts`, где
  `type Razdel = { put: string; nazvanie: string; faza?: string; opisanie?: string }`;
  `<Layout />`, `<Stub />`.

- [ ] **Шаг 1: Написать падающие тесты**

`frontend/tests/obolochka.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { App } from '../src/App'
import { RAZDELY } from '../src/shell/razdely'

const server = setupServer(
  http.get('/api/me', () =>
    HttpResponse.json({ email: 'chef@example.com', display_name: 'Алексей', roles: ['chef'] }),
  ),
  http.get('/api/dishes', () => HttpResponse.json([])),
  http.get('/api/ingredients', () => HttpResponse.json([])),
  http.get('/api/reconciliation', () =>
    HttpResponse.json({ total: 0, linked: 0, needs_human: 0, rows: [] }),
  ),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat(putj = '/dishes') {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={[putj]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('в меню видны все восемь разделов, включая будущие', async () => {
  // Оболочка проектируется один раз и должна знать про все разделы —
  // иначе меню придётся переделывать при появлении каждого следующего.
  narisovat()
  await screen.findByText('Алексей')

  expect(RAZDELY).toHaveLength(8)
  for (const razdel of RAZDELY) {
    expect(screen.getByRole('link', { name: razdel.nazvanie })).toBeInTheDocument()
  }
})

test('заглушка открывается и говорит, когда раздел появится', async () => {
  narisovat()
  await screen.findByText('Алексей')

  await userEvent.click(screen.getByRole('link', { name: 'Дегустации' }))

  expect(screen.getByRole('heading', { name: 'Дегустации' })).toBeInTheDocument()
  expect(screen.getByText(/фаза 4/i)).toBeInTheDocument()
})

test('выход есть и он в шапке', async () => {
  narisovat()
  await screen.findByText('Алексей')

  expect(screen.getByRole('button', { name: 'Выйти' })).toBeInTheDocument()
})
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd frontend && npm run test -- obolochka`
Ожидается: FAIL — модуля `razdely` нет.

- [ ] **Шаг 3: Написать `razdely.ts`**

```ts
/**
 * Карта приложения. Оболочка проектируется один раз, поэтому знает про все
 * разделы, включая ещё не сделанные: иначе меню придётся переделывать при
 * появлении каждого следующего.
 *
 * Вход и карточка блюда пунктами меню не являются, хотя в карте 1.1 ТЗ
 * стоят строками.
 */
export type Razdel = {
  put: string
  nazvanie: string
  /** Заполнено — раздел ещё не сделан, открывается заглушкой. */
  faza?: string
  opisanie?: string
}

export const RAZDELY: Razdel[] = [
  { put: '/ingredients', nazvanie: 'Справочник' },
  { put: '/dishes', nazvanie: 'Блюда' },
  { put: '/reconciliation', nazvanie: 'Сверка справочника' },
  {
    put: '/pricing',
    nazvanie: 'Расчётка',
    faza: 'фаза 3',
    opisanie:
      'Сводная таблица, по которой коммерческий отдел назначает розничные цены: ' +
      'цена, себестоимость в рублях и процентах, маржа, вес, КБЖУ. Появится, ' +
      'когда откроется запись в листы.',
  },
  {
    put: '/cards',
    nazvanie: 'Карточки ингредиентов',
    faza: 'фаза 3',
    opisanie:
      'Пошаговый поток для повара: снять этикетку телефоном, распознать состав ' +
      'и сроки, поправить, сфотографировать продукт, отправить на согласование.',
  },
  {
    put: '/chat',
    nazvanie: 'Чат с ассистентом',
    faza: 'фаза 3',
    opisanie:
      'Свободный вопрос текстом: «посчитай UC чизбургера», «что подорожает, если ' +
      'говядина вырастет на 20 %». Ответ приходит потоком, и таблицы внутри него ' +
      'будут настоящими таблицами, а не пробелами в моноширинном блоке.',
  },
  {
    put: '/competitors',
    nazvanie: 'Конкуренты',
    faza: 'фаза 4',
    opisanie:
      'Еженедельный срез меню восьми сайтов и история изменений цен. В архиве ' +
      '6775 позиций и 675 изменений с июля.',
  },
  {
    put: '/tastings',
    nazvanie: 'Дегустации',
    faza: 'фаза 4',
    opisanie:
      'Слепые дегустации: жюри заходят по ссылке без учётной записи и оценивают ' +
      'с телефона, держа в другой руке образец. Шефу — экран прогресса.',
  },
]
```

- [ ] **Шаг 4: Написать `Nav.tsx`, `Layout.tsx`, `Stub.tsx`**

`frontend/src/shell/Nav.tsx`:

```tsx
import { NavLink } from 'react-router-dom'

import { RAZDELY } from './razdely'

export function Nav({ onGo }: { onGo?: () => void }) {
  return (
    <nav className="menyu" aria-label="Разделы">
      {RAZDELY.map((razdel) => (
        <NavLink
          key={razdel.put}
          to={razdel.put}
          onClick={onGo}
          className={({ isActive }) => (isActive ? 'menyu-punkt menyu-punkt--tekushchiy' : 'menyu-punkt')}
        >
          {razdel.nazvanie}
          {razdel.faza && <span className="menyu-faza">{razdel.faza}</span>}
        </NavLink>
      ))}
    </nav>
  )
}
```

`frontend/src/shell/Layout.tsx` — заменить целиком:

```tsx
import { useState } from 'react'
import { Outlet } from 'react-router-dom'

import { ROLI, useSession } from '../auth/session'
import { Nav } from './Nav'
import './shell.css'

export function Layout() {
  const { me, logout } = useSession()
  const [menyuOtkryto, otkryt] = useState(false)

  return (
    <div className="obolochka">
      <header className="shapka">
        <button
          type="button"
          className="shapka-menyu"
          aria-label="Разделы"
          aria-expanded={menyuOtkryto}
          onClick={() => otkryt(!menyuOtkryto)}
        >
          ≡
        </button>
        <span className="shapka-nazvanie">Кухня</span>
        <span className="shapka-kto">
          <b>{me?.display_name}</b>
          <span className="shapka-rol">
            {me?.roles.map((kod) => ROLI[kod] ?? kod).join(', ')}
          </span>
        </span>
        <button type="button" className="shapka-vyhod" onClick={() => void logout()}>
          Выйти
        </button>
      </header>

      <div className="obolochka-telo">
        <aside className={menyuOtkryto ? 'bok bok--otkryt' : 'bok'}>
          <Nav onGo={() => otkryt(false)} />
        </aside>
        <main className="soderzhimoe">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
```

`frontend/src/pages/Stub.tsx`:

```tsx
import { useLocation } from 'react-router-dom'

import { RAZDELY } from '../shell/razdely'

/** Раздел, которого ещё нет. Пустая страница молчит о том, когда он
 *  появится, — а это единственное, что здесь можно сказать полезного. */
export function Stub() {
  const { pathname } = useLocation()
  const razdel = RAZDELY.find((r) => r.put === pathname)

  return (
    <section className="zaglushka">
      <h1>{razdel?.nazvanie ?? 'Раздел'}</h1>
      <p className="zaglushka-faza">Появится в: {razdel?.faza ?? 'позже'}</p>
      <p>{razdel?.opisanie}</p>
    </section>
  )
}
```

- [ ] **Шаг 5: Написать `shell.css`**

```css
.obolochka {
  min-height: 100vh;
}

.shapka {
  display: flex;
  align-items: center;
  gap: calc(var(--shag) * 2);
  padding: calc(var(--shag) * 2) calc(var(--shag) * 3);
  background: var(--poverhnost);
  border-bottom: 1px solid var(--granitsa);
}

.shapka-nazvanie {
  font-weight: 600;
}

.shapka-kto {
  margin-left: auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  line-height: 1.2;
}

.shapka-rol {
  color: var(--priglushyonnyy);
  font-size: 12px;
}

.shapka-vyhod,
.shapka-menyu {
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  background: none;
  padding: var(--shag) calc(var(--shag) * 2);
  font: inherit;
  cursor: pointer;
}

.obolochka-telo {
  display: flex;
  align-items: flex-start;
}

.bok {
  display: none;
  width: 220px;
  flex: none;
  padding: calc(var(--shag) * 2);
}

.bok--otkryt {
  display: block;
  width: 100%;
}

.menyu {
  display: grid;
  gap: 2px;
}

.menyu-punkt {
  display: flex;
  justify-content: space-between;
  gap: var(--shag);
  padding: calc(var(--shag) * 2);
  border-radius: var(--radius);
  color: inherit;
  text-decoration: none;
}

.menyu-punkt--tekushchiy {
  background: var(--fon);
  font-weight: 600;
}

.menyu-faza {
  color: var(--priglushyonnyy);
  font-size: 12px;
}

.soderzhimoe {
  flex: 1;
  min-width: 0;
  padding: calc(var(--shag) * 3);
}

.zaglushka-faza {
  color: var(--vnimanie);
}

/* Кнопка меню нужна только там, где меню спрятано. */
@media (min-width: 1080px) {
  .shapka-menyu {
    display: none;
  }
  .bok {
    display: block;
    width: 220px;
  }
}
```

- [ ] **Шаг 6: Дописать маршруты в `App.tsx`**

Заменить блок `<Routes>`:

```tsx
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Navigate to="/dishes" replace />} />
        <Route path="ingredients" element={<Ingredients />} />
        <Route path="dishes" element={<Dishes />} />
        <Route path="dishes/:legacyId" element={<DishDetailPage />} />
        <Route path="reconciliation" element={<Reconciliation />} />
        {RAZDELY.filter((r) => r.faza).map((r) => (
          <Route key={r.put} path={r.put.slice(1)} element={<Stub />} />
        ))}
        <Route path="*" element={<Navigate to="/dishes" replace />} />
      </Route>
    </Routes>
```

Экраны `Ingredients`, `Dishes`, `DishDetailPage`, `Reconciliation`
появятся в задачах 10–13. Чтобы задача 9 проверялась отдельно, создайте
их временными заглушками в `frontend/src/pages/`:

```tsx
export function Ingredients() {
  return <p>Справочник</p>
}
```

и так же остальные три. Задачи 10–13 заменят их содержимым.

- [ ] **Шаг 7: Убедиться, что тесты проходят**

Запуск: `cd frontend && npm run test && npm run types && npm run lint`
Ожидается: PASS, все наборы; `tsc` и eslint молчат.

- [ ] **Шаг 8: Коммит**

```bash
git add frontend/src/shell/ frontend/src/pages/ frontend/src/App.tsx frontend/tests/obolochka.test.tsx
git commit -m "Оболочка: шапка, меню на восемь разделов, заглушки с описанием"
```

---

## Задача 10: экран справочника ингредиентов

**Файлы:**
- Правка: `frontend/src/pages/Ingredients.tsx`
- Создать: `frontend/src/ui/Filtry.tsx`, `frontend/src/ui/Sostoyanie.tsx`,
  `frontend/src/pages/pages.css`
- Тест: `frontend/tests/spravochnik.test.tsx`

**Интерфейсы:**
- Отдаёт наружу: `<Filtry search onSearch status onStatus statusy />`,
  `<Sostoyanie query />` — показ загрузки и ошибки. Ими пользуются
  задачи 11–13.

- [ ] **Шаг 1: Написать падающие тесты**

`frontend/tests/spravochnik.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { Ingredients } from '../src/pages/Ingredients'
import { setViewport } from './setup'

// Ответ настоящий: тот самый случай с двумя «Сахарами», из-за которого
// сверка не может решить сама.
const SAHAR = [
  {
    id: 12,
    legacy_id: '12',
    name: 'Сахар',
    category: 'Бакалея',
    unit: 'кг',
    status: 'активный',
    price_per_kg: '100.00',
    weight_per_piece_g: null,
    has_card: false,
  },
  {
    id: 123,
    legacy_id: '123',
    name: 'Сахар',
    category: 'Бакалея',
    unit: 'шт',
    status: 'архивный',
    price_per_kg: '0.00',
    weight_per_piece_g: null,
    has_card: true,
  },
]

const server = setupServer(http.get('/api/ingredients', () => HttpResponse.json(SAHAR)))

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <Ingredients />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('показывает legacy_id, а не внутренний', async () => {
  // Шеф знает в лицо идентификатор из таблицы. Внутренний ему не говорит
  // ничего и только путает.
  narisovat()
  expect(await screen.findAllByText('Сахар')).toHaveLength(2)
  expect(screen.getByText('123')).toBeInTheDocument()
})

test('единица определяет смысл цены', async () => {
  // price_per_kg при unit «шт» означает цену за штуку, несмотря на имя поля.
  narisovat()
  await screen.findAllByText('Сахар')
  expect(screen.getByText('100 ₽/кг')).toBeInTheDocument()
  expect(screen.getByText('0 ₽/шт')).toBeInTheDocument()
})

test('вес штуки у весового — прочерк, а не ноль', async () => {
  narisovat()
  await screen.findAllByText('Сахар')
  expect(screen.getAllByTitle('значения нет').length).toBeGreaterThan(0)
})

test('архивные не прячутся, а помечаются', async () => {
  // Они стоят в составе живых блюд, и без них не разобрать, почему у
  // блюда такая себестоимость.
  narisovat()
  await screen.findAllByText('Сахар')
  expect(screen.getByText('архивный')).toBeInTheDocument()
})

test('отсутствие карточки — видимый сигнал', async () => {
  narisovat()
  await screen.findAllByText('Сахар')
  expect(screen.getByLabelText('карточки нет')).toBeInTheDocument()
  expect(screen.getByLabelText('карточка есть')).toBeInTheDocument()
})

test('на 360 px остаются имя, цена и отметка карточки', async () => {
  setViewport(360)
  narisovat()
  await screen.findAllByText('Сахар')

  expect(screen.getByText('100 ₽/кг')).toBeInTheDocument()
  expect(screen.queryByText('Бакалея')).not.toBeInTheDocument()
})
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd frontend && npm run test -- spravochnik`
Ожидается: FAIL — экран пока заглушка, текста «Сахар» нет.

- [ ] **Шаг 3: Написать `Sostoyanie.tsx` и `Filtry.tsx`**

```tsx
// frontend/src/ui/Sostoyanie.tsx
import type { UseQueryResult } from '@tanstack/react-query'

import { ApiError } from '../api/client'

/**
 * Загрузка и отказ — словами.
 *
 * Молчаливо пустой экран запрещён: пустой справочник и недоступный
 * справочник выглядят одинаково, а значат разное.
 */
export function Sostoyanie({ query }: { query: UseQueryResult<unknown> }) {
  if (query.isPending) return <p className="zagruzka">Загрузка…</p>
  if (!query.isError) return null

  const oshibka = query.error
  const otkaz = oshibka instanceof ApiError && oshibka.status === 403

  return (
    <div className="sboy" role="alert">
      <p>{otkaz ? 'Доступа нет' : 'Не удалось получить данные'}</p>
      <p className="sboy-prichina">
        {oshibka instanceof Error ? oshibka.message : 'неизвестная ошибка'}
      </p>
      {/* Повторять запрос, отвергнутый по правам, бессмысленно. */}
      {!otkaz && (
        <button type="button" onClick={() => void query.refetch()}>
          Повторить
        </button>
      )}
    </div>
  )
}
```

```tsx
// frontend/src/ui/Filtry.tsx
type Props = {
  search: string
  onSearch: (znachenie: string) => void
  status: string
  onStatus: (znachenie: string) => void
  /** Значения берутся из ответа, а не зашиваются в код: у ингредиентов
   *  «активный», у блюд «активное» — это данные шефа, а не перечисление. */
  statusy: string[]
  vsego?: number
}

export function Filtry({ search, onSearch, status, onStatus, statusy, vsego }: Props) {
  return (
    <div className="filtry">
      <input
        type="search"
        placeholder="Поиск по названию"
        aria-label="Поиск по названию"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
      />
      <select aria-label="Статус" value={status} onChange={(e) => onStatus(e.target.value)}>
        <option value="">все статусы</option>
        {statusy.map((znachenie) => (
          <option key={znachenie} value={znachenie}>
            {znachenie}
          </option>
        ))}
      </select>
      {vsego !== undefined && <span className="filtry-schyot">{vsego}</span>}
    </div>
  )
}
```

- [ ] **Шаг 4: Написать `Ingredients.tsx`**

```tsx
import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

import { useIngredients } from '../api/queries'
import type { Ingredient } from '../api/types'
import { DataTable, type Column } from '../ui/DataTable'
import { Filtry } from '../ui/Filtry'
import { Num } from '../ui/Num'
import { Sostoyanie } from '../ui/Sostoyanie'
import './pages.css'

export function Ingredients() {
  // Поиск и фильтр живут в адресе: ссылку на отфильтрованный список шеф
  // шлёт в переписке, и она должна открываться тем же экраном.
  const [params, setParams] = useSearchParams()
  const search = params.get('search') ?? ''
  const status = params.get('status') ?? ''

  const query = useIngredients(search, status)
  const stroki = useMemo(() => query.data ?? [], [query.data])
  const statusy = useMemo(
    () => [...new Set(stroki.map((r) => r.status))].sort(),
    [stroki],
  )

  const kolonki: Column<Ingredient>[] = [
    { key: 'id', title: 'id', priority: 'wide', render: (r) => r.legacy_id },
    {
      key: 'name',
      title: 'Наименование',
      priority: 'always',
      render: (r) => (
        <span className={r.status.startsWith('архив') ? 'arhivnyy' : undefined}>{r.name}</span>
      ),
    },
    { key: 'category', title: 'Категория', priority: 'wide', render: (r) => r.category },
    {
      key: 'price',
      title: 'Цена за единицу',
      align: 'right',
      priority: 'always',
      // Имя поля price_per_kg врёт: при unit «шт» это цена за штуку.
      // Подпись — «Цена за единицу», единица берётся из unit.
      render: (r) => <Num value={r.price_per_kg} unit={`₽/${r.unit}`} />,
    },
    {
      key: 'ves',
      title: 'Вес 1 шт',
      align: 'right',
      priority: 'wide',
      render: (r) => <Num value={r.weight_per_piece_g} unit="г" />,
    },
    { key: 'status', title: 'Статус', priority: 'wide', render: (r) => r.status },
    {
      key: 'card',
      title: 'Карточка',
      priority: 'always',
      render: (r) =>
        r.has_card ? (
          <span aria-label="карточка есть" title="карточка есть">
            ✓
          </span>
        ) : (
          // Отсутствие карточки — сигнал: позиция справочника есть, а
          // карточки от повара нет.
          <span aria-label="карточки нет" title="карточки нет" className="net-kartochki">
            ○
          </span>
        ),
    },
  ]

  function zadat(klyuch: string, znachenie: string) {
    const novye = new URLSearchParams(params)
    if (znachenie) novye.set(klyuch, znachenie)
    else novye.delete(klyuch)
    setParams(novye, { replace: true })
  }

  return (
    <section>
      <h1>Справочник ингредиентов</h1>
      <Filtry
        search={search}
        onSearch={(v) => zadat('search', v)}
        status={status}
        onStatus={(v) => zadat('status', v)}
        statusy={statusy}
        vsego={stroki.length}
      />
      <Sostoyanie query={query} />
      {query.isSuccess && (
        <DataTable
          columns={kolonki}
          rows={stroki}
          rowKey={(r) => String(r.id)}
          empty="Ничего не найдено"
        />
      )}
    </section>
  )
}
```

`frontend/src/pages/pages.css`:

```css
h1 {
  font-size: 18px;
  margin: 0 0 calc(var(--shag) * 3);
}

.filtry {
  display: flex;
  flex-wrap: wrap;
  gap: var(--shag);
  margin-bottom: calc(var(--shag) * 3);
}

.filtry input,
.filtry select {
  padding: var(--shag) calc(var(--shag) * 2);
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  font: inherit;
}

.filtry input {
  flex: 1 1 200px;
  min-width: 0;
}

.filtry-schyot {
  align-self: center;
  color: var(--priglushyonnyy);
}

/* Архивные не прячем, но и не выдаём за живые. */
.arhivnyy {
  color: var(--priglushyonnyy);
  text-decoration: line-through;
  text-decoration-color: var(--granitsa);
}

.net-kartochki {
  color: var(--vnimanie);
}

.zagruzka,
.sboy {
  color: var(--priglushyonnyy);
  padding: calc(var(--shag) * 3) 0;
}

.sboy-prichina {
  font-size: 12px;
}
```

- [ ] **Шаг 5: Убедиться, что тесты проходят**

Запуск: `cd frontend && npm run test -- spravochnik`
Ожидается: PASS, шесть тестов.

- [ ] **Шаг 6: Коммит**

```bash
git add frontend/src/pages/ frontend/src/ui/Filtry.tsx frontend/src/ui/Sostoyanie.tsx \
        frontend/tests/spravochnik.test.tsx
git commit -m "Справочник: цена с настоящей единицей, архивные видны, карточка отмечена"
```

---

## Задача 11: экран блюд

**Файлы:**
- Правка: `frontend/src/pages/Dishes.tsx`
- Тест: `frontend/tests/blyuda.test.tsx`

**Интерфейсы:**
- Берёт из задач 5–7, 10: `Num`, `DataTable`, `Filtry`, `Sostoyanie`,
  `useDishes`.

- [ ] **Шаг 1: Написать падающие тесты**

`frontend/tests/blyuda.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { Dishes } from '../src/pages/Dishes'
import { setViewport } from './setup'

// Ответы настоящие: B001 с ценой, B003 из тех четырнадцати, у кого её нет,
// и выдуманная строка, где себестоимость выше цены.
const BLYUDA = [
  {
    legacy_id: 'B001',
    name: 'Круасан с мортаделой',
    category: 'Блюдо',
    status: 'активное',
    price_menu: '369.00',
    uc_rub: '84.66',
    uc_percent: '22.9',
    margin_percent: '77.1',
    output_grams: '72.000',
    warnings: 1,
  },
  {
    legacy_id: 'B003',
    name: 'Кетчуп',
    category: 'Соус-топпинг',
    status: 'активное',
    price_menu: null,
    uc_rub: '5.52',
    uc_percent: null,
    margin_percent: null,
    output_grams: '25.000',
    warnings: 1,
  },
  {
    legacy_id: 'B099',
    name: 'Салат овощной',
    category: 'Блюдо',
    status: 'активное',
    price_menu: '280.00',
    uc_rub: '1398.00',
    uc_percent: '499.3',
    margin_percent: '-399.3',
    output_grams: '210.000',
    warnings: 2,
  },
]

const server = setupServer(http.get('/api/dishes', () => HttpResponse.json(BLYUDA)))

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <Dishes />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('проценты показываются числом с запятой', async () => {
  narisovat()
  await screen.findByText('Круасан с мортаделой')
  expect(screen.getByText('22,9 %')).toBeInTheDocument()
})

test('у блюда без цены меню маржа — прочерк, а не ноль', async () => {
  narisovat()
  await screen.findByText('Кетчуп')
  const stroka = screen.getByText('Кетчуп').closest('tr')!
  expect(stroka.textContent).not.toMatch(/0\s*%/)
  expect(stroka.querySelectorAll('.num--pusto').length).toBeGreaterThanOrEqual(3)
})

test('себестоимость выше цены помечена красным', async () => {
  narisovat()
  await screen.findByText('Салат овощной')
  expect(screen.getByText('Салат овощной').closest('tr')).toHaveClass('stroka--ubytok')
})

test('подпись направляет к причине, а не пугает', async () => {
  // Это почти всегда перепутанная единица измерения, а не убыток.
  narisovat()
  await screen.findByText('Салат овощной')
  const podskazka = screen.getByTitle(/проверьте единицы измерения/i)
  expect(podskazka).toBeInTheDocument()
  expect(screen.queryByText(/убыток/i)).not.toBeInTheDocument()
})

test('ноль замечаний не тревожит, а больше нуля — заметен', async () => {
  narisovat()
  await screen.findByText('Круасан с мортаделой')
  expect(screen.getByText('Круасан с мортаделой').closest('tr')!.textContent).toContain('1')
})

test('на 360 px остаются название, себестоимость и маржа', async () => {
  setViewport(360)
  narisovat()
  await screen.findByText('Круасан с мортаделой')

  expect(screen.getByText('84,66 ₽')).toBeInTheDocument()
  expect(screen.getByText('77,1 %')).toBeInTheDocument()
  expect(screen.queryByText('369,00 ₽')).not.toBeInTheDocument()
})
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd frontend && npm run test -- blyuda`
Ожидается: FAIL — экран пока заглушка.

- [ ] **Шаг 3: Написать `Dishes.tsx`**

```tsx
import { useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { useDishes } from '../api/queries'
import type { Dish } from '../api/types'
import { DataTable, type Column } from '../ui/DataTable'
import { Filtry } from '../ui/Filtry'
import { Num } from '../ui/Num'
import { Sostoyanie } from '../ui/Sostoyanie'
import './pages.css'

/**
 * Себестоимость выше цены меню.
 *
 * Сравниваем строки как десятичные числа, не переводя их в float: цена и
 * себестоимость приходят строками именно затем, чтобы не терять копейки.
 */
function dorozhe(uc: string, tsena: string | null): boolean {
  if (tsena === null) return false
  return sravnit(uc, tsena) > 0
}

function sravnit(a: string, b: string): number {
  const [ac = '0', ad = ''] = a.split('.')
  const [bc = '0', bd = ''] = b.split('.')
  if (ac.length !== bc.length) return ac.length - bc.length
  if (ac !== bc) return ac < bc ? -1 : 1
  const dlina = Math.max(ad.length, bd.length)
  const ap = ad.padEnd(dlina, '0')
  const bp = bd.padEnd(dlina, '0')
  if (ap === bp) return 0
  return ap < bp ? -1 : 1
}

export function Dishes() {
  const [params, setParams] = useSearchParams()
  const search = params.get('search') ?? ''
  const status = params.get('status') ?? ''
  const idti = useNavigate()

  const query = useDishes(search, status)
  const stroki = useMemo(() => query.data ?? [], [query.data])
  const statusy = useMemo(() => [...new Set(stroki.map((r) => r.status))].sort(), [stroki])

  const kolonki: Column<Dish>[] = [
    { key: 'id', title: 'id', priority: 'wide', render: (r) => r.legacy_id },
    { key: 'name', title: 'Название', priority: 'always', render: (r) => r.name },
    { key: 'category', title: 'Категория', priority: 'wide', render: (r) => r.category },
    {
      key: 'price',
      title: 'Цена меню',
      align: 'right',
      priority: 'wide',
      render: (r) => <Num value={r.price_menu} fraction={2} unit="₽" />,
    },
    {
      key: 'uc',
      title: 'UC ₽',
      align: 'right',
      priority: 'always',
      render: (r) =>
        dorozhe(r.uc_rub, r.price_menu) ? (
          // Подпись направляет к причине: перепутанная единица измерения
          // встречается несравнимо чаще настоящего убытка.
          <span title="Себестоимость выше цены меню — проверьте единицы измерения">
            <Num value={r.uc_rub} fraction={2} unit="₽" />
          </span>
        ) : (
          <Num value={r.uc_rub} fraction={2} unit="₽" />
        ),
    },
    {
      key: 'ucp',
      title: 'UC %',
      align: 'right',
      priority: 'wide',
      render: (r) => <Num value={r.uc_percent} fraction={1} unit="%" />,
    },
    {
      key: 'margin',
      title: 'Маржа %',
      align: 'right',
      priority: 'always',
      render: (r) => <Num value={r.margin_percent} fraction={1} unit="%" />,
    },
    {
      key: 'output',
      title: 'Выход',
      align: 'right',
      priority: 'wide',
      render: (r) => <Num value={r.output_grams} unit="г" />,
    },
    {
      key: 'warnings',
      title: 'Замечания',
      align: 'right',
      priority: 'wide',
      // Ноль — не «всё хорошо», а «нам не на что указать». Тревогой не
      // красим: замечание не значит поломку.
      render: (r) => (
        <span className={r.warnings > 0 ? 'zamechaniya' : undefined}>{r.warnings}</span>
      ),
    },
  ]

  function zadat(klyuch: string, znachenie: string) {
    const novye = new URLSearchParams(params)
    if (znachenie) novye.set(klyuch, znachenie)
    else novye.delete(klyuch)
    setParams(novye, { replace: true })
  }

  return (
    <section>
      <h1>Блюда</h1>
      <Filtry
        search={search}
        onSearch={(v) => zadat('search', v)}
        status={status}
        onStatus={(v) => zadat('status', v)}
        statusy={statusy}
        vsego={stroki.length}
      />
      <Sostoyanie query={query} />
      {query.isSuccess && (
        <DataTable
          columns={kolonki}
          rows={stroki}
          rowKey={(r) => r.legacy_id}
          rowClass={(r) => (dorozhe(r.uc_rub, r.price_menu) ? 'stroka--ubytok' : undefined)}
          onOpen={(r) => idti(`/dishes/${r.legacy_id}`)}
          empty="Ничего не найдено"
        />
      )}
    </section>
  )
}
```

Дописать в `pages.css`:

```css
.zamechaniya {
  color: var(--vnimanie);
  font-weight: 600;
}
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `cd frontend && npm run test -- blyuda`
Ожидается: PASS, шесть тестов.

- [ ] **Шаг 5: Коммит**

```bash
git add frontend/src/pages/Dishes.tsx frontend/src/pages/pages.css frontend/tests/blyuda.test.tsx
git commit -m "Блюда: прочерк вместо нуля, красная строка ведёт к единицам измерения"
```

---

## Задача 12: карточка блюда

**Файлы:**
- Правка: `frontend/src/pages/DishDetail.tsx`
- Тест: `frontend/tests/kartochka.test.tsx`

- [ ] **Шаг 1: Написать падающие тесты**

`frontend/tests/kartochka.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { DishDetailPage } from '../src/pages/DishDetail'

// Ответ настоящий, из раздела 4 ТЗ.
const B001 = {
  legacy_id: 'B001',
  name: 'Круасан с мортаделой',
  category: 'Блюдо',
  status: 'активное',
  price_menu: '369.00',
  uc_rub: '84.66',
  uc_percent: '22.9',
  margin_percent: '77.1',
  output_grams: '72.000',
  warnings: 1,
  protein_g: '4.1',
  fat_g: '14.1',
  carbs_g: '3.6',
  kcal: '159',
  kbju_coverage: '0.778',
  components: [
    {
      name: 'Салат айсберг',
      short_name: 'Салат айсберг пф',
      row_type: 'main',
      unit: 'кг',
      net_weight_g: '16.000',
      gross_weight_g: '22.19',
      price_per_unit: '213.38',
      cost_rub: '4.74',
      share_percent: '5.6',
    },
    {
      name: 'Контейнер бумажный без крышки 207х127х55 крафт/черный',
      short_name: '',
      row_type: 'packaging',
      unit: 'шт',
      net_weight_g: '1.000',
      gross_weight_g: null,
      price_per_unit: '7.99',
      cost_rub: '7.99',
      share_percent: '9.4',
    },
  ],
  warning_texts: ['КБЖУ нет у 1 ингр. (Салат айсберг) — нутриенты приблизительны'],
}

const server = setupServer(http.get('/api/dishes/B001', () => HttpResponse.json(B001)))

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={['/dishes/B001']}>
        <Routes>
          <Route path="/dishes/:legacyId" element={<DishDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('нетто и брутто — две разные колонки', async () => {
  // 16 г айсберга в блюде требуют 22,19 г со склада из-за потерь при
  // нарезке. Схлопывать их в одну колонку нельзя.
  narisovat()
  await screen.findByText('Салат айсберг')

  expect(screen.getByText('16 г')).toBeInTheDocument()
  expect(screen.getByText('22,19 г')).toBeInTheDocument()
})

test('замечания показаны текстом целиком, а не числом', async () => {
  // Именно они объясняют, почему число такое.
  narisovat()
  expect(
    await screen.findByText(/КБЖУ нет у 1 ингр\. \(Салат айсберг\)/),
  ).toBeInTheDocument()
})

test('покрытие КБЖУ показано долей веса', async () => {
  // У B001 это 0.778: у айсберга КБЖУ не заполнено, а весит он почти
  // четверть блюда. Число выше порога 0.5, поэтому предупреждения нет.
  narisovat()
  await screen.findByText('Салат айсберг')
  expect(screen.getByText(/77,8\s*%/)).toBeInTheDocument()
  expect(screen.queryByText(/доверять нельзя/)).not.toBeInTheDocument()
})

test('покрытие ниже половины помечается прямо', async () => {
  // Ниже 0.5 цифрам КБЖУ доверять нельзя, и это надо сказать словами, а
  // не оставить читателю самому делить в уме.
  server.use(
    http.get('/api/dishes/B001', () => HttpResponse.json({ ...B001, kbju_coverage: '0.312' })),
  )
  narisovat()

  expect(await screen.findByText(/доверять нельзя/)).toBeInTheDocument()
})

test('упаковка стоит отдельной группой и брутто у неё нет', async () => {
  narisovat()
  const upakovka = await screen.findByText(/Контейнер бумажный/)
  expect(screen.getByRole('heading', { name: /упаковка/i })).toBeInTheDocument()
  expect(upakovka.closest('tr')!.querySelectorAll('.num--pusto').length).toBeGreaterThan(0)
})

test('пустое короткое имя не оставляет пустоту', async () => {
  narisovat()
  const upakovka = await screen.findByText(/Контейнер бумажный/)
  expect(upakovka).toBeInTheDocument()
})
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd frontend && npm run test -- kartochka`
Ожидается: FAIL — экрана нет.

- [ ] **Шаг 3: Написать `DishDetail.tsx`**

```tsx
import { Link, useParams } from 'react-router-dom'

import { useDish } from '../api/queries'
import type { Component } from '../api/types'
import { Num } from '../ui/Num'
import { Sostoyanie } from '../ui/Sostoyanie'
import './pages.css'

const PORT_KBJU = 0.5

export function DishDetailPage() {
  const { legacyId = '' } = useParams()
  const query = useDish(legacyId)

  if (!query.isSuccess) return <Sostoyanie query={query} />
  const blyudo = query.data

  const osnova = blyudo.components.filter((k) => k.row_type === 'main')
  const upakovka = blyudo.components.filter((k) => k.row_type === 'packaging')
  const pokrytie = Number(blyudo.kbju_coverage)

  return (
    <section className="kartochka">
      <Link to="/dishes" className="nazad">
        ← Блюда
      </Link>

      <header className="kartochka-shapka">
        <h1>{blyudo.name}</h1>
        <p className="kartochka-podpis">
          {blyudo.legacy_id} · {blyudo.category} · {blyudo.status}
        </p>
      </header>

      <div className="krupno">
        <Pokazatel podpis="Цена меню">
          <Num value={blyudo.price_menu} fraction={2} unit="₽" />
        </Pokazatel>
        <Pokazatel podpis="Себестоимость">
          <Num value={blyudo.uc_rub} fraction={2} unit="₽" />
        </Pokazatel>
        <Pokazatel podpis="Маржа">
          <Num value={blyudo.margin_percent} fraction={1} unit="%" />
        </Pokazatel>
        <Pokazatel podpis="Выход">
          <Num value={blyudo.output_grams} unit="г" />
        </Pokazatel>
      </div>

      <div className="kbju">
        <span>
          Б <Num value={blyudo.protein_g} fraction={1} unit="г" />
        </span>
        <span>
          Ж <Num value={blyudo.fat_g} fraction={1} unit="г" />
        </span>
        <span>
          У <Num value={blyudo.carbs_g} fraction={1} unit="г" />
        </span>
        <span>
          <Num value={blyudo.kcal} unit="ккал" />
        </span>
        <span className={pokrytie < PORT_KBJU ? 'kbju-slabo' : 'kbju-dolya'}>
          КБЖУ заполнено у <Num value={String(pokrytie * 100)} fraction={1} unit="%" /> веса
          {pokrytie < PORT_KBJU && ' — цифрам доверять нельзя'}
        </span>
      </div>

      <h2>Состав</h2>
      <Sostav stroki={osnova} />

      {upakovka.length > 0 && (
        <>
          {/* Упаковка в выход блюда не входит, и брутто у неё нет. */}
          <h2>Упаковка</h2>
          <Sostav stroki={upakovka} />
        </>
      )}

      {blyudo.warning_texts.length > 0 && (
        <>
          <h2>Замечания</h2>
          {/* Список целиком, а не числом: замечания и есть объяснение
              того, почему число такое. */}
          <ul className="zamechaniya-spisok">
            {blyudo.warning_texts.map((tekst) => (
              <li key={tekst}>{tekst}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

function Pokazatel({ podpis, children }: { podpis: string; children: React.ReactNode }) {
  return (
    <div className="pokazatel">
      <span className="pokazatel-podpis">{podpis}</span>
      <span className="pokazatel-znachenie">{children}</span>
    </div>
  )
}

function Sostav({ stroki }: { stroki: Component[] }) {
  return (
    <div className="tablitsa-obolochka">
      <table className="tablitsa">
        <thead>
          <tr>
            <th>Ингредиент</th>
            <th className="vpravo">Нетто</th>
            <th className="vpravo">Брутто</th>
            <th className="vpravo">Цена за единицу</th>
            <th className="vpravo">Стоимость</th>
            <th className="vpravo">Доля</th>
          </tr>
        </thead>
        <tbody>
          {stroki.map((k) => (
            <tr key={`${k.name}-${k.net_weight_g}`}>
              <td>
                {/* «Короткое для айки»: технологи работают в iiko и по
                    обычному имени не всегда понимают, что брать. */}
                {k.short_name || k.name}
                {k.short_name && <span className="polnoe-imya">{k.name}</span>}
              </td>
              <td className="vpravo">
                <Num value={k.net_weight_g} unit="г" />
              </td>
              <td className="vpravo">
                <Num value={k.gross_weight_g} unit="г" />
              </td>
              <td className="vpravo">
                <Num value={k.price_per_unit} fraction={2} unit={`₽/${k.unit}`} />
              </td>
              <td className="vpravo">
                <Num value={k.cost_rub} fraction={2} unit="₽" />
              </td>
              <td className="vpravo">
                <Num value={k.share_percent} fraction={1} unit="%" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

Дописать в `pages.css`:

```css
.krupno {
  display: flex;
  flex-wrap: wrap;
  gap: calc(var(--shag) * 6);
  padding: calc(var(--shag) * 3) 0;
  border-block: 1px solid var(--granitsa);
}

.pokazatel {
  display: grid;
  gap: calc(var(--shag) / 2);
}

.pokazatel-podpis {
  color: var(--priglushyonnyy);
  font-size: 12px;
}

.pokazatel-znachenie {
  font-size: 22px;
}

.kbju {
  display: flex;
  flex-wrap: wrap;
  gap: calc(var(--shag) * 4);
  padding: calc(var(--shag) * 2) 0;
  color: var(--priglushyonnyy);
}

.kbju-slabo {
  color: var(--vnimanie);
}

.polnoe-imya {
  display: block;
  color: var(--priglushyonnyy);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.zamechaniya-spisok {
  margin: 0;
  padding-left: calc(var(--shag) * 5);
  color: var(--vnimanie);
}

.nazad {
  color: var(--aktsent);
  text-decoration: none;
}

h2 {
  font-size: 15px;
  margin: calc(var(--shag) * 5) 0 calc(var(--shag) * 2);
}
```

- [ ] **Шаг 4: Убедиться, что тесты проходят**

Запуск: `cd frontend && npm run test -- kartochka`
Ожидается: PASS, пять тестов.

- [ ] **Шаг 5: Коммит**

```bash
git add frontend/src/pages/DishDetail.tsx frontend/src/pages/pages.css frontend/tests/kartochka.test.tsx
git commit -m "Карточка блюда: нетто и брутто врозь, замечания текстом"
```

---

## Задача 13: экран сверки справочника

**Файлы:**
- Создать: `frontend/src/ui/NameDiff.tsx`
- Правка: `frontend/src/pages/Reconciliation.tsx`
- Тест: `frontend/tests/sverka.test.tsx`

**Интерфейсы:**
- Отдаёт наружу: `<NameDiff a={string} b={string} />` — посимвольная
  подсветка различий.

- [ ] **Шаг 1: Написать падающие тесты**

`frontend/tests/sverka.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, test } from 'vitest'

import { Reconciliation } from '../src/pages/Reconciliation'

// Ответ настоящий, из раздела 4 ТЗ. Данные грязные — «Оснвова» с опечаткой,
// лишний пробел, поставщик «Хз» — и показываются как есть.
const SVERKA = {
  total: 101,
  linked: 85,
  needs_human: 16,
  rows: [
    {
      card_id: 7,
      name: 'Булочка для датского хот дога',
      link_status: 'ambiguous',
      supplier: 'Хз',
      candidates: [
        { ingredient_id: 34, legacy_id: '34', name: 'Булочка для датского хот дога', score: null },
        { ingredient_id: 121, legacy_id: '121', name: 'Булочка для датского хот дога', score: null },
      ],
    },
    {
      card_id: 25,
      name: 'Корж для римской пиццы',
      link_status: 'candidate',
      supplier: 'Папа наполи',
      candidates: [],
    },
    {
      card_id: 24,
      name: 'Оснвова для пиццы круглая , неаполитанская.',
      link_status: 'orphan',
      supplier: 'Папа наполи',
      candidates: [],
    },
  ],
}

const INGREDIENTY = [
  {
    id: 34,
    legacy_id: '34',
    name: 'Булочка для датского хот дога',
    category: 'Выпечка',
    unit: 'шт',
    status: 'активный',
    price_per_kg: '18.50',
    weight_per_piece_g: '60.000',
    has_card: true,
  },
  {
    id: 121,
    legacy_id: '121',
    name: 'Булочка для датского хот дога',
    category: 'Выпечка',
    unit: 'кг',
    status: 'активный',
    price_per_kg: '0.00',
    weight_per_piece_g: null,
    has_card: true,
  },
]

const server = setupServer(
  http.get('/api/reconciliation', () => HttpResponse.json(SVERKA)),
  http.get('/api/ingredients', () => HttpResponse.json(INGREDIENTY)),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function narisovat() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <Reconciliation />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

test('сводка называет три числа', async () => {
  narisovat()
  expect(await screen.findByText('101')).toBeInTheDocument()
  expect(screen.getByText('85')).toBeInTheDocument()
  expect(screen.getByText('16')).toBeInTheDocument()
})

test('три группы стоят врозь: они требуют разных действий', async () => {
  narisovat()
  await screen.findByText('101')
  expect(screen.getByRole('heading', { name: /несколько совпадений/i })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: /похожее/i })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: /пары нет/i })).toBeInTheDocument()
})

test('предвыбранного варианта нет ни одного', async () => {
  // Один из кандидатов — «огурцы маринованные НЕ резаные» против
  // «Огурцы маринованные резанные», похожесть 95 %, смысл противоположный.
  // Подсвеченное как очевидное человек примет не глядя.
  narisovat()
  await screen.findByText('101')
  for (const perekl of screen.queryAllByRole('radio')) {
    expect(perekl).not.toBeChecked()
  }
})

test('кнопки «связать всё автоматически» не существует', async () => {
  narisovat()
  await screen.findByText('101')
  expect(screen.queryByRole('button', { name: /автоматич/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /связать всё/i })).not.toBeInTheDocument()
})

test('тёзки различаются ценой и единицей, а не именем', async () => {
  // Ручка отдаёт только id и имя. Цену подтягиваем из /api/ingredients:
  // выбирать шеф будет по ней.
  narisovat()
  const gruppa = await screen.findByTestId('kartochka-7')
  expect(within(gruppa).getByText('18,50 ₽/шт')).toBeInTheDocument()
  expect(within(gruppa).getByText('0,00 ₽/кг')).toBeInTheDocument()
})

test('грязные данные показываются как есть', async () => {
  // Подчистить за шефа значило бы скрыть, что запись требует внимания.
  narisovat()
  expect(
    await screen.findByText('Оснвова для пиццы круглая , неаполитанская.'),
  ).toBeInTheDocument()
  expect(screen.getByText('Хз')).toBeInTheDocument()
})

test('действия обозначены, но объявлены недоступными', async () => {
  narisovat()
  await screen.findByText('101')
  const knopka = screen.getAllByRole('button', { name: /связать/i })[0]!
  expect(knopka).toBeDisabled()
  expect(screen.getAllByText(/появится в следующей фазе/i).length).toBeGreaterThan(0)
})
```

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd frontend && npm run test -- sverka`
Ожидается: FAIL — экрана нет.

- [ ] **Шаг 3: Написать `NameDiff.tsx`**

```tsx
/**
 * Посимвольная подсветка различий двух имён.
 *
 * Различие и есть предмет решения: «огурцы маринованные не резаные» против
 * «Огурцы маринованные резанные» — похожесть 95 %, смысл противоположный.
 * Показать имена рядом без подсветки значит спрятать «не» в середине
 * строки.
 *
 * Сравнение идёт с краёв: общее начало, общий конец, различие посередине.
 * Полноценный diff здесь избыточен — имена короткие и отличаются одним
 * куском.
 */
export function NameDiff({ a, b }: { a: string; b: string }) {
  const nachalo = obshcheeNachalo(a, b)
  const konets = obshchiyKonets(a.slice(nachalo), b.slice(nachalo))

  const seredina = a.slice(nachalo, a.length - konets)

  return (
    <span>
      {a.slice(0, nachalo)}
      {seredina && <mark className="razlichie">{seredina}</mark>}
      {konets > 0 ? a.slice(a.length - konets) : ''}
    </span>
  )
}

function obshcheeNachalo(a: string, b: string): number {
  let i = 0
  while (i < a.length && i < b.length && a[i]!.toLowerCase() === b[i]!.toLowerCase()) i += 1
  return i
}

function obshchiyKonets(a: string, b: string): number {
  let i = 0
  while (
    i < a.length &&
    i < b.length &&
    a[a.length - 1 - i]!.toLowerCase() === b[b.length - 1 - i]!.toLowerCase()
  ) {
    i += 1
  }
  return i
}
```

- [ ] **Шаг 4: Написать `Reconciliation.tsx`**

```tsx
import { useMemo } from 'react'

import { useIngredients, useReconciliation } from '../api/queries'
import type { Ingredient, LinkStatus, ReconciliationRow } from '../api/types'
import { NameDiff } from '../ui/NameDiff'
import { Num } from '../ui/Num'
import { Sostoyanie } from '../ui/Sostoyanie'
import './pages.css'

const GRUPPY: { status: LinkStatus; zagolovok: string; chto: string; deystvie: string }[] = [
  {
    status: 'ambiguous',
    zagolovok: 'Несколько совпадений',
    chto:
      'В справочнике несколько активных позиций с этим именем. Надо выбрать, ' +
      'какая имелась в виду — различает их не имя, а цена и единица.',
    deystvie: 'Связать с выбранной',
  },
  {
    status: 'candidate',
    zagolovok: 'Есть похожее',
    chto: 'Точного совпадения нет. Надо подтвердить предложенное или отвергнуть.',
    deystvie: 'Связать',
  },
  {
    status: 'orphan',
    zagolovok: 'Пары нет',
    chto:
      'Ингредиент оформлен поваром, но калькулятор его не видит. Надо завести ' +
      'позицию в справочник или отложить.',
    deystvie: 'Завести в справочник',
  },
]

export function Reconciliation() {
  const query = useReconciliation()
  // Список справочника берётся один раз и держится под рукой: ручка сверки
  // отдаёт у кандидатов только id и имя, а выбирают по цене.
  const spravochnik = useIngredients()

  const poId = useMemo(() => {
    const karta = new Map<number, Ingredient>()
    for (const stroka of spravochnik.data ?? []) karta.set(stroka.id, stroka)
    return karta
  }, [spravochnik.data])

  if (!query.isSuccess) return <Sostoyanie query={query} />
  const svodka = query.data

  return (
    <section>
      <h1>Сверка справочника</h1>
      <p className="poyasnenie">
        Справочник ингредиентов и карточки, которые заполняют повара, лежат в разных
        таблицах и не связаны между собой. Что склеилось по точному совпадению имени —
        склеилось; остальное решает человек.
      </p>

      <div className="krupno">
        <Pokazatel podpis="Всего карточек" znachenie={svodka.total} />
        <Pokazatel podpis="Склеено" znachenie={svodka.linked} />
        <Pokazatel podpis="Требуют решения" znachenie={svodka.needs_human} vnimanie />
      </div>

      {GRUPPY.map((gruppa) => {
        const stroki = svodka.rows.filter((r) => r.link_status === gruppa.status)
        if (stroki.length === 0) return null
        return (
          <div key={gruppa.status}>
            <h2>
              {gruppa.zagolovok} — {stroki.length}
            </h2>
            <p className="poyasnenie">{gruppa.chto}</p>
            {stroki.map((stroka) => (
              <Kartochka
                key={stroka.card_id}
                stroka={stroka}
                deystvie={gruppa.deystvie}
                poId={poId}
              />
            ))}
          </div>
        )
      })}
    </section>
  )
}

function Pokazatel({
  podpis,
  znachenie,
  vnimanie,
}: {
  podpis: string
  znachenie: number
  vnimanie?: boolean
}) {
  return (
    <div className="pokazatel">
      <span className="pokazatel-podpis">{podpis}</span>
      <span className={vnimanie ? 'pokazatel-znachenie kbju-slabo' : 'pokazatel-znachenie'}>
        {znachenie}
      </span>
    </div>
  )
}

function Kartochka({
  stroka,
  deystvie,
  poId,
}: {
  stroka: ReconciliationRow
  deystvie: string
  poId: Map<number, Ingredient>
}) {
  return (
    <article className="sverka-kartochka" data-testid={`kartochka-${stroka.card_id}`}>
      <header>
        {/* Имена показываются как есть, с опечатками и лишними пробелами:
            подчистить за шефа значило бы скрыть, что запись требует
            внимания. */}
        <b>{stroka.name}</b>
        <span className="postavshchik">{stroka.supplier}</span>
      </header>

      {stroka.candidates.length > 0 ? (
        <ul className="varianty">
          {stroka.candidates.map((kandidat) => {
            const ingredient = poId.get(kandidat.ingredient_id)
            return (
              <li key={kandidat.ingredient_id}>
                <label>
                  {/* Предвыбранного варианта нет. Подсвеченное как очевидное
                      совпадение человек примет не глядя. */}
                  <input type="radio" name={`vybor-${stroka.card_id}`} disabled />
                  <NameDiff a={kandidat.name} b={stroka.name} />
                  <span className="variant-otlichie">
                    <span className="variant-id">{kandidat.legacy_id}</span>
                    {ingredient && (
                      <Num
                        value={ingredient.price_per_kg}
                        fraction={2}
                        unit={`₽/${ingredient.unit}`}
                      />
                    )}
                  </span>
                </label>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="net-kandidatov">
          Подбор похожих появится вместе с ручкой подбора: сейчас бэкенд кандидатов не
          отдаёт.
        </p>
      )}

      <div className="deystviya">
        {/* Ручек записи ещё нет, и выдумывать их нельзя. */}
        <button type="button" disabled>
          {deystvie}
        </button>
        <button type="button" disabled>
          Отложить
        </button>
        <span className="deystviya-poyasnenie">Действие появится в следующей фазе</span>
      </div>
    </article>
  )
}
```

Дописать в `pages.css`:

```css
.poyasnenie {
  color: var(--priglushyonnyy);
  max-width: 62ch;
}

.sverka-kartochka {
  background: var(--poverhnost);
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  padding: calc(var(--shag) * 3);
  margin-bottom: calc(var(--shag) * 2);
}

.sverka-kartochka header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: var(--shag);
}

.postavshchik {
  color: var(--priglushyonnyy);
}

.varianty {
  list-style: none;
  margin: calc(var(--shag) * 2) 0 0;
  padding: 0;
}

.varianty label {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--shag);
  padding: var(--shag) 0;
}

.variant-otlichie {
  margin-left: auto;
  display: flex;
  gap: calc(var(--shag) * 3);
}

.variant-id {
  color: var(--priglushyonnyy);
}

.razlichie {
  background: #fdf3e3;
  color: var(--vnimanie);
}

.net-kandidatov {
  color: var(--priglushyonnyy);
  font-size: 12px;
}

.deystviya {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: calc(var(--shag) * 2);
  margin-top: calc(var(--shag) * 2);
}

.deystviya button {
  padding: var(--shag) calc(var(--shag) * 2);
  border: 1px solid var(--granitsa);
  border-radius: var(--radius);
  background: none;
  font: inherit;
}

.deystviya-poyasnenie {
  color: var(--priglushyonnyy);
  font-size: 12px;
}
```

- [ ] **Шаг 5: Убедиться, что тесты проходят**

Запуск: `cd frontend && npm run test && npm run types && npm run lint`
Ожидается: PASS — все наборы, включая семь тестов сверки.

- [ ] **Шаг 6: Коммит**

```bash
git add frontend/src/pages/Reconciliation.tsx frontend/src/ui/NameDiff.tsx \
        frontend/src/pages/pages.css frontend/tests/sverka.test.tsx
git commit -m "Сверка: три группы, различия подсвечены, автоподстановки нет"
```

---

## Задача 14: сборка образа и раздача статики

**Файлы:**
- Создать: `infra/nginx/Dockerfile`, `.dockerignore`
- Правка: `infra/nginx/kitchen-platform.conf`
- Правка: `infra/docker-compose.yml`
- Тест: `backend/tests/unit/test_infra.py` (дописать)

**Интерфейсы:**
- Образ `kitchen-platform-nginx:${APP_VERSION}` с `dist` внутри.

- [ ] **Шаг 1: Написать падающие тесты периметра**

`backend/tests/unit/test_infra.py` уже разбирает compose-файлы. Дописать:

```python
def test_nginx_sobiraetsya_a_ne_tyanetsya() -> None:
    """Сборка фронтенда привязана к коммиту.

    Готовый образ nginx с примонтированной статикой означал бы, что
    выложенный фронтенд и выложенный бэкенд могут разъехаться по версиям:
    образ тот же, а содержимое тома — какое осталось с прошлого раза.
    """
    nginx = _load(COMPOSE)["services"]["nginx"]

    assert "build" in nginx, "статика собирается вместе с образом"
    assert nginx["build"]["dockerfile"] == "infra/nginx/Dockerfile"
    assert nginx["build"]["context"] == "..", "в контекст должны попасть и frontend, и infra"
    assert nginx["image"].startswith("kitchen-platform-nginx")
```

`_load` и `COMPOSE` в этом файле уже объявлены — пользуйтесь ими.

Второй тест не пишем: `test_only_nginx_is_published_outside` в этом же
файле уже проверяет, что наружу смотрит только nginx, и после нашей правки
он обязан продолжать проходить. Это и есть проверка, что появление статики
не расшатало периметр.

- [ ] **Шаг 2: Убедиться, что тесты падают**

Запуск: `cd backend && uv run pytest tests/unit/test_infra.py -v`
Ожидается: FAIL — у `nginx` стоит `image: nginx:1.27-alpine`, ключа
`build` нет.

- [ ] **Шаг 3: Написать `infra/nginx/Dockerfile`**

```dockerfile
# Сборка фронтенда и его раздача.
#
# Node живёт только здесь: на сервер он не ставится. Сборка привязана к
# коммиту, поэтому «какая версия интерфейса выложена» перестаёт быть
# вопросом — она та же, что у бэкенда.

FROM node:22-alpine AS sborka
WORKDIR /app

# Сначала манифесты, потом код: слой с зависимостями переживает правку
# исходников и не пересобирается на каждый коммит.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
# Конфиг не копируем: он подмонтирован томом из репозитория, и правка
# nginx не должна требовать пересборки образа.
COPY --from=sborka /app/dist /usr/share/nginx/html
```

- [ ] **Шаг 4: Написать `.dockerignore` в корне репозитория**

```
# Контекст сборки — весь репозиторий, а в нём есть каталоги, которых в
# образе быть не должно и которые сильно замедляют отправку контекста.
.git
**/node_modules
**/dist
backend/.venv
backend/.ruff_cache
backend/.pytest_cache
**/__pycache__
_archive_botov
docs
*.md
.env
.env.*
!.env.example
```

- [ ] **Шаг 5: Поправить `kitchen-platform.conf`**

В блоке `server` на 443 заменить `location /`:

```nginx
    # Статика фронтенда лежит в образе.
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri /index.html;
    }

    # Хешированные имена файлов меняются при каждой сборке, поэтому их
    # можно кэшировать надолго.
    #
    # Используется expires, а не add_header: add_header внутри location
    # ОТМЕНЯЕТ все заголовки, унаследованные от server, — вместе с HSTS и
    # CSP. Эта ловушка тихая: заголовки просто перестают приходить.
    location /assets/ {
        expires 1y;
        access_log off;
    }

    # А вот index.html кэшировать нельзя: иначе после выкладки браузер
    # неделю показывает старую версию, и шеф не понимает, почему ничего
    # не изменилось.
    location = /index.html {
        expires -1;
    }

    location /api/ {
        proxy_pass http://api:8080;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_buffering off;
        proxy_read_timeout 300s;
    }
```

Блоки `location /api/auth/`, `location /api/chat/` и `location /healthz`
оставить как есть — они точнее общего `/api/` и продолжат выигрывать по
правилам nginx.

- [ ] **Шаг 6: Поправить `infra/docker-compose.yml`**

Заменить у сервиса `nginx`:

```yaml
  nginx:
    # Контекст — корень репозитория: в образ едет и frontend/, и infra/.
    build:
      context: ..
      dockerfile: infra/nginx/Dockerfile
    image: kitchen-platform-nginx:${APP_VERSION:-dev}
    restart: unless-stopped
```

Остальное у сервиса (порты, тома, `depends_on`, сети, логи) не трогать.

- [ ] **Шаг 7: Убедиться, что тесты проходят**

Запуск: `cd backend && uv run pytest tests/unit/test_infra.py -v`
Ожидается: PASS.

- [ ] **Шаг 8: Проверить, что образ действительно собирается**

```bash
docker compose -f infra/docker-compose.yml build nginx
docker run --rm kitchen-platform-nginx:dev ls /usr/share/nginx/html
```

Ожидается: в выводе `index.html` и каталог `assets`. Проверяем не «команда
прошла», а «в выводе есть то, что я менял» — 09.09 `docker compose config`
отработал без ошибок, показывая конфигурацию без наших правок.

Если Docker на машине разработки недоступен, шаг переносится на сервер и
отмечается в отчёте как невыполненный. Заявлять его сделанным нельзя.

- [ ] **Шаг 9: Коммит**

```bash
git add infra/nginx/Dockerfile .dockerignore infra/nginx/kitchen-platform.conf \
        infra/docker-compose.yml backend/tests/unit/test_infra.py
git commit -m "Фронтенд собирается в образ и раздаётся nginx"
```

---

## Задача 15: CI и Makefile

**Файлы:**
- Правка: `.github/workflows/ci.yml`
- Правка: `Makefile`
- Правка: `.pre-commit-config.yaml`

- [ ] **Шаг 1: Добавить задачу в CI**

В `.github/workflows/ci.yml`, в конец списка `jobs`:

```yaml
  frontend:
    name: фронтенд
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
        working-directory: frontend
      - run: npm run types
        working-directory: frontend
      - run: npm run lint
        working-directory: frontend
      - run: npm run test
        working-directory: frontend
      # Сборка входит в блокирующий CI намеренно: тесты проходят и при
      # сломанной сборке, а выкладка на сервере — нет.
      - run: npm run build
        working-directory: frontend
```

- [ ] **Шаг 2: Добавить цели в Makefile**

```make
front-install:  ## Поставить зависимости фронтенда
	cd frontend && npm ci

front-types:  ## tsc: типы фронтенда
	cd frontend && npm run types

front-lint:  ## eslint
	cd frontend && npm run lint

front-test:  ## Тесты фронтенда (Vitest)
	cd frontend && npm run test

front-build:  ## Сборка фронтенда
	cd frontend && npm run build
```

Заменить цель `check`:

```make
check: lint types arch test front-types front-lint front-test  ## Всё, что блокирует PR. Цель — уложиться в 5 минут
```

И дописать новые цели в строку `.PHONY`.

- [ ] **Шаг 3: Проверить бюджет времени**

```bash
time (cd frontend && npm run types && npm run lint && npm run test && npm run build)
```

Ожидается: меньше двух минут на холодном `node_modules`. Если больше —
разбираться, а не поднимать бюджет: требование к блокирующему CI —
укладываться в пять минут, и оно защищает дисциплину, а не производительность.

- [ ] **Шаг 4: Коммит**

```bash
git add .github/workflows/ci.yml Makefile
git commit -m "Фронтенд в блокирующем CI: типы, линтер, тесты, сборка"
```

---

## Задача 16: привести документы в соответствие

**Файлы:**
- Правка: `docs/FRONTEND.md` — раздел 3
- Правка: `docs/ROADMAP.md`
- Правка: `CLAUDE.md` — журнал разборов
- Правка: `README.md`
- Правка: `CONTRIBUTING.md`

- [ ] **Шаг 1: Переписать раздел 3 ТЗ**

Заменить описание прямого обращения к Supabase на три наши ручки:
`POST /api/auth/login` (тело `{email, password}`, ответ — как у `/api/me`),
`POST /api/auth/refresh`, `POST /api/auth/logout`. Указать, что токен живёт
в httpOnly-куках, фронтенд его не видит, а `SUPABASE_ANON_KEY` переменной
сборки фронтенда не является.

Таблицу кодов 401/403 оставить: она верна и важна.

- [ ] **Шаг 2: Дописать разбор в журнал `CLAUDE.md`**

```markdown
- **10.09.2026** ТЗ на фронтенд предписывало браузеру ходить за токеном
  прямо в Supabase. Так нельзя: GoTrue слушает 127.0.0.1:8000, наружу его
  никто не проксирует, а наш собственный CSP `default-src 'self'` запретил
  бы этот запрос и при доступном адресе. Признак, что решение было принято
  раньше и потерялось: в nginx уже лежал `location /api/auth/` с
  ограничением частоты, а в `.env.example` — `SESSION_COOKIE_SECURE`.
  Отсюда правило: перед тем как выполнять инструкцию из документа,
  проверять, исполнима ли она в развёрнутом виде.
```

- [ ] **Шаг 3: Обновить роадмап**

В фазе 2 перевести в сделанные пункты «Фронтенд» и «Экран ручной сверки»
со строками доказательства — числом пройденных тестов и выводом сборки.
Пункты «Домен и TLS» и «Расчётка и конкуренты» оставить несделанными.

Обновить дату и раздел «Где мы сейчас».

**Пункт переезжает в «сделано» только вместе со строкой доказательства.**
Если что-то из задачи 14 проверить не удалось — так и написать.

- [ ] **Шаг 4: Дописать README и CONTRIBUTING**

В `README.md` — как поднять фронтенд в разработке:

```bash
cd backend && uv run uvicorn kitchen.web.app:app --port 8080
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Отдельно оговорить: в разработке `SESSION_COOKIE_SECURE=false`, иначе
браузер не примет куку по http.

В `CONTRIBUTING.md` — что перед PR гоняется `make check`, и что на Windows
цели фронтенда выполняются как есть.

- [ ] **Шаг 5: Полная проверка перед PR**

```bash
cd backend && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy src && uv run lint-imports && uv run pytest
cd ../frontend && npm run types && npm run lint && npm run test && npm run build
```

Ожидается: всё зелёное. Числа пройденных тестов записать — они пойдут в
роадмап строкой доказательства.

- [ ] **Шаг 6: Коммит и PR**

```bash
git add docs/ CLAUDE.md README.md CONTRIBUTING.md
git commit -m "Документы под фактическое устройство входа"
git push -u origin frontend-faza-2
gh pr create --title "Фронтенд фазы 2" --body-file - <<'EOF'
Пять экранов на живом API: вход, справочник, блюда, карточка блюда,
сверка справочника. Плюс оболочка с меню на восемь разделов — шесть из
них заглушки с описанием того, что там будет.

Вход пришлось переделать. Раздел 3 ТЗ предписывал браузеру ходить за
токеном прямо в Supabase, а так нельзя: GoTrue слушает 127.0.0.1:8000,
наружу его никто не проксирует, и CSP `default-src 'self'` запретил бы
этот запрос даже при доступном адресе. Теперь в GoTrue ходит бэкенд, а
браузер получает httpOnly-куки и токена не видит вовсе.

Ручек записи нет: правило «пока живы боты, не пишем» в силе. Кнопки на
сверке присутствуют и не работают — намеренно.

Проверено: <числа пройденных тестов подставить из шага 5>.
Не проверено: `art.karppacho.ru` не резолвится, A-записи нет.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

Числа в теле PR подставить настоящие — те, что напечатал шаг 5. Строку
`<числа...>` в неизменном виде оставлять нельзя.

---

## Что проверяется приёмкой

| Заявление | Чем доказывается |
|---|---|
| «тесты проходят» | вывод `npm run test` и `uv run pytest` с числами |
| «типы в порядке» | вывод `npm run types` и `uv run mypy src` |
| «слои не нарушены» | вывод `uv run lint-imports` |
| «собирается» | `npm run build` и `docker compose build nginx` |
| «статика в образе» | `docker run --rm kitchen-platform-nginx:dev ls /usr/share/nginx/html` |
| «работает с телефона» | тесты на 360 px в наборах `tablitsa`, `spravochnik`, `blyuda` |

«Прошлый прогон», «должно пройти» и «я не менял это место» доказательствами
не являются.

## Чего этот план не закрывает

- `art.karppacho.ru` не резолвится: A-записи на 153.80.247.91 нет. До её
  появления «работает у шефа» заявить нельзя, и это не дефект работы.
- Ручки записи, подбор кандидатов, расчётка, конкуренты, дегустации, чат —
  фазы 3 и 4.
- Импорт конкурентов в базу: их листы читаются, но не переносятся.
