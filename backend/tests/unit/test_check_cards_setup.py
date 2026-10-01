"""Скрипт проверки настройки «Нового ингредиента» — без сети и без Google.

Настоящий запуск — на сервере, один раз при настройке папки и потом после
любой правки доступа. Здесь он гоняется на фальшивом Drive: для каждого
охвата доступа своя сессия, как в бою.
"""

from __future__ import annotations

import base64
import io
import json
import re
import struct
import sys
from pathlib import Path

import check_cards_setup
import httpx
import pytest
import requests

from kitchen.config import Settings
from kitchen.llm.polza import PolzaClient, polza_from_settings
from kitchen.sync.drive import INSPECT_SCOPE, SCOPES, DriveClient
from tests.fake_drive import FOLDER_ID, ROBOT, SHEET_MIME, FakeDrive, FakeFile
from tests.fake_polza import (
    BASE_URL,
    KEY,
    MODEL,
    Answer,
    ClosingTransport,
    FakePolza,
    ok,
    refusal,
)

FAKE_PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\nNE-NASTOYASHCHII-KLYUCH\n-----END PRIVATE KEY-----\n"
)
BOT = "old-bot@example.iam.gserviceaccount.com"
"""Не подстрока адреса платформы: иначе «адреса бота нет в выводе» не проверить."""


def _key(path: Path, email: str, encoding: str = "utf-8") -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": email,
                "private_key_id": "fake-key-id",
                "private_key": FAKE_PRIVATE_KEY,
            }
        ),
        encoding=encoding,
    )
    return path


def _drive() -> FakeDrive:
    """Drive с закрытой папкой и двумя книгами: карточки можно править, кухню — нет."""
    fake = FakeDrive()
    fake.add(FakeFile("cards-book", "Карточки", SHEET_MIME, can_edit=True))
    fake.add(FakeFile("kitchen-book", "Кухня", SHEET_MIME, can_edit=False))
    return fake


class Setup:
    """Настройки, сессии по охватам и запуск скрипта."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.drive = _drive()
        self.by_scope: dict[str, FakeDrive] = {}
        self.opened: list[tuple[Path, str]] = []
        self.values: dict[str, object] = {
            "google_credentials_path": _key(tmp_path / "platform.json", ROBOT),
            "drive_cards_folder_id": FOLDER_ID,
            "drive_scope": "drive.file",
            "sheets_id_ingredient_cards": "cards-book",
            "sheets_id_kitchen": "kitchen-book",
            "google_connect_timeout": 4,
            "google_read_timeout": 44,
            "google_refresh_timeout": 14,
            "polza_api_key": KEY,
            "polza_base_url": BASE_URL,
            "llm_vision_model": MODEL,
        }
        self.polza = FakePolza(ok('{"ok": true}'))
        monkeypatch.setattr(check_cards_setup, "load_settings", self._settings)
        monkeypatch.setattr(check_cards_setup, "authorized_session", self._session)
        monkeypatch.setattr(check_cards_setup, "polza_from_settings", self._polza)

    def _settings(self) -> Settings:
        return Settings(_env_file=None, **self.values)

    def _session(self, path: Path, scope: str) -> requests.Session:
        self.opened.append((path, scope))
        return self.by_scope.get(scope, self.drive).session()

    def _polza(self, settings: Settings) -> PolzaClient | None:
        """Тот же клиент, что в бою, только вместо сети — фальшивый polza.ai."""
        self.polza_transport = ClosingTransport(self.polza)
        return polza_from_settings(settings, transport=self.polza_transport)

    def scopes(self) -> list[str]:
        """Охваты открытых сессий по порядку."""
        return [scope for _, scope in self.opened]

    def run(self, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
        code = check_cards_setup.main(list(argv))
        return code, capsys.readouterr().out


@pytest.fixture
def setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Setup:
    return Setup(monkeypatch, tmp_path)


def test_all_good(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Всё настроено: код 0, адрес аккаунта напечатан, узкого доступа хватает.

    Пробный файл загружен, прочитан и выброшен в корзину; ключ не напечатан;
    доступ к папке скрипт только читает."""
    code, out = setup.run(capsys)

    assert code == 0, out
    assert ROBOT in out
    assert "DRIVE_SCOPE=drive.file" in out
    assert "ОШИБКА" not in out
    assert "NE-NASTOYASHCHII-KLYUCH" not in out
    assert "fake-key-id" not in out
    [probe] = [f for f in setup.drive.files.values() if f.own]
    assert probe.trashed is True
    assert probe.parents == [FOLDER_ID]
    assert probe.content.startswith(b"\xff\xd8\xff")
    assert probe.content.endswith(b"\xff\xd9")
    assert len(probe.content) == 1024
    assert setup.drive.permission_writes() == []


def test_timeouts_and_scopes(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Книги смотрит полный ``drive`` (``drive.file`` их не видит, а «только
    чтение» могло бы занизить право редактирования), папку — «только чтение
    сведений», пробу грузит сначала узкий ``drive.file``. Всё — только GET,
    кроме самой пробы; таймауты — из настроек."""
    code, _ = setup.run(capsys)

    assert code == 0
    assert setup.scopes() == [SCOPES["drive"], INSPECT_SCOPE, SCOPES["drive.file"]]
    assert {s.timeout for s in setup.drive.sent} == {(4, 44)}
    probe = [f.id for f in setup.drive.files.values() if f.own]
    writes = [s for s in setup.drive.sent if s.method != "GET"]
    assert [s.method for s in writes] == ["POST", "PATCH"]
    assert writes[1].path.endswith(f"/{probe[0]}")


def test_books_read_with_full_scope(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Если бы право редактирования читалось охватом «только чтение», а Drive
    учитывал охват, книга кухни молча получила бы «только чтение»."""
    readonly = _drive()
    readonly.files["cards-book"].can_edit = False
    setup.by_scope[INSPECT_SCOPE] = readonly
    setup.drive.files["kitchen-book"].can_edit = True

    code, out = setup.run(capsys)

    assert code == 0, out
    assert "книга карточек: есть право редактирования" in out
    assert "книга кухни: есть право редактирования" in out
    assert not [s for s in readonly.sent if "book" in s.path]


def test_narrow_scope_not_enough_is_an_error(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """``drive.file`` не видит папку, созданную человеком: проба проходит только
    с ``drive``. Настроен ``drive.file`` — платформа не сохранит ни одного фото,
    это ошибка с понятным действием, а не предупреждение."""
    setup.by_scope[SCOPES["drive.file"]] = FakeDrive(only_own_files=True)

    code, out = setup.run(capsys)

    assert code == 1, out
    assert "с DRIVE_SCOPE=drive.file фото не сохранятся" in out
    assert "выставьте DRIVE_SCOPE=drive и перезапустите api" in out
    assert "всё в порядке" not in out
    assert setup.scopes()[-2:] == [SCOPES["drive.file"], SCOPES["drive"]]
    [probe] = [f for f in setup.drive.files.values() if f.own]
    assert probe.trashed is True


def test_full_scope_configured_and_needed(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    setup.values["drive_scope"] = "drive"
    setup.by_scope[SCOPES["drive.file"]] = FakeDrive(only_own_files=True)

    code, out = setup.run(capsys)

    assert code == 0, out
    assert "DRIVE_SCOPE=drive — так и выставлено" in out


def test_full_scope_configured_but_narrow_is_enough(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """Настроено шире нужного — работать будет; предупреждение, код 0."""
    setup.values["drive_scope"] = "drive"

    code, out = setup.run(capsys)

    assert code == 0, out
    assert "ВНИМАНИЕ" in out
    assert "выставьте DRIVE_SCOPE=drive.file" in out


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ReadTimeout("Read timed out. (read timeout=44)"),
        requests.exceptions.ConnectionError("Connection reset by peer"),
    ],
)
def test_google_not_answering_is_not_a_scope_problem(
    setup: Setup, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    """Google не ответил на загрузку — это не повод расширять охват: ошибка
    «повторите запуск», ``drive`` не пробуется. Ответ мог потеряться уже после
    того, как файл создан, — имя пробы напечатано, чтобы его можно было найти."""
    narrow = _drive()
    narrow.drop_next(error)
    setup.by_scope[SCOPES["drive.file"]] = narrow

    code, out = setup.run(capsys)

    assert code == 1
    assert "Google не ответил — повторите запуск" in out
    assert "DRIVE_SCOPE=" not in out
    assert setup.scopes() == [SCOPES["drive"], INSPECT_SCOPE, SCOPES["drive.file"]]
    assert "«проверка-настройки_" in out


def test_no_scope_works(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Загрузка не прошла ни с одним охватом — ошибка и никакого совета про DRIVE_SCOPE."""
    setup.drive.fail_next(403, "insufficientFilePermissions", method="POST")
    setup.drive.fail_next(403, "insufficientFilePermissions", method="POST")

    code, out = setup.run(capsys)

    assert code == 1
    assert "ОШИБКА" in out
    assert "DRIVE_SCOPE=" not in out
    assert setup.scopes()[-2:] == [SCOPES["drive.file"], SCOPES["drive"]]


def test_no_probe_into_a_folder_that_failed_checks(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """Папка открыта всему домену на изменение — пробный файл туда не кладётся:
    папку сначала чинят."""
    setup.drive.folder.permissions.append({"id": "d", "type": "domain", "role": "writer"})

    code, _ = setup.run(capsys)

    assert code == 1
    assert [s for s in setup.drive.sent if s.method != "GET"] == []


def test_drive_api_disabled(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Drive API выключен — одна понятная строка, а не десяток одинаковых отказов."""
    setup.drive.fail_next(403, "accessNotConfigured")

    code, out = setup.run(capsys)

    assert code == 1
    assert "включите Drive API" in out
    assert len(setup.drive.sent) == 1


def test_folder_on_my_drive_is_ok_with_account_space(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """Папка на «Моём диске» владельца — не ошибка (решение 01.10): фото
    загружает сервисный аккаунт, место — его. Сколько занято и сколько
    свободно — числами; проба идёт как обычно."""
    setup.drive.folder.drive_id = None
    setup.drive.quota = {"limit": str(15 * 1024**3), "usage": str(1536 * 1024**2)}

    code, out = setup.run(capsys)

    assert code == 0, out
    assert "ОШИБКА" not in out
    [line] = _lines(out, "OK", "«Моём диске»")
    assert "папка на «Моём диске» владельца; место — у сервисного аккаунта" in line
    [space] = _lines(out, "место сервисного аккаунта")
    assert "занято 1,5 ГБ" in space
    assert "свободно 13,5 ГБ из 15 ГБ" in space
    assert [s for s in setup.drive.sent if s.path == "/drive/v3/about"]
    [probe] = [f for f in setup.drive.files.values() if f.own]
    assert probe.trashed is True


def test_account_space_unknown_is_only_a_note(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """Место узнать не вышло — это подсказка, а не ошибка: решает пробная загрузка."""
    setup.drive.folder.drive_id = None
    setup.drive.quota = {"usage": "много"}

    code, out = setup.run(capsys)

    assert code == 0, out
    assert "ВНИМАНИЕ" not in out
    assert _lines(out, "место сервисного аккаунта узнать не удалось")


def test_shared_drive_folder_does_not_ask_account_space(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """На общем диске фото занимают место диска, а не аккаунта: про место
    аккаунта не спрашиваем."""
    code, out = setup.run(capsys)

    assert code == 0, out
    assert "папка на общем диске" in out
    assert not [s for s in setup.drive.sent if s.path == "/drive/v3/about"]


@pytest.mark.parametrize("kind", ["anyone", "domain"])
@pytest.mark.parametrize("role", ["writer", "fileOrganizer", "organizer"])
def test_folder_editable_by_link_fails_and_is_not_fixed(
    setup: Setup, capsys: pytest.CaptureFixture[str], kind: str, role: str
) -> None:
    """Папка открыта по ссылке (или всему домену) на изменение — ошибка: фото
    может удалить кто угодно. Пробы нет. Исправляет человек: скрипт доступ
    только читает."""
    setup.drive.folder.permissions.append({"id": "open", "type": kind, "role": role})

    code, out = setup.run(capsys)

    assert code == 1
    [line] = _lines(out, "ОШИБКА", "папка открыта на изменение")
    assert "папка открыта на изменение всем по ссылке — фото может удалить кто угодно" in line
    assert [s for s in setup.drive.sent if s.method != "GET"] == []
    assert setup.drive.permission_writes() == []


@pytest.mark.parametrize("kind", ["anyone", "domain"])
@pytest.mark.parametrize("role", ["reader", "commenter"])
def test_folder_readable_by_link_is_a_warning(
    setup: Setup, capsys: pytest.CaptureFixture[str], kind: str, role: str
) -> None:
    """Доступ «все со ссылкой — читатель» (решение 01.10) — предупреждение: фото
    видны всем, у кого ссылка, но удалить их нельзя. Код 0, проба идёт."""
    setup.drive.folder.permissions.append({"id": "anyoneWithLink", "type": kind, "role": role})

    code, out = setup.run(capsys)

    assert code == 0, out
    assert "ОШИБКА" not in out
    [line] = _lines(out, "ВНИМАНИЕ", "фото видны")
    assert "фото видны всем, у кого есть ссылка" in line
    [probe] = [f for f in setup.drive.files.values() if f.own]
    assert probe.trashed is True
    assert setup.drive.permission_writes() == []


def test_closed_folder_is_ok(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = setup.run(capsys)

    assert code == 0, out
    assert _lines(out, "OK", "закрыта: доступ «Ограниченный»")
    assert "фото видны" not in out


def test_who_has_access_is_listed(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = setup.run(capsys)

    assert code == 0
    assert "Менеджер контента" in out
    assert "chef@example.com" in out


def test_cannot_add_files(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Нет права добавлять файлы: сведения Drive — подсказка, решает пробная
    загрузка, и она не проходит ни с одним доступом."""
    setup.drive.folder.can_add_children = False

    code, out = setup.run(capsys)

    assert code == 1
    assert "добавлять файлы нельзя" in out
    assert "Менеджер контента" in out
    assert setup.scopes()[-2:] == [SCOPES["drive.file"], SCOPES["drive"]]


def test_capability_hint_does_not_override_the_probe(
    setup: Setup, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive сказал «нельзя», а пробная загрузка прошла — верим загрузке:
    сведения читаются охватом «только чтение»."""
    monkeypatch.setattr(
        check_cards_setup, "_capability", lambda resource, name: name != "canAddChildren"
    )

    code, out = setup.run(capsys)

    assert code == 0, out
    assert "добавлять файлы нельзя" in out
    assert "DRIVE_SCOPE=drive.file" in out


def test_cards_book_must_be_editable(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    setup.drive.files["cards-book"].can_edit = False

    code, out = setup.run(capsys)

    assert code == 1
    assert "книга карточек" in out
    assert "Редактор" in out


def test_kitchen_book_editable_is_only_a_warning(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """Книга кухни закрыта для записи — право редактирования ей не нужно.
    Лишнее право — предупреждение, не ошибка."""
    setup.drive.files["kitchen-book"].can_edit = True

    code, out = setup.run(capsys)

    assert code == 0
    assert "ВНИМАНИЕ" in out
    assert "книга кухни" in out


def test_books_not_configured(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    setup.values["sheets_id_ingredient_cards"] = ""

    code, out = setup.run(capsys)

    assert code == 1
    assert "SHEETS_ID_INGREDIENT_CARDS" in out


def test_folder_not_configured(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    setup.values["drive_cards_folder_id"] = ""

    code, out = setup.run(capsys)

    assert code == 1
    assert "DRIVE_CARDS_FOLDER_ID" in out
    assert all(not f.own for f in setup.drive.files.values())


def test_missing_key(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    setup.values["google_credentials_path"] = setup.tmp_path / "нет.json"

    code, out = setup.run(capsys)

    assert code == 1
    assert "GOOGLE_CREDENTIALS_PATH" in out
    assert setup.opened == []


def test_trash_refused_names_the_leftover(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Корзина не удалась — нужна роль «Менеджер контента»; оставшийся файл
    назван, чтобы его убрали руками."""
    setup.drive.fail_next(403, "insufficientFilePermissions", method="PATCH")
    setup.drive.fail_next(403, "insufficientFilePermissions", method="PATCH")

    code, out = setup.run(capsys)

    assert code == 1
    warnings = _lines(out, "ВНИМАНИЕ", "корзина не удалась")
    assert warnings
    assert all("Менеджер контента" in line for line in warnings)
    leftovers = [f for f in setup.drive.files.values() if f.own and not f.trashed]
    assert leftovers
    assert all(f.name in out for f in leftovers)


def test_trash_outside_photo_folder_has_no_role_hint(
    setup: Setup, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пробный файл пропал из папки фото до корзины — клиент его не выбросит.
    Роль тут ни при чём: подсказка «нужна роль „Менеджер контента“» увела бы
    администратора чинить не то."""
    trash = DriveClient.trash

    def moved_away_then_trash(client: DriveClient, file_id: str) -> None:
        setup.drive.files[file_id].parents = ["chuzhaya-papka"]
        trash(client, file_id)

    monkeypatch.setattr(DriveClient, "trash", moved_away_then_trash)

    code, out = setup.run(capsys)

    assert code == 1
    refusals = _lines(out, "корзина не удалась")
    assert refusals
    assert all("не из папки фото" in line for line in refusals)
    assert not any("Менеджер контента" in line for line in refusals)


def _lines(out: str, *parts: str) -> list[str]:
    return [line for line in out.splitlines() if all(part in line for part in parts)]


@pytest.mark.parametrize(("email", "verdict"), [(ROBOT, "тот же аккаунт"), (BOT, "другой аккаунт")])
def test_bot_key_compared_without_addresses(
    setup: Setup, capsys: pytest.CaptureFixture[str], email: str, verdict: str
) -> None:
    """С ключом бота — только «тот же/другой»; адрес бота не печатается."""
    bot_key = _key(setup.tmp_path / "bot.json", email)

    code, out = setup.run(capsys, "--bot-key", str(bot_key))

    assert code == 0
    assert verdict in out
    assert BOT not in out
    assert ROBOT in out


def test_unreadable_bot_key_is_a_warning(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = setup.run(capsys, "--bot-key", str(setup.tmp_path / "нет-бота.json"))

    assert code == 0
    assert "ключ бота не прочитан" in out


@pytest.mark.parametrize(("email", "verdict"), [(ROBOT, "тот же аккаунт"), (BOT, "другой аккаунт")])
def test_bot_key_from_stdin(
    setup: Setup,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    email: str,
    verdict: str,
) -> None:
    """``--bot-key -`` читает ключ бота из stdin: второй копии ключа внутри
    контейнера класть не нужно (``exec -T api … --bot-key - < bot.json``)."""
    key = {"client_email": email, "private_key": FAKE_PRIVATE_KEY}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(key)))

    code, out = setup.run(capsys, "--bot-key", "-")

    assert code == 0
    assert verdict in out
    assert BOT not in out
    assert "NE-NASTOYASHCHII-KLYUCH" not in out


def test_folder_link_instead_of_id(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Ссылка вместо id — понятная ошибка настройки и код 1, без трассировки."""
    setup.values["drive_cards_folder_id"] = "https://drive.google.com/drive/folders/abc123"

    code, out = setup.run(capsys)

    assert code == 1
    assert "ОШИБКА" in out
    assert "после /folders/" in out
    assert "Traceback" not in out
    assert setup.opened == []


# ---------------------------------------------------------------------------
# Ключ не в UTF-8: Блокнот сохраняет «Юникод» как UTF-16
# ---------------------------------------------------------------------------
def test_platform_key_not_in_utf8(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Ключ пересохранили Блокнотом в UTF-16 — понятная строка и код 1, а не
    трассировка ``UnicodeDecodeError``."""
    _key(setup.tmp_path / "platform.json", ROBOT, encoding="utf-16")

    code, out = setup.run(capsys)

    assert code == 1
    assert "GOOGLE_CREDENTIALS_PATH" in out
    assert "UTF-8" in out
    assert setup.opened == []


def test_bot_key_not_in_utf8(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    bot_key = _key(setup.tmp_path / "bot.json", BOT, encoding="utf-16")

    code, out = setup.run(capsys, "--bot-key", str(bot_key))

    assert code == 0
    assert "ключ бота не прочитан" in out
    assert "UTF-8" in out


def test_platform_key_with_bom(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Блокнот сохраняет «UTF-8 с BOM»: метка в начале файла ломает разбор JSON и
    у Google, поэтому это ошибка — с понятной причиной, а не «не JSON»."""
    _key(setup.tmp_path / "platform.json", ROBOT, encoding="utf-8-sig")

    code, out = setup.run(capsys)

    assert code == 1
    assert "BOM" in out
    assert "не JSON" not in out
    assert setup.opened == []


def test_bom_is_not_a_literal_in_the_source() -> None:
    """Невидимый символ в исходнике легко потерять при правке — и проверка
    «начинается с BOM» стала бы «начинается с пустой строки»: с BOM любой ключ."""
    source = Path(check_cards_setup.__file__).read_text(encoding="utf-8")

    assert chr(0xFEFF) not in source
    assert chr(0xFEFF) == check_cards_setup.BOM


def test_bot_key_with_bom(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    bot_key = _key(setup.tmp_path / "bot.json", BOT, encoding="utf-8-sig")

    code, out = setup.run(capsys, "--bot-key", str(bot_key))

    assert code == 0
    assert "ключ бота не прочитан" in out
    assert "BOM" in out


def test_bot_key_from_stdin_not_in_utf8(
    setup: Setup, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps({"client_email": BOT, "private_key": FAKE_PRIVATE_KEY}).encode("utf-16")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))

    code, out = setup.run(capsys, "--bot-key", "-")

    assert code == 0
    assert "ключ бота не прочитан" in out
    assert "UTF-8" in out


# ---------------------------------------------------------------------------
# --llm: пробный вызов модели распознавания
# ---------------------------------------------------------------------------
def _jpeg_size(jpeg: bytes) -> tuple[int, int]:
    """Высота и ширина из заголовка SOF0."""
    start = jpeg.index(b"\xff\xc0")
    height, width = struct.unpack(">HH", jpeg[start + 5 : start + 9])
    return height, width


def test_llm_probe(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    """Пробный вызов тем же клиентом и той же моделью, что распознают этикетки:
    напечатаны адрес, модель, цена в рублях и время; ключ — нет. Картинка —
    маленький сгенерированный JPEG, не фото."""
    code, out = setup.run(capsys, "--llm")

    assert code == 0, out
    assert BASE_URL in out
    assert MODEL in out
    assert "0,0123 ₽" in out
    assert re.search(r"\d+,\d с\b", out)
    assert KEY not in out
    [body] = setup.polza.bodies()
    assert body["model"] == MODEL
    [image] = [p for p in body["messages"][1]["content"] if p["type"] == "image_url"]
    jpeg = base64.b64decode(image["image_url"]["url"].removeprefix("data:image/jpeg;base64,"))
    assert jpeg.startswith(b"\xff\xd8\xff")
    assert jpeg.endswith(b"\xff\xd9")
    assert len(jpeg) < 1024
    height, width = _jpeg_size(jpeg)
    assert min(height, width) > 10, "картинку меньше 11 пикселей модели Qwen не принимают"
    assert setup.polza_transport.closed, "клиент закрыт после пробы"


def test_no_llm_call_without_the_flag(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = setup.run(capsys)

    assert code == 0
    assert setup.polza.requests == []
    assert "polza" not in out.lower()


@pytest.mark.parametrize(
    ("answer", "text"),
    [
        (refusal(401), "ключ не принят"),
        (refusal(403), "ключ не принят"),
        (refusal(402), "нет денег на счёте"),
        (refusal(404), "модель не найдена"),
        (httpx.ConnectError, "нет связи"),
        (httpx.ReadTimeout, "нет связи"),
        (refusal(400, "image input is not supported"), "и в режиме JSON, и без него"),
        (refusal(400, "image input is not supported"), "image input is not supported"),
        (refusal(400), "LLM_VISION_MODEL"),
        (refusal(422, "Unprocessable image"), "отклонил запрос: Unprocessable image"),
        (refusal(402, "Insufficient balance"), "Insufficient balance"),
        (httpx.Response(200, content=b"<html>login</html>"), "POLZA_BASE_URL"),
    ],
)
def test_llm_probe_failure_is_explained(
    setup: Setup, capsys: pytest.CaptureFixture[str], answer: Answer, text: str
) -> None:
    setup.polza = FakePolza(answer)

    code, out = setup.run(capsys, "--llm")

    assert code == 1
    assert text in out
    assert "ОШИБКА" in out
    assert KEY not in out
    assert "Сообщите администратору" not in out, "скрипт читает администратор — нужна подсказка"
    [failure] = _lines(out, "пробный вызов не прошёл")
    if isinstance(answer, httpx.Response):
        reason = answer.json()["error"]["message"] if answer.status_code != 200 else ""
        assert not reason or failure.count(reason) == 1, f"причина — один раз: {failure}"


def test_llm_probe_without_key(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    setup.values["polza_api_key"] = ""

    code, out = setup.run(capsys, "--llm")

    assert code == 1
    assert "POLZA_API_KEY" in out
    assert setup.polza.requests == []


def test_llm_probe_without_price_still_passes(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """Вызов прошёл, а цену polza.ai не прислал — проба пройдена, об этом сказано."""
    setup.polza = FakePolza(ok('{"ok": true}', usage=None))

    code, out = setup.run(capsys, "--llm")

    assert code == 0, out
    assert "цену не сообщил" in out
