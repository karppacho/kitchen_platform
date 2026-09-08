"""Периметр: что публикуется наружу.

У трёх Telegram-ботов не было ни одного входящего порта. У веба он есть —
это новая поверхность атаки, которой у проекта никогда не было.

Самый вероятный способ пострадать здесь — не изощрённая атака, а забытое
двоеточие: `"5432:5432"` вместо `"127.0.0.1:5432:5432"`. У self-hosted
Supabase Postgres и Kong по умолчанию публикуются на все интерфейсы, и на
машине с белым адресом их начинают сканировать в течение часов.

Поэтому правило проверяется тестом, а не внимательностью при код-ревью.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "infra" / "docker-compose.yml"
SUPABASE_OVERRIDE = REPO / "infra" / "supabase" / "docker-compose.override.yml"

# Единственное, чему положено смотреть наружу. Расширение этого множества —
# осознанное решение, которое обязано сопровождаться правкой UFW и внятным
# ответом на вопрос «зачем».
PUBLIC_ALLOWED: dict[str, set[str]] = {"nginx": {"80:80", "443:443"}}


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _published(service: dict[str, Any]) -> list[str]:
    return [str(p) for p in (service.get("ports") or [])]


def _all_yaml_files() -> list[Path]:
    found: list[Path] = []
    for pattern in ("*.yml", "*.yaml"):
        found += [
            p
            for p in REPO.rglob(pattern)
            if ".venv" not in p.parts and "node_modules" not in p.parts
        ]
    return sorted(found)


@pytest.mark.parametrize("path", _all_yaml_files(), ids=lambda p: str(p.name))
def test_yaml_files_parse(path: Path) -> None:
    """Синтаксическая ошибка в конфиге обнаруживается здесь, а не на сервере."""
    yaml.safe_load(path.read_text(encoding="utf-8"))


def test_only_nginx_is_published_outside() -> None:
    compose = _load(COMPOSE)
    for name, service in compose["services"].items():
        for port in _published(service):
            if port.startswith("127.0.0.1:"):
                continue
            assert port in PUBLIC_ALLOWED.get(name, set()), (
                f"Сервис «{name}» публикует {port} на все интерфейсы. "
                f"Наружу смотрит только nginx на 80 и 443; всё остальное "
                f"привязывается к 127.0.0.1."
            )


def test_supabase_ports_are_local_only() -> None:
    """Postgres, пулер и Kong не должны быть видны из интернета."""
    override = _load(SUPABASE_OVERRIDE)["services"]
    for name in ("db", "supavisor", "kong"):
        ports = _published(override[name])
        assert ports, f"«{name}»: оверлей обязан переопределить порты, иначе останутся базовые"
        for port in ports:
            assert port.startswith("127.0.0.1:"), f"«{name}»: {port} смотрит наружу"


def test_studio_publishes_nothing() -> None:
    """За Studio — полный доступ к базе, а защита одна пара логин/пароль.

    Доступ только через SSH-туннель.
    """
    assert _load(SUPABASE_OVERRIDE)["services"]["studio"].get("ports") == []


def test_unused_supabase_services_are_disabled() -> None:
    """То, чем не пользуемся, не поднимаем: это память, которой в обрез."""
    override = _load(SUPABASE_OVERRIDE)["services"]
    for name in ("rest", "realtime", "storage", "imgproxy", "functions", "analytics", "vector"):
        assert override[name].get("profiles") == ["unused"], (
            f"«{name}» должен быть выключен профилем. PostgREST отдельно: "
            f"решение не использовать его принято в docs/adr/0002."
        )


def test_secrets_are_not_mounted_writable() -> None:
    """Ключ сервисного аккаунта монтируется только на чтение."""
    compose = _load(COMPOSE)
    for name, service in compose["services"].items():
        for volume in service.get("volumes") or []:
            if "service_account" in str(volume):
                assert str(volume).endswith(":ro"), f"«{name}»: ключ Google смонтирован на запись"
