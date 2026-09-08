"""Проверка живости.

От неё зависит деплой: смоук сверяет отданную версию с той, что только что
выложил, и откатывается при несовпадении. Если этот контракт сломается,
деплой перестанет замечать неперезапустившийся процесс — ровно тот случай,
что произошёл 19.08.2026.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from kitchen.config import Settings
from kitchen.web.app import create_app


def _client(**overrides: object) -> TestClient:
    settings = Settings(app_version="deadbeef", app_env="test", **overrides)  # type: ignore[arg-type]
    return TestClient(create_app(settings))


def test_healthz_reports_deployed_commit() -> None:
    body = _client().get("/healthz").json()
    assert body["status"] == "ok"
    assert body["version"] == "deadbeef", "смоук деплоя сверяет именно это поле"


def test_healthz_needs_no_auth() -> None:
    """Иначе смоук не сможет её вызвать, а откат не сработает."""
    assert _client().get("/healthz").status_code == 200


def test_openapi_hidden_outside_development() -> None:
    """Схема подробно описывает внутреннее устройство и наружу не нужна."""
    assert _client().get("/openapi.json").status_code == 404

    dev = TestClient(create_app(Settings(app_env="development")))
    assert dev.get("/openapi.json").status_code == 200
