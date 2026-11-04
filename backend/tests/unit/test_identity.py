"""Роли и проверка токена в общем модуле ``kitchen.identity``.

Сайт и MCP берут пользователя отсюда. Тексты отказов те же, что видит
человек на сайте (см. ``test_web_auth``), поэтому здесь — сам модуль, без HTTP,
и то, что сайт действительно идёт через него.
"""

from __future__ import annotations

import uuid

import pytest

from kitchen import identity
from kitchen.config import Settings
from kitchen.web import auth
from tests.unit.test_web_auth import (
    PROFILE_ID,
    SECRET,
    FakeSession,
    make_client,
    make_profile,
    make_token,
)


def _settings(secret: str = SECRET) -> Settings:
    return Settings(app_env="test", supabase_jwt_secret=secret)  # type: ignore[call-arg]


def test_valid_token_gives_payload() -> None:
    payload = identity.decode_token(make_token(), _settings())

    assert payload["sub"] == str(PROFILE_ID)


@pytest.mark.parametrize(
    ("token", "secret", "detail"),
    [
        (make_token(minutes=-5), SECRET, "токен недействителен"),
        ("не-токен", SECRET, "токен недействителен"),
        (make_token(), "another-secret-of-sufficient-length-32", "токен недействителен"),
        (make_token(), "", "вход не настроен"),
    ],
)
def test_bad_token_is_token_error(token: str, secret: str, detail: str) -> None:
    with pytest.raises(identity.TokenError) as error:
        identity.decode_token(token, _settings(secret))

    assert error.value.detail == detail


def test_load_user_gives_roles() -> None:
    user = identity.load_user(
        FakeSession(make_profile(roles=("cook", "chef"))),  # type: ignore[arg-type]
        str(PROFILE_ID),
    )

    assert user.roles == {"cook", "chef"}
    assert user.has("chef")
    assert not user.has("commerce")


def test_load_user_rejects_bad_subject() -> None:
    with pytest.raises(identity.TokenError):
        identity.load_user(FakeSession(None), "не-uuid")  # type: ignore[arg-type]


def test_load_user_without_profile_is_access_denied() -> None:
    with pytest.raises(identity.AccessDeniedError) as error:
        identity.load_user(FakeSession(None), str(uuid.uuid4()))  # type: ignore[arg-type]

    assert error.value.detail == "профиль не заведён — обратитесь к администратору"


def test_load_user_inactive_is_access_denied() -> None:
    with pytest.raises(identity.AccessDeniedError) as error:
        identity.load_user(FakeSession(make_profile(active=False)), str(PROFILE_ID))  # type: ignore[arg-type]

    assert error.value.detail == "доступ отключён"


def test_require_roles_names_roles_sorted() -> None:
    cook = identity.CurrentUser(
        id=PROFILE_ID, email="c@example.com", display_name="Повар", roles=frozenset({"cook"})
    )

    assert identity.require_roles(cook, "cook", "chef") is cook
    with pytest.raises(identity.AccessDeniedError) as error:
        identity.require_roles(cook, "developer", "chef")

    assert error.value.detail == "нужна роль: chef, developer"


def test_site_uses_identity_for_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сайт не держит своей проверки: подмена в общем модуле меняет ответ ручки."""

    def refuse(_token: str, _settings: Settings) -> dict[str, object]:
        raise identity.TokenError("подменено в identity")

    monkeypatch.setattr(identity, "decode_token", refuse)

    reply = make_client().get("/api/me", headers={"Authorization": f"Bearer {make_token()}"})

    assert reply.status_code == 401
    assert reply.json()["detail"] == "подменено в identity"
    assert reply.headers["WWW-Authenticate"] == "Bearer"


def test_site_uses_identity_for_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Профиль и роли сайт тоже берёт из общего модуля."""
    seen: list[str] = []
    real = identity.load_user

    def spy(session: object, subject: str) -> identity.CurrentUser:
        seen.append(subject)
        return real(session, subject)  # type: ignore[arg-type]

    monkeypatch.setattr(identity, "load_user", spy)

    reply = make_client().get("/api/me", headers={"Authorization": f"Bearer {make_token()}"})

    assert reply.status_code == 200
    assert seen == [str(PROFILE_ID)]


def test_current_user_is_the_same_class() -> None:
    """``auth.CurrentUser`` — тот же класс: старые импорты и тесты живут."""
    assert auth.CurrentUser is identity.CurrentUser
