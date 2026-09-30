"""Фото карточек в Google Drive: только закрытая папка общего диска.

Клиент проверяется на фальшивом Drive, подменяющем транспорт requests: до
него доходят те же байты, параметры и таймауты, что ушли бы в Google. Ни
одного настоящего запроса.

Главное, что здесь сторожится: платформа никогда не открывает доступ к фото
(ни одного запроса на запись в ``/permissions``), каждый запрос видит общий
диск (``supportsAllDrives=true``) и ограничен таймаутом.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import google.auth.exceptions
import google.auth.transport.requests
import pytest
import requests
from google.oauth2 import service_account
from pydantic import ValidationError

from kitchen.config import Settings
from kitchen.domain import cards
from kitchen.sync import drive
from kitchen.sync.drive import (
    SCOPES,
    DriveClient,
    DriveError,
    authorized_session,
    drive_from_settings,
    explain_drive_error,
    folder_is_closed,
)
from tests.fake_drive import (
    CHEF,
    FOLDER_ID,
    ROBOT,
    SHARED_DRIVE_ID,
    SHEET_MIME,
    UPLOAD,
    FakeDrive,
    FakeFile,
)

if TYPE_CHECKING:
    from pathlib import Path

TIMEOUT = (7, 42)
"""Не значения по умолчанию: тест видит, что дошли именно настройки."""

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 1000 + b"\xff\xd9"
NAME = "Север_Сыр_моцарелла_этикетка_2026-09-30_12-00-00.jpg"


def _client(fake: FakeDrive, folder_id: str = FOLDER_ID) -> DriveClient:
    return DriveClient(fake.session, folder_id=folder_id, timeout=TIMEOUT)


def _upload(client: DriveClient, content: bytes = JPEG) -> str:
    return client.upload_jpeg(content, name=NAME, app_properties={"draft": "d-1", "kind": "label"})


def _every_call(fake: FakeDrive) -> DriveClient:
    """Прогнать все вызовы клиента: загрузку, скачивание, корзину, чтение."""
    client = _client(fake)
    file_id = _upload(client)
    client.download(file_id, max_bytes=10_000)
    client.trash(file_id)
    client.metadata(FOLDER_ID)
    client.permissions(FOLDER_ID)
    return client


# ---------------------------------------------------------------------------
# Загрузка
# ---------------------------------------------------------------------------
def test_upload_is_multipart_with_parents_properties_and_name() -> None:
    """Одним запросом multipart: метаданные (имя, папка, appProperties) и сам JPEG.

    Папка — только настроенная закрытая: файл, созданный без неё, лёг бы в
    «Мой диск» сервисного аккаунта, а у того нет ни места, ни шефа в доступе.
    """
    fake = FakeDrive()

    file_id = _upload(_client(fake))

    [sent] = fake.sent
    assert (sent.method, sent.path) == ("POST", "/upload/drive/v3/files")
    assert sent.params["uploadType"] == "multipart"
    assert sent.headers["Content-Type"].startswith("multipart/related; boundary=")
    stored = fake.files[file_id]
    assert set(stored.metadata) == {"name", "mimeType", "parents", "appProperties"}
    assert stored.name == NAME
    assert stored.parents == [FOLDER_ID]
    assert stored.app_properties == {"draft": "d-1", "kind": "label"}
    assert stored.mime_type == "image/jpeg"
    assert stored.content == JPEG


def test_fake_refuses_unknown_upload_metadata() -> None:
    """Фальшивка не принимает молча то, чего не понимает: лишний ключ в
    метаданных загрузки (права, общий доступ) — AssertionError, а не зелёный тест."""
    fake = FakeDrive()
    metadata = {"name": NAME, "parents": [FOLDER_ID], "writersCanShare": True}
    body = (
        b"--b\r\nContent-Type: application/json\r\n\r\n"
        + json.dumps(metadata).encode()
        + b"\r\n--b\r\nContent-Type: image/jpeg\r\n\r\n"
        + JPEG
        + b"\r\n--b--\r\n"
    )

    with pytest.raises(AssertionError, match="writersCanShare"):
        fake.session().post(
            f"https://www.googleapis.com{UPLOAD}",
            params={"uploadType": "multipart", "supportsAllDrives": "true"},
            data=body,
            headers={"Content-Type": "multipart/related; boundary=b"},
            timeout=TIMEOUT,
        )


def test_never_writes_permissions() -> None:
    """Доступ к фото даёт папка, а не файл: ни одного запроса на запись в
    ``/permissions`` — ни при загрузке, ни при скачивании, ни при корзине.
    Бот делал каждое фото «доступным всем по ссылке» — здесь этого нет."""
    fake = FakeDrive()

    _every_call(fake)

    assert fake.permission_writes() == []
    assert [s for s in fake.sent if "/permissions" in s.path] == [
        s for s in fake.sent if s.method == "GET" and s.path.endswith("/permissions")
    ]
    assert all(p["type"] == "user" for p in fake.folder.permissions)


def test_every_request_supports_all_drives() -> None:
    """Папка — на общем диске. Без ``supportsAllDrives=true`` Drive отвечает
    «файл не найден» на всё, что там лежит."""
    fake = FakeDrive()

    _every_call(fake)

    assert len(fake.sent) == 6
    assert all(s.params.get("supportsAllDrives") == "true" for s in fake.sent)


def test_every_request_has_timeouts() -> None:
    """У requests таймаута нет по умолчанию: зависший запрос вешает ручку
    навсегда. Каждый запрос — с таймаутом из настроек."""
    fake = FakeDrive()

    _every_call(fake)

    assert [s.timeout for s in fake.sent] == [TIMEOUT] * 6


class _Credentials:
    """Учётка вместо ключа: запоминает, с каким таймаутом её просили обновить токен."""

    def __init__(self) -> None:
        self.refresh_timeouts: list[object] = []

    def before_request(
        self, request: object, method: str, url: str, headers: dict[str, str]
    ) -> None:
        self.refresh_timeouts.append(getattr(request, "keywords", {}).get("timeout"))
        headers["Authorization"] = "Bearer test-token"


def test_token_refresh_uses_the_request_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Настоящая AuthorizedSession: токен обновляется с таймаутом самого запроса,
    поэтому таймаут на каждом запросе закрывает и обновление. Своего таймаута
    обновления google-auth 2.57 не применяет — его и не передаём."""
    credentials = _Credentials()
    seen: dict[str, object] = {}

    def from_file(filename: str, *, scopes: list[str]) -> _Credentials:
        seen.update(filename=filename, scopes=scopes)
        return credentials

    monkeypatch.setattr(service_account.Credentials, "from_service_account_file", from_file)
    fake = FakeDrive()

    def connect() -> requests.Session:
        session = authorized_session(tmp_path / "key.json", SCOPES["drive.file"])
        assert isinstance(session, google.auth.transport.requests.AuthorizedSession)
        session.mount("https://", fake)
        return session

    DriveClient(connect, folder_id=FOLDER_ID, timeout=TIMEOUT).metadata(FOLDER_ID)

    assert seen == {
        "filename": str(tmp_path / "key.json"),
        "scopes": ["https://www.googleapis.com/auth/drive.file"],
    }
    assert credentials.refresh_timeouts == [TIMEOUT]
    assert [s.timeout for s in fake.sent] == [TIMEOUT]
    assert fake.sent[0].headers["Authorization"] == "Bearer test-token"


def test_one_file_id_rule_for_domain_and_drive() -> None:
    """Правило id файла Drive — одно: ссылка в листе и адрес запроса не расходятся."""
    assert drive.DRIVE_FILE_ID is cards.DRIVE_FILE_ID


def test_upload_reply_without_id_is_bad_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDrive()
    monkeypatch.setattr(fake, "_upload", lambda sent: {"name": NAME})

    with pytest.raises(DriveError) as caught:
        _upload(_client(fake))

    assert caught.value.kind == "bad_reply"


def test_upload_without_folder_setting_refused_without_request() -> None:
    fake = FakeDrive()

    with pytest.raises(ValueError, match="DRIVE_CARDS_FOLDER_ID"):
        _upload(_client(fake, folder_id=""))

    assert fake.sent == []


# ---------------------------------------------------------------------------
# Отказы Google — понятным текстом
# ---------------------------------------------------------------------------
def test_storage_quota_is_quota_with_clear_text() -> None:
    """403 ``storageQuotaExceeded`` — не «нет прав», а «нет места»; так же Drive
    отвечает, когда папка не на общем диске: своего места у сервисного
    аккаунта нет."""
    fake = FakeDrive()
    fake.fail_next(403, "storageQuotaExceeded")

    with pytest.raises(DriveError) as caught:
        _upload(_client(fake))

    assert caught.value.kind == "quota"
    assert caught.value.status == 403
    assert caught.value.reason == "storageQuotaExceeded"
    assert "нет места" in str(caught.value)
    assert "общем диске" in str(caught.value)


def test_folder_outside_shared_drive_is_quota() -> None:
    fake = FakeDrive()
    fake.folder.drive_id = None

    with pytest.raises(DriveError) as caught:
        _upload(_client(fake))

    assert caught.value.kind == "quota"


def test_drive_api_disabled_says_enable_it() -> None:
    fake = FakeDrive()
    fake.fail_next(403, "accessNotConfigured")

    with pytest.raises(DriveError) as caught:
        _client(fake).metadata(FOLDER_ID)

    assert caught.value.kind == "forbidden"
    assert caught.value.reason == "accessNotConfigured"
    assert "включите Drive API" in str(caught.value)
    assert "Сообщите администратору" in str(caught.value)


@pytest.mark.parametrize(
    ("status", "reason", "kind"),
    [
        (403, "insufficientFilePermissions", "forbidden"),
        (401, "authError", "forbidden"),
        (404, "notFound", "not_found"),
        (403, "userRateLimitExceeded", "unavailable"),
        (429, "rateLimitExceeded", "unavailable"),
        (500, "backendError", "unavailable"),
        (503, "backendError", "unavailable"),
        (400, "badRequest", "bad_reply"),
    ],
)
def test_google_refusals_by_kind(status: int, reason: str, kind: str) -> None:
    fake = FakeDrive()
    fake.fail_next(status, reason)

    with pytest.raises(DriveError) as caught:
        _client(fake).metadata("fake-file-1")

    assert caught.value.kind == kind
    assert caught.value.status == status
    assert str(caught.value)


def test_service_disabled_in_new_error_format() -> None:
    """Новые ответы Google называют выключенный API в ``details``."""
    body = {
        "error": {
            "code": 403,
            "message": "Google Drive API has not been used in project 1 before",
            "status": "PERMISSION_DENIED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED"}
            ],
        }
    }

    error = explain_drive_error(403, json.dumps(body).encode())

    assert error.kind == "forbidden"
    assert "включите Drive API" in str(error)


def test_error_page_that_is_not_json() -> None:
    """Страница прокси вместо JSON: вид — по коду ответа."""
    assert explain_drive_error(502, b"<html>Bad Gateway</html>").kind == "unavailable"
    assert explain_drive_error(418, b"<html>teapot</html>").kind == "bad_reply"


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ReadTimeout("Read timed out. (read timeout=42)"),
        requests.exceptions.ConnectionError("Connection reset by peer"),
    ],
)
def test_network_failure_is_unavailable(error: Exception) -> None:
    fake = FakeDrive()
    fake.drop_next(error)

    with pytest.raises(DriveError) as caught:
        _upload(_client(fake))

    assert caught.value.kind == "unavailable"
    assert caught.value.status is None


def test_revoked_key_is_forbidden() -> None:
    """Google не выдал токен (ключ отозван) — чинит администратор, повтор не поможет."""
    fake = FakeDrive()
    fake.drop_next(google.auth.exceptions.RefreshError("invalid_grant: Invalid JWT Signature."))

    with pytest.raises(DriveError) as caught:
        _upload(_client(fake))

    assert caught.value.kind == "forbidden"


def test_reply_that_is_not_json_is_bad_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDrive()
    monkeypatch.setattr(fake, "_route", lambda sent: (200, b"<html>login</html>", "text/html"))

    with pytest.raises(DriveError) as caught:
        _client(fake).metadata(FOLDER_ID)

    assert caught.value.kind == "bad_reply"


def test_unreadable_key_is_forbidden_not_crash(tmp_path: Path) -> None:
    """Сессия собирается при первом запросе: без ключа приложение стартует, а
    загрузка фото отвечает понятно."""
    settings = Settings(
        _env_file=None,
        google_credentials_path=tmp_path / "нет-ключа.json",
        drive_cards_folder_id=FOLDER_ID,
    )
    client = drive_from_settings(settings)

    with pytest.raises(DriveError) as caught:
        _upload(client)

    assert caught.value.kind == "forbidden"
    assert "GOOGLE_CREDENTIALS_PATH" in str(caught.value)


# ---------------------------------------------------------------------------
# Скачивание и корзина
# ---------------------------------------------------------------------------
def test_download_refuses_file_over_max_bytes() -> None:
    """Файл больше ``max_bytes`` — отказ, а не молча обрезанная картинка: битое
    фото в прокси или мусор в распознавании без следа хуже понятной ошибки.
    Целиком такой файл не читается — память сервера цела: прочитано не больше
    ``max_bytes`` и ещё одного куска в 64 КБ, на котором предел перейдён."""
    fake = FakeDrive()
    client = _client(fake)
    huge = b"\xff\xd8" + bytes(range(256)) * 4096
    file_id = client.upload_jpeg(huge, name=NAME)

    with pytest.raises(DriveError) as caught:
        client.download(file_id, max_bytes=1000)

    assert caught.value.kind == "too_large"
    assert "больше допустимого" in str(caught.value)
    assert fake.served <= 1000 + 64 * 1024
    media = [s for s in fake.sent if s.params.get("alt") == "media"]
    assert [(s.method, s.path) for s in media] == [("GET", f"/drive/v3/files/{file_id}")]


def test_download_up_to_max_bytes() -> None:
    fake = FakeDrive()
    client = _client(fake)
    file_id = client.upload_jpeg(JPEG, name=NAME)

    assert client.download(file_id, max_bytes=len(JPEG)) == JPEG
    assert client.download(file_id, max_bytes=100_000) == JPEG
    with pytest.raises(ValueError, match="max_bytes"):
        client.download(file_id, max_bytes=0)


def test_download_of_google_document_is_refused() -> None:
    fake = FakeDrive()
    fake.add(FakeFile("sheet-1", "Книга", SHEET_MIME, drive_id=None))

    with pytest.raises(DriveError) as caught:
        _client(fake).download("sheet-1", max_bytes=1000)

    assert caught.value.kind == "forbidden"


def test_trash_moves_to_trash_and_never_deletes() -> None:
    """Корзина, а не удаление: ошибочно выброшенное фото шеф достанет сам."""
    fake = FakeDrive()
    client = _client(fake)
    file_id = _upload(client)

    client.trash(file_id)

    assert fake.files[file_id].trashed is True
    assert [s.method for s in fake.sent] == ["POST", "GET", "PATCH"]
    assert "parents" in fake.sent[1].params["fields"]
    assert json.loads(fake.sent[-1].body) == {"trashed": True}


def test_trash_refuses_file_outside_photo_folder() -> None:
    """С охватом ``drive`` платформа видит всё, что открыто сервисному аккаунту.
    Неверный id не должен отправить в корзину чужой файл — только из папки фото."""
    fake = FakeDrive()
    fake.add(FakeFile("other-folder", "Другое", "application/vnd.google-apps.folder"))
    fake.add(
        FakeFile(
            "chef-file",
            "Меню.jpg",
            "image/jpeg",
            parents=["other-folder"],
            drive_id=SHARED_DRIVE_ID,
        )
    )

    with pytest.raises(DriveError) as caught:
        _client(fake).trash("chef-file")

    assert caught.value.kind == "forbidden"
    assert "не из папки фото" in str(caught.value)
    assert fake.files["chef-file"].trashed is False
    assert [s.method for s in fake.sent] == ["GET"]


def test_trash_of_missing_file_is_already_done() -> None:
    """Файла уже нет (или его убрали между проверкой и корзиной) — цель
    достигнута, это не ошибка."""
    fake = FakeDrive()
    client = _client(fake)

    client.trash("gone-file")
    assert [s.method for s in fake.sent] == ["GET"]

    file_id = _upload(client)
    fake.fail_next(404, "notFound", method="PATCH")
    client.trash(file_id)
    assert [s.method for s in fake.sent][-2:] == ["GET", "PATCH"]


@pytest.mark.parametrize(
    "file_id", ["", "../permissions", "abc/permissions", "abc?alt=media", "a b", "файл"]
)
def test_bad_file_id_refused_without_request(file_id: str) -> None:
    """Id вклеивается в адрес: «abc/permissions» превратил бы корзину в запрос
    к доступам. Такой id отвергается до сети."""
    fake = FakeDrive()
    client = _client(fake)

    for call in (
        lambda: client.download(file_id, max_bytes=100),
        lambda: client.trash(file_id),
        lambda: client.metadata(file_id),
        lambda: client.permissions(file_id),
    ):
        with pytest.raises(ValueError, match="идентификатор файла Drive"):
            call()

    assert fake.sent == []


# ---------------------------------------------------------------------------
# Папка
# ---------------------------------------------------------------------------
def test_folder_metadata_and_permissions() -> None:
    fake = FakeDrive()
    fake.folder.permissions.extend(
        {"id": f"p-{n}", "type": "user", "role": "reader", "emailAddress": f"{n}@example.com"}
        for n in range(150)
    )
    client = _client(fake)

    folder = client.metadata(FOLDER_ID)
    permissions = client.permissions(FOLDER_ID)

    assert folder["mimeType"] == "application/vnd.google-apps.folder"
    assert folder["driveId"] == "shared-drive-1"
    assert folder["capabilities"] == {"canAddChildren": True, "canEdit": True, "canTrash": True}
    assert len(permissions) == 152
    assert {p.get("emailAddress") for p in permissions} >= {ROBOT, CHEF}


def test_folder_is_closed() -> None:
    """Закрыта — ни «всем, у кого есть ссылка», ни «всем в домене»."""
    people = [
        {"type": "user", "role": "fileOrganizer", "emailAddress": ROBOT},
        {"type": "group", "role": "writer", "emailAddress": "kitchen@example.com"},
    ]

    assert folder_is_closed(people) is True
    assert folder_is_closed([*people, {"type": "anyone", "role": "reader"}]) is False
    assert folder_is_closed([*people, {"type": "domain", "role": "reader"}]) is False


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
def test_drive_scope_is_narrow_by_default_and_checked() -> None:
    """По умолчанию — узкий ``drive.file``; опечатка ловится при старте."""
    assert Settings(_env_file=None).drive_scope == "drive.file"
    assert Settings(_env_file=None, drive_scope="drive").drive_scope == "drive"
    with pytest.raises(ValidationError, match="drive_scope"):
        Settings(_env_file=None, drive_scope="drive.readonly")


def test_folder_setting_is_an_id_not_a_link() -> None:
    """Ссылку на папку вместо id ловит старт, понятным текстом — а не
    трассировка при первой загрузке фото."""
    link = "https://drive.google.com/drive/folders/abc_DEF-123?usp=sharing"

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, drive_cards_folder_id=link)

    assert "DRIVE_CARDS_FOLDER_ID" in str(caught.value)
    assert "после /folders/" in str(caught.value)
    assert "usp=sharing" not in str(caught.value)
    assert Settings(_env_file=None, drive_cards_folder_id="abc_DEF-123").drive_cards_folder_id
    assert Settings(_env_file=None, drive_cards_folder_id="").drive_cards_folder_id == ""


def test_client_from_settings_uses_configured_scope_and_timeouts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeDrive()
    calls: list[tuple[object, ...]] = []

    def connect(path: Path, scope: str) -> requests.Session:
        calls.append((path, scope))
        return fake.session()

    monkeypatch.setattr("kitchen.sync.drive.authorized_session", connect)
    settings = Settings(
        _env_file=None,
        google_credentials_path=tmp_path / "key.json",
        drive_cards_folder_id=FOLDER_ID,
        drive_scope="drive",
        google_connect_timeout=3,
        google_read_timeout=33,
        google_refresh_timeout=13,
    )

    client = drive_from_settings(settings)
    assert calls == []
    file_id = _upload(client)
    client.trash(file_id)

    assert calls == [(tmp_path / "key.json", "https://www.googleapis.com/auth/drive")]
    assert [s.timeout for s in fake.sent] == [(3, 33)] * 3
