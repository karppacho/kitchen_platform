"""Проверить настройку «Нового ингредиента»: ключ, таблицы, папку фото, загрузку.

Запускать на сервере после настройки папки фото и после любой правки
доступов — к таблицам, к папке, к ключу сервисного аккаунта::

    docker compose -f infra/docker-compose.yml exec api python scripts/check_cards_setup.py

С ключом бота (путь, видимый изнутри контейнера) — сравнить аккаунты.
Печатается только «тот же аккаунт» или «другой аккаунт», без адресов::

    ... python scripts/check_cards_setup.py --bot-key /путь/к/ключу-бота.json

Что проверяется:

* адрес сервисного аккаунта платформы — печатается: его вписывают в доступ
  папки и таблиц. Сам ключ не печатается никогда;
* книга карточек — у платформы право редактирования: она туда пишет. Книга
  кухни закрыта для записи, и право редактирования на ней — предупреждение:
  лишнее право лучше снять;
* папка фото — папка на общем диске, закрыта (доступ «Ограниченный»), в неё
  можно добавлять файлы; кто имеет к ней доступ;
* пробная загрузка 1 КБ JPEG → скачивание → корзина: сначала с узким доступом
  ``drive.file``, не вышло — с ``drive``. Вывод говорит, какое значение
  DRIVE_SCOPE выставить.

Скрипт ничего не меняет в доступах — только читает; исправляет человек.
Код выхода 0 — всё обязательное прошло; предупреждения его не портят.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kitchen.config import Settings, load_settings
from kitchen.sync.drive import (
    API_DISABLED_REASONS,
    INSPECT_SCOPE,
    SCOPES,
    DriveClient,
    DriveError,
    DriveScope,
    authorized_session,
    folder_is_closed,
)
from kitchen.sync.ownership import WRITE_OPEN

RULE = "─" * 78
FOLDER_MIME = "application/vnd.google-apps.folder"
ORDER: tuple[DriveScope, ...] = ("drive.file", "drive")
"""Сначала узкий доступ: хватит его — шире не нужно."""

ROLES = {
    "organizer": "Менеджер",
    "fileOrganizer": "Менеджер контента",
    "writer": "Автор",
    "commenter": "Комментатор",
    "reader": "Читатель",
    "owner": "Владелец",
}


def _probe_jpeg() -> bytes:
    """1 КБ с подписью JPEG: начало FFD8FF, конец FFD9 — как проверяет платформа.

    Картинки внутри нет: заголовок JFIF, комментарий-заполнитель и конец.
    Drive хранит байты как есть, а проверяется, что они вернутся те же.
    """
    head = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    tail = b"\xff\xd9"
    filler = 1024 - len(head) - len(tail) - 4
    comment = b"kitchen-platform: setup check".ljust(filler, b".")
    return head + b"\xff\xfe" + (filler + 2).to_bytes(2, "big") + comment + tail


PROBE = _probe_jpeg()


class Report:
    """Вывод по разделам и итог: ошибка портит код выхода, предупреждение — нет."""

    def __init__(self) -> None:
        self.failed = False
        self.warned = False

    def section(self, title: str) -> None:
        print()
        print(title)
        print(RULE)

    def ok(self, text: str) -> None:
        print(f"  OK        {text}")

    def warn(self, text: str) -> None:
        self.warned = True
        print(f"  ВНИМАНИЕ  {text}")

    def fail(self, text: str) -> None:
        self.failed = True
        print(f"  ОШИБКА    {text}")

    def note(self, text: str) -> None:
        print(f"            {text}".rstrip())


class _DriveOff(Exception):  # noqa: N818 — не сбой скрипта, а сигнал «дальше не идти»
    """Drive API выключен: все дальнейшие запросы получат тот же отказ."""

    def __init__(self, error: DriveError) -> None:
        super().__init__(str(error))
        self.error = error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверить настройку «Нового ингредиента»")
    parser.add_argument(
        "--bot-key",
        type=Path,
        help="ключ сервисного аккаунта бота: сказать, тот же это аккаунт или другой",
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    report = Report()

    print(RULE)
    print("ПРОВЕРКА НАСТРОЙКИ: НОВЫЙ ИНГРЕДИЕНТ")
    print(RULE)

    email = _account(settings.google_credentials_path, args.bot_key, report)
    if email is not None:
        # Таблицы и папку смотрит охват «только чтение сведений»: проверка
        # ничего не меняет, и права ей нужны лишь на это.
        inspect = _client(settings, INSPECT_SCOPE)
        try:
            _check_books(inspect, settings, report)
            if _check_folder(inspect, settings, email, report):
                _probe(settings, report)
        except _DriveOff as off:
            report.fail(_why(off.error))

    # Здесь встанет пробный вызов модели распознавания (флаг --llm: цена и
    # адрес polza.ai) — тем же клиентом, которым распознаёт платформа.

    print()
    print(RULE)
    if report.failed:
        print("ИТОГ: есть ошибки — «Новый ингредиент» не заработает, пока их не исправить.")
        return 1
    if report.warned:
        print("ИТОГ: обязательное в порядке; прочтите предупреждения выше.")
        return 0
    print("ИТОГ: всё в порядке.")
    return 0


# ---------------------------------------------------------------------------
# Ключ
# ---------------------------------------------------------------------------
def _account(path: Path, bot_key: Path | None, report: Report) -> str | None:
    report.section("СЕРВИСНЫЙ АККАУНТ")
    email = _client_email(path)
    if email is None:
        report.fail(f"ключ не прочитан — проверьте GOOGLE_CREDENTIALS_PATH ({path})")
        return None
    report.ok(f"адрес: {email}")
    report.note("этот адрес вписывают в доступ папки фото и таблиц")
    if bot_key is not None:
        bot = _client_email(bot_key)
        if bot is None:
            report.warn("ключ бота не прочитан — сравнить аккаунты не вышло")
        elif bot == email:
            report.ok("ключ бота: тот же аккаунт — доступы бота у платформы уже есть")
        else:
            report.ok("ключ бота: другой аккаунт — доступы бота платформе не достались")
    return email


def _client_email(path: Path) -> str | None:
    """Только адрес из ключа; остальное содержимое ключа не выходит отсюда."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    email = data.get("client_email") if isinstance(data, dict) else None
    return email if isinstance(email, str) and email else None


# ---------------------------------------------------------------------------
# Таблицы
# ---------------------------------------------------------------------------
def _check_books(client: DriveClient, settings: Settings, report: Report) -> None:
    """Право редактирования нужно ровно тем книгам, запись в которые открыта."""
    report.section("ТАБЛИЦЫ")
    books = (
        ("ingredient_cards", "книга карточек", "SHEETS_ID_INGREDIENT_CARDS"),
        ("kitchen", "книга кухни", "SHEETS_ID_KITCHEN"),
    )
    ids = {
        "ingredient_cards": settings.sheets_id_ingredient_cards,
        "kitchen": settings.sheets_id_kitchen,
    }
    for key, title, setting in books:
        book_id = ids[key]
        writes = key in WRITE_OPEN
        problem = report.fail if writes else report.warn
        if not book_id:
            problem(f"{title}: не задан {setting}")
            continue
        try:
            can_edit = _capability(client.metadata(book_id), "canEdit")
        except DriveError as error:
            _stop_if_disabled(error)
            problem(f"{title}: не открылась — {_why(error)}")
            continue
        if writes and can_edit:
            report.ok(f"{title}: есть право редактирования — платформа сюда пишет")
        elif writes:
            problem(
                f"{title}: нет права редактирования — дайте сервисному аккаунту роль «Редактор»"
            )
        elif can_edit:
            report.warn(
                f"{title}: есть право редактирования, а писать сюда платформе нельзя — "
                "книга закрыта для записи. Хватит роли «Читатель»"
            )
        else:
            report.ok(f"{title}: только чтение — платформа сюда не пишет")


# ---------------------------------------------------------------------------
# Папка фото
# ---------------------------------------------------------------------------
def _check_folder(client: DriveClient, settings: Settings, email: str, report: Report) -> bool:
    """Папка годится для фото? Открыта или не на общем диске — пробный файл туда
    не кладётся: в открытой папке он стал бы виден по ссылке."""
    report.section("ПАПКА ФОТО")
    folder_id = settings.drive_cards_folder_id
    if not folder_id:
        report.fail("не задан DRIVE_CARDS_FOLDER_ID — id закрытой папки на общем диске")
        return False
    try:
        folder = client.metadata(folder_id)
        people = client.permissions(folder_id)
    except DriveError as error:
        _stop_if_disabled(error)
        report.fail(f"папка не открылась — {_why(error)}")
        return False

    passed = True
    if folder.get("mimeType") != FOLDER_MIME:
        report.fail("DRIVE_CARDS_FOLDER_ID указывает не на папку")
        passed = False
    elif not folder.get("driveId"):
        report.fail(
            "папка не на общем диске: у сервисного аккаунта нет своего места, загрузка "
            "упадёт. Нужна папка на общем диске"
        )
        passed = False
    else:
        report.ok("папка на общем диске")
    if folder.get("trashed") is True:
        report.fail("папка в корзине")
        passed = False
    if folder_is_closed(people):
        report.ok("закрыта: доступ «Ограниченный», по ссылке посторонний не откроет")
    else:
        report.fail(
            "открыта: доступна всем, у кого есть ссылка, или всему домену — "
            "поставьте доступ «Ограниченный»"
        )
        passed = False
    # Сведения читаются охватом «только чтение», поэтому здесь — подсказка, а
    # решает пробная загрузка ниже: не прошла ни с одним доступом — ошибка.
    if _capability(folder, "canAddChildren"):
        report.ok("можно добавлять файлы")
    else:
        report.warn(
            "по сведениям Drive, добавлять файлы нельзя — сервисному аккаунту нужна роль "
            "«Менеджер контента»; проверит пробная загрузка"
        )

    report.note("")
    report.note("Доступ к папке:")
    for person in people:
        role = ROLES.get(str(person.get("role")), str(person.get("role")))
        mark = "  ← платформа" if person.get("emailAddress") == email else ""
        report.note(f"  · {role:18} {_who(person)}{mark}")
    return passed


def _who(person: dict[str, object]) -> str:
    kind = person.get("type")
    if kind == "anyone":
        return "все, у кого есть ссылка"
    if kind == "domain":
        return f"весь домен {person.get('domain', '')}".strip()
    address = str(person.get("emailAddress") or person.get("displayName") or "—")
    return f"группа {address}" if kind == "group" else address


# ---------------------------------------------------------------------------
# Пробная загрузка
# ---------------------------------------------------------------------------
def _probe(settings: Settings, report: Report) -> None:
    report.section("ПРОБНАЯ ЗАГРУЗКА: 1 КБ JPEG → скачивание → корзина")
    for scope in ORDER:
        if _try_probe(_client(settings, SCOPES[scope]), scope, report):
            _advise(scope, settings.drive_scope, report)
            return
    report.fail("пробный файл не прошёл ни с одним доступом — сохранять фото платформа не сможет")


def _try_probe(client: DriveClient, scope: DriveScope, report: Report) -> bool:
    name = f"проверка-настройки_{datetime.now(UTC):%Y-%m-%d_%H-%M-%S}.jpg"
    try:
        file_id = client.upload_jpeg(PROBE, name=name, app_properties={"purpose": "setup-check"})
    except DriveError as error:
        _stop_if_disabled(error)
        report.note(f"{scope}: загрузка не удалась — {_why(error)}")
        return False

    passed = True
    try:
        if client.download(file_id, max_bytes=len(PROBE) + 1) != PROBE:
            report.note(f"{scope}: скачанный файл не совпал с загруженным")
            passed = False
    except DriveError as error:
        report.note(f"{scope}: скачивание не удалось — {_why(error)}")
        passed = False
    try:
        client.trash(file_id)
    except DriveError as error:
        report.warn(
            f"{scope}: корзина не удалась — {_why(error)} Пробный файл «{name}» остался "
            "в папке: удалите его руками. Сервисному аккаунту нужна роль «Менеджер контента»"
        )
        return False
    if passed:
        report.ok(f"{scope}: загрузка, скачивание и корзина прошли")
    return passed


def _advise(working: DriveScope, configured: DriveScope, report: Report) -> None:
    if working == configured:
        report.ok(f"DRIVE_SCOPE={working} — так и выставлено")
    elif working == "drive.file":
        report.warn(
            f"хватает узкого доступа: выставьте DRIVE_SCOPE={working} в .env сервера "
            f"(сейчас «{configured}»)"
        )
    else:
        report.warn(
            f"узкого доступа drive.file не хватает: выставьте DRIVE_SCOPE={working} в .env "
            f"сервера (сейчас «{configured}»)"
        )


# ---------------------------------------------------------------------------
# Общее
# ---------------------------------------------------------------------------
def _client(settings: Settings, scope: str) -> DriveClient:
    path = settings.google_credentials_path
    refresh = settings.google_refresh_timeout
    return DriveClient(
        lambda: authorized_session(path, scope, refresh_timeout=refresh),
        folder_id=settings.drive_cards_folder_id,
        timeout=settings.google_timeout,
    )


def _capability(resource: dict[str, object], name: str) -> bool:
    capabilities = resource.get("capabilities")
    return isinstance(capabilities, dict) and capabilities.get(name) is True


def _stop_if_disabled(error: DriveError) -> None:
    if error.reason in API_DISABLED_REASONS:
        raise _DriveOff(error)


def _why(error: DriveError) -> str:
    """Текст для человека и, для администратора, что именно ответил Google."""
    if error.status is None:
        return str(error)
    return f"{error} [{error.status} {error.reason}".rstrip() + "]"


if __name__ == "__main__":
    raise SystemExit(main())
