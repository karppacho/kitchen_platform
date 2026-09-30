"""Скрипт проверки настройки «Нового ингредиента» — без сети и без Google.

Настоящий запуск — на сервере, один раз при настройке папки и потом после
любой правки доступа. Здесь он гоняется на фальшивом Drive: для каждого
охвата доступа своя сессия, как в бою.
"""

from __future__ import annotations

import io
import json
import sys
from typing import TYPE_CHECKING

import check_cards_setup
import pytest
import requests

from kitchen.config import Settings
from kitchen.sync.drive import INSPECT_SCOPE, SCOPES
from tests.fake_drive import FOLDER_ID, ROBOT, SHEET_MIME, FakeDrive, FakeFile

if TYPE_CHECKING:
    from pathlib import Path

FAKE_PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\nNE-NASTOYASHCHII-KLYUCH\n-----END PRIVATE KEY-----\n"
)
BOT = "old-bot@example.iam.gserviceaccount.com"
"""Не подстрока адреса платформы: иначе «адреса бота нет в выводе» не проверить."""


def _key(path: Path, email: str) -> Path:
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": email,
                "private_key_id": "fake-key-id",
                "private_key": FAKE_PRIVATE_KEY,
            }
        ),
        encoding="utf-8",
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
        }
        monkeypatch.setattr(check_cards_setup, "load_settings", self._settings)
        monkeypatch.setattr(check_cards_setup, "authorized_session", self._session)

    def _settings(self) -> Settings:
        return Settings(_env_file=None, **self.values)

    def _session(self, path: Path, scope: str) -> requests.Session:
        self.opened.append((path, scope))
        return self.by_scope.get(scope, self.drive).session()

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
    """Папка открыта всем — пробный файл туда не кладётся: он стал бы виден
    по ссылке."""
    setup.drive.folder.permissions.append({"id": "d", "type": "domain", "role": "reader"})

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


def test_folder_not_on_shared_drive(setup: Setup, capsys: pytest.CaptureFixture[str]) -> None:
    setup.drive.folder.drive_id = None

    code, out = setup.run(capsys)

    assert code == 1
    assert "не на общем диске" in out


def test_open_folder_fails_and_is_not_fixed(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    """Папка «доступна всем по ссылке» — ошибка. Исправляет человек: скрипт
    доступ только читает."""
    setup.drive.folder.permissions.append(
        {"id": "anyoneWithLink", "type": "anyone", "role": "reader"}
    )

    code, out = setup.run(capsys)

    assert code == 1
    assert "всем, у кого есть ссылка" in out
    assert "Ограниченный" in out
    assert setup.drive.permission_writes() == []


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
    assert "Менеджер контента" in out
    leftovers = [f for f in setup.drive.files.values() if f.own and not f.trashed]
    assert leftovers
    assert all(f.name in out for f in leftovers)


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
