"""Завести человеку доступ в платформу.

Двухшаговое по устройству: учётку создаёт Supabase Auth, профиль и роли —
мы. Разделение не наше изобретение, а следствие того, что вход ведёт
GoTrue, а предметная область — наша.

Запуск на сервере::

    docker compose -f infra/docker-compose.yml exec api \\
        python scripts/grant_access.py chef@example.ru --roles chef --name "Бренд-шеф"

Пароль генерируется и печатается ОДИН раз. Хранить его негде и не нужно:
человек меняет его сам, а забытый сбрасывается повторным запуском с
`--reset-password`.

Скрипт идемпотентен: повторный запуск для того же адреса не создаёт вторую
учётку, а обновляет роли.
"""

from __future__ import annotations

import argparse
import secrets
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import select

from kitchen.config import load_settings
from kitchen.db import models
from kitchen.db.session import make_session_factory

RULE = "─" * 78


def admin_headers(service_key: str) -> dict[str, str]:
    """Заголовки администратора GoTrue.

    `service_role` обходит все проверки прав. Он живёт только здесь, на
    бэкенде, и никогда не уезжает во фронтенд.
    """
    return {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }


def find_user(base_url: str, headers: dict[str, str], email: str) -> dict[str, object] | None:
    response = httpx.get(
        f"{base_url}/auth/v1/admin/users",
        headers=headers,
        params={"page": 1, "per_page": 200},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    users = payload.get("users", []) if isinstance(payload, dict) else []
    for user in users:
        if str(user.get("email", "")).lower() == email.lower():
            return dict(user)
    return None


def create_user(
    base_url: str, headers: dict[str, str], email: str, password: str
) -> dict[str, object]:
    """Создать учётку.

    `email_confirm=True` — подтверждение почты выключено осознанно: у
    self-hosted GoTrue нет почтовика, а письма с российского VPS без
    SPF/DKIM уходят в спам. Вешать вход на такую доставку нельзя.
    """
    response = httpx.post(
        f"{base_url}/auth/v1/admin/users",
        headers=headers,
        json={"email": email, "password": password, "email_confirm": True},
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"GoTrue отказал ({response.status_code}): {response.text[:300]}")
    return dict(response.json())


def set_password(base_url: str, headers: dict[str, str], user_id: str, password: str) -> None:
    response = httpx.put(
        f"{base_url}/auth/v1/admin/users/{user_id}",
        headers=headers,
        json={"password": password},
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"GoTrue отказал ({response.status_code}): {response.text[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Завести доступ в платформу")
    parser.add_argument("email")
    parser.add_argument("--name", default="", help="как показывать человека")
    parser.add_argument(
        "--roles",
        nargs="+",
        default=["chef"],
        help="chef, cook, commerce, developer",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="выдать новый пароль существующей учётке",
    )
    args = parser.parse_args()

    settings = load_settings()
    service_key = settings.supabase_service_role_key.get_secret_value()
    if not service_key:
        print("Не задан SUPABASE_SERVICE_ROLE_KEY — без него учётку не создать.")
        return 1

    headers = admin_headers(service_key)
    base_url = settings.supabase_url.rstrip("/")

    print(RULE)
    print(f"ДОСТУП ДЛЯ {args.email}")
    print(RULE)

    existing = find_user(base_url, headers, args.email)
    password: str | None = None

    if existing is None:
        password = secrets.token_urlsafe(18)
        user = create_user(base_url, headers, args.email, password)
        print("  учётка создана")
    else:
        user = existing
        print("  учётка уже есть")
        if args.reset_password:
            password = secrets.token_urlsafe(18)
            set_password(base_url, headers, str(user["id"]), password)
            print("  пароль заменён")

    user_id = uuid.UUID(str(user["id"]))

    sessions = make_session_factory(settings.database_url)
    with sessions() as session, session.begin():
        known = {row.code for row in session.scalars(select(models.Role)).all()}
        unknown = sorted(set(args.roles) - known)
        if unknown:
            print(f"  НЕИЗВЕСТНЫЕ РОЛИ: {', '.join(unknown)}")
            print(f"  известные: {', '.join(sorted(known))}")
            return 1

        profile = session.get(models.Profile, user_id)
        if profile is None:
            profile = models.Profile(id=user_id, email=args.email)
            session.add(profile)
            print("  профиль создан")
        profile.display_name = args.name or profile.display_name or args.email
        profile.is_active = True

        session.flush()
        # Роли переустанавливаются целиком: «добавить» и «оставить как есть»
        # различить по аргументам невозможно, а тихо накапливать права —
        # худший из вариантов.
        for link in list(profile.roles):
            session.delete(link)
        session.flush()
        for code in sorted(set(args.roles)):
            session.add(models.UserRole(profile_id=user_id, role_code=code))
        print(f"  роли: {', '.join(sorted(set(args.roles)))}")

    print()
    if password:
        print("  ПАРОЛЬ (показывается один раз):")
        print(f"    {password}")
        print()
        print("  Передайте его человеку и попросите сменить при первом входе.")
    else:
        print("  Пароль не менялся. Нужен новый — запустите с --reset-password.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
