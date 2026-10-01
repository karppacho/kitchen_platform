"""Черновики карточек и фото: ручки ``/api/cards/*`` на настоящей базе.

Повар заводит карточку ингредиента мастером на телефоне. Черновик живёт на
сервере — один активный на повара: телефон уснул, браузер закрылся, а
работа на месте. Фото лежат в папке фото на Google Drive (в бою — папка
бота на «Моём диске», «все со ссылкой — читатель»; ручкам место и доступ
папки не важны). Drive здесь — фальшивка на уровне транспорта
(``tests/fake_drive.py``), код шлёт ей те же запросы, что ушли бы в Google.

Главное, что стерегут эти тесты:

* черновик видит и меняет только его владелец — чужой отвечает 404, как
  несуществующий;
* id файла Drive берётся только из нашей загрузки, ни одна ручка не
  принимает его от клиента;
* сначала Drive, потом база: сбой Drive не оставляет в черновике следов;
* корзина не роняет ответ, а фото, ссылка на которое могла уйти в лист,
  в корзину не отправляется вовсе.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

from kitchen.cards.drafts import PROXY_LIMIT
from kitchen.config import Settings
from kitchen.db import models
from kitchen.domain.cards import (
    DEFAULT_CATEGORIES,
    TEXT_LIMITS,
    check_nutrients,
    label_fields_from_extraction,
)
from kitchen.sync.drive import DriveClient
from kitchen.web import auth, cards
from kitchen.web.app import create_app
from tests.fake_drive import FOLDER_ID, FakeDrive
from tests.integration.test_database import _url

if TYPE_CHECKING:
    import httpx
    import requests
    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

COOK = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")
NOBODY = "33333333-3333-3333-3333-333333333333"
CSRF = {"X-Kitchen-Csrf": "1"}

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + bytes(200) + b"\xff\xd9"
JPEG_2 = b"\xff\xd8\xff\xe1" + b"\x01" * 300 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(200)
NINE_MB = b"\xff\xd8\xff\xe0" + bytes(9 * 1024 * 1024) + b"\xff\xd9"
NO_END = b"\xff\xd8\xff\xe0" + bytes(200)

STORAGE_MISSING = "Хранилище фото не настроено — сообщите администратору"
STORAGE_DOWN = "Хранилище фото недоступно — попробуйте позже"
STORAGE_BROKEN = "Хранилище фото недоступно — сообщите администратору"

ENDPOINTS = [
    ("GET", "/api/cards/options"),
    ("GET", "/api/cards/name-check?name=Соус"),
    ("GET", "/api/cards/drafts/current"),
    ("POST", "/api/cards/drafts"),
    ("PATCH", f"/api/cards/drafts/{NOBODY}"),
    ("DELETE", f"/api/cards/drafts/{NOBODY}"),
    ("PUT", f"/api/cards/drafts/{NOBODY}/photos/label"),
    ("DELETE", f"/api/cards/drafts/{NOBODY}/photos/label"),
    ("GET", f"/api/cards/drafts/{NOBODY}/photos/label"),
]


# ---------------------------------------------------------------------------
# Окружение
# ---------------------------------------------------------------------------
@pytest.fixture
def drive() -> FakeDrive:
    return FakeDrive()


@pytest.fixture
def people(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        session.add(models.Profile(id=COOK, email="cook@example.com", display_name="Повар"))
        session.add(models.Profile(id=OTHER, email="cook2@example.com", display_name="Повар 2"))


def make_client(
    drive: FakeDrive,
    user: uuid.UUID | None = COOK,
    roles: tuple[str, ...] = ("cook",),
    folder: str = FOLDER_ID,
) -> TestClient:
    """Приложение с фальшивым Drive; ``user=None`` — никто не вошёл."""
    app = create_app(Settings(app_env="test", database_url=_url(), drive_cards_folder_id=folder))
    app.dependency_overrides[cards.get_drive] = lambda: DriveClient(
        drive.session, folder_id=folder, timeout=(5, 30)
    )
    if user is not None:
        app.dependency_overrides[auth.current_user] = lambda: auth.CurrentUser(
            id=user, email="cook@example.com", display_name="Повар", roles=frozenset(roles)
        )
    return TestClient(app)


@pytest.fixture
def cook(people: None, drive: FakeDrive) -> TestClient:
    return make_client(drive)


def start(client: TestClient) -> dict[str, object]:
    reply = client.post("/api/cards/drafts", headers=CSRF)
    assert reply.status_code == 201, reply.text
    body: dict[str, object] = reply.json()
    return body


def patch(client: TestClient, draft_id: object, **fields: object) -> httpx.Response:
    return client.patch(f"/api/cards/drafts/{draft_id}", json=fields, headers=CSRF)


def named(client: TestClient) -> dict[str, object]:
    """Черновик с поставщиком и названием — по ним называется файл фото."""
    draft = start(client)
    assert patch(client, draft["id"], supplier="Метро", name="Соус Барбекю").status_code == 200
    return draft


def photo_url(draft_id: object, kind: str = "label") -> str:
    return f"/api/cards/drafts/{draft_id}/photos/{kind}"


def put_photo(
    client: TestClient, draft_id: object, kind: str = "label", content: bytes = JPEG
) -> httpx.Response:
    return client.put(
        photo_url(draft_id, kind),
        files={"photo": ("photo.jpg", content, "image/jpeg")},
        headers=CSRF,
    )


def current(client: TestClient) -> dict[str, object] | None:
    reply = client.get("/api/cards/drafts/current")
    assert reply.status_code == 200
    body: dict[str, object] | None = reply.json()
    return body


def row(sessions: sessionmaker[Session], draft_id: object) -> dict[str, object]:
    """Строка черновика в базе целиком — все колонки."""
    with sessions() as session:
        draft = session.get(models.CardDraft, uuid.UUID(str(draft_id)))
        assert draft is not None
        return {c.key: getattr(draft, c.key) for c in sa_inspect(models.CardDraft).columns}


def uploaded(drive: FakeDrive) -> list[str]:
    """Файлы, которые платформа положила в Drive, — в порядке загрузки."""
    return [file.id for file in drive.files.values() if file.own]


def trashed(drive: FakeDrive) -> list[str]:
    return [file.id for file in drive.files.values() if file.trashed]


def orphan_logs(caplog: pytest.LogCaptureFixture, file_id: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and file_id in record.getMessage()
    ]


# ---------------------------------------------------------------------------
# Схема
# ---------------------------------------------------------------------------
def test_card_drafts_schema(sessions: sessionmaker[Session]) -> None:
    """У каждого внешнего ключа — индекс; активный черновик — один на повара;
    КБЖУ — Numeric(12,3), как у карточек."""
    with sessions() as session:
        inspector = sa_inspect(session.get_bind())
        foreign = {
            tuple(key["constrained_columns"]): key
            for key in inspector.get_foreign_keys("card_drafts")
        }
        indexes = {index["name"]: index for index in inspector.get_indexes("card_drafts")}
        columns = {column["name"]: column for column in inspector.get_columns("card_drafts")}

    owner, write = foreign[("owner_id",)], foreign[("sheet_write_id",)]
    assert (owner["referred_table"], owner["options"].get("ondelete")) == ("profiles", "CASCADE")
    assert (write["referred_table"], write["options"].get("ondelete")) == (
        "sheet_writes",
        "SET NULL",
    )
    indexed = {tuple(index["column_names"]) for index in indexes.values()}
    assert {("owner_id",), ("sheet_write_id",)} <= indexed
    active = indexes["ux_card_drafts_active_owner"]
    assert active["unique"]
    assert "status" in str(active["dialect_options"]["postgresql_where"])
    for name in ("protein", "fat", "carbs", "kcal"):
        assert (columns[name]["type"].precision, columns[name]["type"].scale) == (12, 3)


def test_deleted_cook_takes_his_drafts(sessions: sessionmaker[Session], cook: TestClient) -> None:
    start(cook)

    with sessions.begin() as session:
        session.delete(session.get(models.Profile, COOK))

    with sessions() as session:
        assert session.scalars(select(models.CardDraft)).all() == []


# ---------------------------------------------------------------------------
# Вход и роли
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_without_login_gives_401(people: None, drive: FakeDrive, method: str, path: str) -> None:
    reply = make_client(drive, user=None).request(method, path, headers=CSRF)

    assert reply.status_code == 401


@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
def test_commerce_gets_403(people: None, drive: FakeDrive, method: str, path: str) -> None:
    """Карточки заводят повара; коммерсу этот раздел не нужен."""
    reply = make_client(drive, roles=("commerce",)).request(method, path, headers=CSRF)

    assert reply.status_code == 403
    assert drive.sent == []


@pytest.mark.parametrize("role", ["cook", "chef", "developer"])
def test_card_roles_may_start_a_draft(people: None, drive: FakeDrive, role: str) -> None:
    client = make_client(drive, roles=(role,))

    assert start(client)["step"] == "supplier"


@pytest.mark.parametrize("role", ["chef", "developer"])
def test_chef_and_developer_may_edit_and_handle_photos(
    people: None, drive: FakeDrive, role: str
) -> None:
    """Шеф и разработчик заводят карточки наравне с поваром — не только
    «Начать», но и правка, и фото."""
    client = make_client(drive, roles=(role,))
    draft = start(client)

    edited = patch(client, draft["id"], supplier="Метро", name="Соус Барбекю")
    uploaded_reply = put_photo(client, draft["id"])
    shown = client.get(photo_url(draft["id"]))
    removed = client.delete(photo_url(draft["id"]), headers=CSRF)

    assert [edited.status_code, uploaded_reply.status_code, shown.status_code] == [200, 200, 200]
    assert shown.content == JPEG
    assert removed.status_code == 200
    assert removed.json()["photos"]["label"] is False


def test_changing_request_without_csrf_header_is_403(
    sessions: sessionmaker[Session], people: None, drive: FakeDrive
) -> None:
    """Ручки карточек — за общей защитой от подделки: с кукой и без
    заголовка запрос до ручки не доходит."""
    client = make_client(drive)
    client.cookies.set(auth.ACCESS_COOKIE, "token-from-browser")

    reply = client.post("/api/cards/drafts")

    assert reply.status_code == 403
    with sessions() as session:
        assert session.scalars(select(models.CardDraft)).all() == []


# ---------------------------------------------------------------------------
# Черновик: начать, продолжить, чужой
# ---------------------------------------------------------------------------
def test_no_draft_yet_is_null(cook: TestClient) -> None:
    assert current(cook) is None


def test_new_draft_is_empty(cook: TestClient) -> None:
    draft = start(cook)

    assert draft["status"] == "active"
    assert draft["step"] == "supplier"
    assert draft["photos"] == {"label": False, "package": False, "before": False, "after": False}
    assert draft["warnings"] == []
    assert draft["missing"] == [
        "Поставщик",
        "Категория",
        "Название",
        "Фото этикетки",
        "Согласован ли продукт",
    ]
    assert current(cook) == draft


def test_second_start_gives_409(cook: TestClient) -> None:
    """Один активный черновик на повара: второй «Начать» не теряет первый."""
    first = start(cook)

    reply = cook.post("/api/cards/drafts", headers=CSRF)

    assert reply.status_code == 409
    assert "незаконченная карточка" in reply.json()["detail"]
    assert current(cook)["id"] == first["id"]  # type: ignore[index]


def test_drafts_of_two_cooks_live_side_by_side(cook: TestClient, drive: FakeDrive) -> None:
    other = make_client(drive, user=OTHER)

    mine, theirs = start(cook), start(other)

    assert mine["id"] != theirs["id"]
    assert current(other)["id"] == theirs["id"]  # type: ignore[index]


def test_foreign_draft_is_404(cook: TestClient, drive: FakeDrive) -> None:
    """Чужой черновик — как несуществующий: 404, а не 403, чтобы не
    подтверждать, что он есть."""
    mine = named(cook)
    assert put_photo(cook, mine["id"]).status_code == 200
    other = make_client(drive, user=OTHER)
    draft_id = mine["id"]

    replies = [
        patch(other, draft_id, name="Чужое"),
        other.delete(f"/api/cards/drafts/{draft_id}", headers=CSRF),
        put_photo(other, draft_id, content=JPEG_2),
        other.delete(photo_url(draft_id), headers=CSRF),
        other.get(photo_url(draft_id)),
    ]

    assert [reply.status_code for reply in replies] == [404] * 5
    assert current(other) is None
    after = current(cook)
    assert after is not None
    assert (after["name"], after["status"], after["photos"]) == (
        "Соус Барбекю",
        "active",
        {"label": True, "package": False, "before": False, "after": False},
    )
    assert len(uploaded(drive)) == 1
    assert trashed(drive) == []


def test_unknown_draft_is_404(cook: TestClient) -> None:
    assert patch(cook, NOBODY, name="Соус").status_code == 404


# ---------------------------------------------------------------------------
# Правка (PATCH)
# ---------------------------------------------------------------------------
def test_patch_parses_decimal_comma(cook: TestClient) -> None:
    draft = start(cook)

    reply = patch(cook, draft["id"], protein="12,5", kcal="250 ккал / 1046 кДж")

    assert reply.status_code == 200
    assert (reply.json()["protein"], reply.json()["kcal"]) == ("12.5", "250")
    assert current(cook)["protein"] == "12.5"  # type: ignore[index]


def test_patch_unclear_number_is_422_and_changes_nothing(cook: TestClient) -> None:
    draft = start(cook)
    patch(cook, draft["id"], protein="10")

    reply = patch(cook, draft["id"], name="Соус", protein="abc")

    assert reply.status_code == 422
    assert reply.json() == {"detail": "Белки: «abc» — не число", "field": "protein"}
    after = current(cook)
    assert after is not None
    assert (after["protein"], after["name"]) == ("10", "")


def test_patch_long_text_is_422_not_cut(cook: TestClient) -> None:
    """Молча обрезанный состав — потерянная работа повара: отказ с пределом."""
    limit = TEXT_LIMITS["composition"]
    draft = start(cook)

    reply = patch(cook, draft["id"], composition="а" * (limit + 1))

    assert reply.status_code == 422
    assert reply.json() == {
        "detail": f"Состав: не больше {limit} знаков",
        "field": "composition",
    }
    assert current(cook)["composition"] == ""  # type: ignore[index]
    assert patch(cook, draft["id"], composition="а" * limit).json()["composition"] == "а" * limit


def test_patch_huge_nutrient_is_refused_quickly(cook: TestClient) -> None:
    """Строка в 100 000 знаков до разбора ккал не доходит: он квадратичен."""
    draft = start(cook)

    started = time.monotonic()
    reply = patch(cook, draft["id"], kcal="ккал " + "1" * 100_000)

    assert time.monotonic() - started < 2
    assert reply.status_code == 422
    assert reply.json()["field"] == "kcal"


def test_patch_unknown_step_is_422(cook: TestClient) -> None:
    draft = start(cook)

    reply = patch(cook, draft["id"], step="teleport")

    assert reply.status_code == 422
    assert reply.json()["field"] == "step"
    assert current(cook)["step"] == "supplier"  # type: ignore[index]
    assert patch(cook, draft["id"], step="label").json()["step"] == "label"


def test_patch_is_partial_and_cleans_text(cook: TestClient) -> None:
    draft = start(cook)
    patch(cook, draft["id"], supplier="Метро")

    reply = patch(cook, draft["id"], name="  Соус​  Барбекю ", approval="Отбракован")

    body = reply.json()
    assert (body["supplier"], body["name"], body["approval"]) == (
        "Метро",
        "Соус Барбекю",
        "Отбракован",
    )
    assert body["missing"] == ["Категория", "Фото этикетки"]
    assert patch(cook, draft["id"], approval=None).json()["approval"] is None


def test_patch_number_instead_of_string_is_refused(cook: TestClient) -> None:
    """Числа — строками, как во всём API: 12.5 из JSON пришло бы двоичной дробью."""
    draft = start(cook)

    assert patch(cook, draft["id"], protein=12.5).status_code == 422
    assert current(cook)["protein"] is None  # type: ignore[index]


def test_manual_edit_renews_nutrient_warnings(cook: TestClient) -> None:
    draft = start(cook)

    wrong = patch(cook, draft["id"], protein="120").json()["warnings"]
    fixed = patch(cook, draft["id"], protein="12").json()["warnings"]

    assert wrong == list(check_nutrients(Decimal("120"), None, None, None))
    assert fixed == []


def test_label_notes_survive_manual_edit_without_duplicates(
    cook: TestClient, sessions: sessionmaker[Session]
) -> None:
    """Распознавание кладёт в черновик числа и замечания самой этикетки —
    без проверки КБЖУ: её черновик считает при каждой выдаче по своим числам.
    Правка повара меняет только проверку: замечания этикетки остаются,
    проверка не дублируется и в базе не хранится."""
    draft = start(cook)
    values, notes = label_fields_from_extraction(
        {
            "proteins": "120",
            "fats": "<0,5",
            "carbohydrates": "10",
            "kcal": "300",
            "nutrition_basis": "на 100 г",
        }
    )
    recognized = check_nutrients(Decimal("120"), None, Decimal("10"), Decimal("300"))
    label_notes = [note for note in notes if note not in recognized]
    assert label_notes, "у этикетки есть свои замечания — про жиры"
    assert recognized, "и проверка чисел нашла белки больше 100 г"
    with sessions.begin() as session:
        record = session.get(models.CardDraft, uuid.UUID(str(draft["id"])))
        assert record is not None
        for field in ("protein", "fat", "carbs", "kcal"):
            setattr(record, field, values[field])
        record.recognition_warnings = label_notes

    shown = current(cook)["warnings"]  # type: ignore[index]
    after_protein = patch(cook, draft["id"], protein="12").json()["warnings"]
    again = patch(cook, draft["id"], protein="12").json()["warnings"]
    after_fat = patch(cook, draft["id"], fat="0,5").json()["warnings"]
    after_kcal = patch(cook, draft["id"], kcal="93").json()["warnings"]

    mismatch = check_nutrients(Decimal("12"), Decimal("0.5"), Decimal("10"), Decimal("300"))
    assert mismatch, "по Б, Ж, У выходит около 93 ккал, а указано 300"
    assert shown == label_notes + list(recognized)
    assert after_protein == again == label_notes
    assert after_fat == label_notes + list(mismatch)
    assert after_kcal == label_notes
    assert row(sessions, draft["id"])["recognition_warnings"] == label_notes, (
        "проверка чисел в базе не хранится"
    )


# ---------------------------------------------------------------------------
# Фото: загрузка
# ---------------------------------------------------------------------------
def test_photo_goes_to_the_closed_folder_named_like_the_bot(
    cook: TestClient, drive: FakeDrive
) -> None:
    draft = named(cook)

    reply = put_photo(cook, draft["id"])

    assert reply.status_code == 200
    assert reply.json()["photos"]["label"] is True
    [file_id] = uploaded(drive)
    file = drive.files[file_id]
    assert file.parents == [FOLDER_ID]
    assert file.content == JPEG
    assert file.name.startswith("Метро_Соус_Барбекю_этикетка_")
    assert file.name.endswith(".jpg")
    assert file.app_properties == {"cardDraft": str(draft["id"]), "photoKind": "label"}
    assert drive.permission_writes() == []
    assert file_id not in reply.text, "id файла клиенту не нужен — фото идут через прокси"


@pytest.mark.parametrize(
    ("content", "status"),
    [(PNG, 415), (NINE_MB, 413), (NO_END, 415), (b"", 415)],
    ids=["png", "9mb", "jpeg-without-ffd9", "empty"],
)
def test_bad_photo_is_refused_before_drive(
    cook: TestClient, drive: FakeDrive, sessions: sessionmaker[Session], content: bytes, status: int
) -> None:
    draft = named(cook)
    before = row(sessions, draft["id"])

    reply = put_photo(cook, draft["id"], content=content)

    assert reply.status_code == status
    assert drive.sent == []
    assert row(sessions, draft["id"]) == before


def test_photo_needs_supplier_and_name(cook: TestClient, drive: FakeDrive) -> None:
    """По поставщику и названию называется файл — шеф ищет фото в папке глазами."""
    draft = start(cook)

    reply = put_photo(cook, draft["id"])

    assert reply.status_code == 409
    assert "поставщика и название" in reply.json()["detail"]
    assert drive.sent == []


def test_storage_not_configured_is_503_before_drive(people: None, drive: FakeDrive) -> None:
    client = make_client(drive, folder="")
    draft = named(client)

    reply = put_photo(client, draft["id"])

    assert reply.status_code == 503
    assert reply.json() == {"detail": STORAGE_MISSING}
    assert drive.sent == []


@pytest.mark.parametrize(
    ("status", "reason", "text", "logged"),
    [
        (500, "backendError", STORAGE_DOWN, "Google Drive сейчас не отвечает"),
        (403, "storageQuotaExceeded", STORAGE_BROKEN, "нет места для фото"),
    ],
    ids=["drive-down", "quota"],
)
def test_drive_failure_changes_nothing(
    cook: TestClient,
    drive: FakeDrive,
    sessions: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    status: int,
    reason: str,
    text: str,
    logged: str,
) -> None:
    """Сначала Drive, потом база: загрузка не удалась — прежнее фото на месте.
    Повару — что делать, подробности Google — в лог."""
    draft = named(cook)
    assert put_photo(cook, draft["id"]).status_code == 200
    before = row(sessions, draft["id"])
    drive.fail_next(status, reason, method="POST")

    with caplog.at_level(logging.WARNING):
        reply = put_photo(cook, draft["id"], content=JPEG_2)

    assert reply.status_code == 502
    assert reply.json() == {"detail": text}
    assert "Service Accounts" not in reply.text, "сырой ответ Google повару не показываем"
    assert any(logged in record.getMessage() for record in caplog.records)
    assert row(sessions, draft["id"]) == before
    assert trashed(drive) == []
    assert cook.get(photo_url(draft["id"])).content == JPEG


def test_unreadable_google_key_is_told_neutrally(
    people: None,
    drive: FakeDrive,
    sessions: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Ключ сервисного аккаунта не прочитан: повару — нейтральный текст, а имя
    переменной окружения и путь — только администратору, в лог."""
    client = make_client(drive)
    draft = named(client)

    def no_key() -> requests.Session:
        raise FileNotFoundError("service_account.json")

    client.app.dependency_overrides[cards.get_drive] = lambda: DriveClient(  # type: ignore[attr-defined]
        no_key, folder_id=FOLDER_ID, timeout=(5, 30)
    )

    with caplog.at_level(logging.WARNING):
        reply = put_photo(client, draft["id"])

    assert reply.status_code == 502
    assert reply.json() == {"detail": STORAGE_BROKEN}
    assert any("GOOGLE_CREDENTIALS_PATH" in record.getMessage() for record in caplog.records)
    assert row(sessions, draft["id"])["label_file_id"] is None


def test_unknown_photo_kind_is_422(cook: TestClient, drive: FakeDrive) -> None:
    draft = named(cook)

    assert put_photo(cook, draft["id"], kind="selfie").status_code == 422
    assert drive.sent == []


# ---------------------------------------------------------------------------
# Фото: замена, удаление, корзина
# ---------------------------------------------------------------------------
def test_replacing_photo_trashes_the_previous(cook: TestClient, drive: FakeDrive) -> None:
    draft = named(cook)
    put_photo(cook, draft["id"])
    [first] = uploaded(drive)

    reply = put_photo(cook, draft["id"], content=JPEG_2)

    assert reply.status_code == 200
    assert trashed(drive) == [first]
    assert cook.get(photo_url(draft["id"])).content == JPEG_2


@pytest.mark.parametrize("trouble", ["not-in-folder", "gone", "drive-down"])
def test_trash_trouble_on_replace_is_not_fatal(
    cook: TestClient, drive: FakeDrive, caplog: pytest.LogCaptureFixture, trouble: str
) -> None:
    """Корзина — уборка, а не часть загрузки: отказ корзины не отнимает у
    повара новое фото. Сирота остаётся в папке фото без ссылок из листа и
    базы и пишется в лог."""
    draft = named(cook)
    put_photo(cook, draft["id"])
    [first] = uploaded(drive)
    if trouble == "not-in-folder":
        drive.files[first].parents = ["somewhere-else"]
    elif trouble == "gone":
        del drive.files[first]
    else:
        drive.fail_next(503, "backendError", method="PATCH")

    with caplog.at_level(logging.WARNING):
        reply = put_photo(cook, draft["id"], content=JPEG_2)

    assert reply.status_code == 200
    assert reply.json()["photos"]["label"] is True
    assert cook.get(photo_url(draft["id"])).content == JPEG_2
    if trouble == "gone":
        # Файла уже нет — корзина считает дело сделанным: сироты нет.
        assert orphan_logs(caplog, first) == []
    else:
        assert orphan_logs(caplog, first), "сирота должна остаться в журнале"
        assert first not in trashed(drive)


def test_removing_photo_trashes_it(cook: TestClient, drive: FakeDrive) -> None:
    draft = named(cook)
    put_photo(cook, draft["id"], kind="package")
    [file_id] = uploaded(drive)

    reply = cook.delete(photo_url(draft["id"], "package"), headers=CSRF)

    assert reply.status_code == 200
    assert reply.json()["photos"]["package"] is False
    assert trashed(drive) == [file_id]
    assert cook.get(photo_url(draft["id"], "package")).status_code == 404


def test_removing_empty_slot_is_harmless(cook: TestClient, drive: FakeDrive) -> None:
    draft = named(cook)

    reply = cook.delete(photo_url(draft["id"], "before"), headers=CSRF)

    assert reply.status_code == 200
    assert drive.sent == []


def test_cancel_during_upload_trashes_the_new_file(
    cook: TestClient, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """«Начать заново» на другой вкладке, пока фото летело в Drive: файл
    уже там, а черновика нет. Файл — в корзину, повару — 404.

    Заодно видно, что во время загрузки строка черновика не заперта:
    иначе отмена ждала бы загрузку, а тест — повис."""
    draft = named(cook)

    class CancelledMeanwhile(DriveClient):
        def upload_jpeg(
            self, content: bytes, *, name: str, app_properties: dict[str, str] | None = None
        ) -> str:
            file_id = super().upload_jpeg(content, name=name, app_properties=app_properties)
            with sessions.begin() as session:
                record = session.get(models.CardDraft, uuid.UUID(str(draft["id"])))
                assert record is not None
                record.status = "cancelled"
            return file_id

    cook.app.dependency_overrides[cards.get_drive] = lambda: CancelledMeanwhile(  # type: ignore[attr-defined]
        drive.session, folder_id=FOLDER_ID, timeout=(5, 30)
    )

    reply = put_photo(cook, draft["id"])

    assert reply.status_code == 404
    assert len(uploaded(drive)) == 1
    assert trashed(drive) == uploaded(drive)
    assert row(sessions, draft["id"])["label_file_id"] is None


def test_cancel_without_photos_does_not_touch_drive(cook: TestClient, drive: FakeDrive) -> None:
    draft = start(cook)

    assert cook.delete(f"/api/cards/drafts/{draft['id']}", headers=CSRF).status_code == 204
    assert drive.sent == []


def test_cancel_trashes_every_photo(
    cook: TestClient, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """«Начать заново»: черновик отменён, его фото — в корзине Drive."""
    draft = named(cook)
    for kind in ("label", "package", "after"):
        assert put_photo(cook, draft["id"], kind=kind).status_code == 200

    reply = cook.delete(f"/api/cards/drafts/{draft['id']}", headers=CSRF)

    assert reply.status_code == 204
    assert sorted(trashed(drive)) == sorted(uploaded(drive))
    assert len(trashed(drive)) == 3
    assert row(sessions, draft["id"])["status"] == "cancelled"
    assert current(cook) is None
    assert start(cook)["id"] != draft["id"]


def test_cancel_survives_drive_trouble(
    cook: TestClient,
    drive: FakeDrive,
    sessions: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    draft = named(cook)
    put_photo(cook, draft["id"])
    [file_id] = uploaded(drive)
    drive.fail_next(503, "backendError")

    with caplog.at_level(logging.WARNING):
        reply = cook.delete(f"/api/cards/drafts/{draft['id']}", headers=CSRF)

    assert reply.status_code == 204
    assert row(sessions, draft["id"])["status"] == "cancelled"
    assert orphan_logs(caplog, file_id)


def test_cancel_after_sheet_write_keeps_photos(
    cook: TestClient,
    drive: FakeDrive,
    sessions: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Попытка записи в лист была (любой исход) — строка могла лечь, и ссылки
    P, S–U у шефа должны открываться. Фото не трогаем, только пишем в лог."""
    draft = named(cook)
    put_photo(cook, draft["id"])
    [file_id] = uploaded(drive)
    with sessions.begin() as session:
        session.add(
            models.SheetWrite(
                book="ingredient_cards",
                sheet="Лист1",
                row=7,
                action="append",
                status="failed",
                request_key=f"card-draft:{draft['id']}",
                before={},
                values={},
            )
        )
    sent = len(drive.sent)

    with caplog.at_level(logging.WARNING):
        reply = cook.delete(f"/api/cards/drafts/{draft['id']}", headers=CSRF)

    assert reply.status_code == 204
    assert drive.sent[sent:] == [], "ни одного запроса к корзине"
    assert trashed(drive) == []
    assert row(sessions, draft["id"])["status"] == "cancelled"
    assert orphan_logs(caplog, file_id)


@pytest.mark.parametrize("action", ["replace", "remove"])
def test_photo_after_sheet_write_is_not_trashed(
    cook: TestClient,
    drive: FakeDrive,
    sessions: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
    action: str,
) -> None:
    """То же правило, что у «Начать заново»: строка с прежней ссылкой могла
    лечь в лист — прежнее фото остаётся в папке, слот меняется."""
    draft = named(cook)
    put_photo(cook, draft["id"])
    [first] = uploaded(drive)
    with sessions.begin() as session:
        session.add(
            models.SheetWrite(
                book="ingredient_cards",
                sheet="Лист1",
                row=7,
                action="append",
                status="pending",
                request_key=f"card-draft:{draft['id']}",
                before={},
                values={},
            )
        )

    with caplog.at_level(logging.WARNING):
        if action == "replace":
            reply = put_photo(cook, draft["id"], content=JPEG_2)
        else:
            reply = cook.delete(photo_url(draft["id"]), headers=CSRF)

    assert reply.status_code == 200
    assert reply.json()["photos"]["label"] is (action == "replace")
    assert trashed(drive) == []
    assert orphan_logs(caplog, first)


# ---------------------------------------------------------------------------
# Прокси фото
# ---------------------------------------------------------------------------
def test_proxy_serves_photo_privately(cook: TestClient) -> None:
    draft = named(cook)
    put_photo(cook, draft["id"])

    reply = cook.get(photo_url(draft["id"]))

    assert reply.status_code == 200
    assert reply.content == JPEG
    assert reply.headers["content-type"] == "image/jpeg"
    assert "private" in reply.headers["cache-control"]
    assert reply.headers["x-content-type-options"] == "nosniff"


def test_proxy_of_empty_slot_is_404(cook: TestClient, drive: FakeDrive) -> None:
    draft = named(cook)

    assert cook.get(photo_url(draft["id"], "before")).status_code == 404
    assert drive.sent == []


def test_proxy_refuses_oversized_file(
    cook: TestClient, drive: FakeDrive, caplog: pytest.LogCaptureFixture
) -> None:
    """Файл в папке подменили огромным: отказ и лог, а не обрезанная картинка."""
    draft = named(cook)
    put_photo(cook, draft["id"])
    [file_id] = uploaded(drive)
    drive.files[file_id].content = b"\xff\xd8\xff" + bytes(PROXY_LIMIT)

    with caplog.at_level(logging.WARNING):
        reply = cook.get(photo_url(draft["id"]))

    assert reply.status_code == 502
    assert orphan_logs(caplog, file_id)
    assert drive.served <= PROXY_LIMIT + 64 * 1024


def test_proxy_when_drive_lost_the_file(
    cook: TestClient, drive: FakeDrive, caplog: pytest.LogCaptureFixture
) -> None:
    """Файл удалили из папки руками: понятный текст, а не битая картинка молча."""
    draft = named(cook)
    put_photo(cook, draft["id"])
    [file_id] = uploaded(drive)
    del drive.files[file_id]

    with caplog.at_level(logging.WARNING):
        reply = cook.get(photo_url(draft["id"]))

    assert reply.status_code == 502
    assert reply.json() == {"detail": "Фото пропало из хранилища — сфотографируйте ещё раз"}
    assert orphan_logs(caplog, file_id)
    assert any("Не найдено в Google Drive" in line for line in orphan_logs(caplog, file_id))


# ---------------------------------------------------------------------------
# id файла — только из нашей загрузки
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slot", ["label_file_id", "package_file_id", "before_file_id", "after_file_id"]
)
def test_patch_does_not_take_file_id(
    cook: TestClient, sessions: sessionmaker[Session], slot: str
) -> None:
    draft = start(cook)

    reply = patch(cook, draft["id"], **{slot: "someone-elses-file"})

    assert reply.status_code == 422
    assert row(sessions, draft["id"])[slot] is None


def test_start_and_upload_ignore_file_id_from_client(
    cook: TestClient, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    reply = cook.post(
        "/api/cards/drafts", json={"label_file_id": "someone-elses-file"}, headers=CSRF
    )
    assert reply.status_code == 201
    draft_id = reply.json()["id"]
    assert row(sessions, draft_id)["label_file_id"] is None

    patch(cook, draft_id, supplier="Метро", name="Соус Барбекю")
    cook.put(
        photo_url(draft_id),
        files={"photo": ("photo.jpg", JPEG, "image/jpeg")},
        data={"file_id": "someone-elses-file"},
        headers=CSRF,
    )

    assert row(sessions, draft_id)["label_file_id"] == uploaded(drive)[0]


# ---------------------------------------------------------------------------
# Подсказки: категории, поставщики, похожие названия
# ---------------------------------------------------------------------------
def _cards(sessions: sessionmaker[Session], *cards: tuple[str, str, str, bool]) -> None:
    """Карточки в базе: название, категория, поставщик, убрана ли из листа."""
    with sessions.begin() as session:
        for name, category, supplier, removed in cards:
            session.add(
                models.IngredientCard(
                    name=name,
                    category=category,
                    supplier=supplier,
                    removed_at=datetime(2026, 9, 1, tzinfo=UTC) if removed else None,
                )
            )


def test_options_merge_seed_and_cards_by_frequency(
    cook: TestClient, sessions: sessionmaker[Session]
) -> None:
    _cards(
        sessions,
        ("Соус Барбекю", "Соусы", "Метро", False),
        ("Соус Сырный", "Соусы", "Метро", False),
        ("Соус Чесночный", "соусы ", "Ашан", False),
        ("Креветки", "Морепродукты", "Ашан", False),
        ("Мидии", "Морепродукты", "", False),
        ("Говядина", "Мясо", "Метро", False),
        ("Моцарелла", "сыры", "Метро", False),
        ("Что-то", "", "", False),
        # Убрана из листа — в подсказки не идёт.
        ("Гречка", "Крупы", "Лента", True),
    )

    reply = cook.get("/api/cards/options")

    assert reply.status_code == 200
    categories = reply.json()["categories"]
    assert categories[:4] == ["Соусы", "Морепродукты", "Мясо", "Сыры"]
    assert categories[4:] == [c for c in DEFAULT_CATEGORIES if c not in {"Соусы", "Мясо", "Сыры"}]
    assert reply.json()["suppliers"] == ["Метро", "Ашан"]


def test_name_check_tells_exact_similar_hidden_and_reference(
    cook: TestClient, sessions: sessionmaker[Session]
) -> None:
    _cards(
        sessions,
        ("Соус Барбекю", "Соусы", "Метро", False),
        ("Соус Сырный", "Соусы", "Ашан", False),
        ("Сыр Моцарелла", "Сыры", "Метро", True),
    )
    with sessions.begin() as session:
        session.add(models.Ingredient(legacy_id="1", name="Соус барбекю", status="активное"))
        session.add(models.Ingredient(legacy_id="2", name="Сыр Моцарелла", status=""))
        session.add(models.Ingredient(legacy_id="3", name="Кетчуп", status="архив"))
        session.add(
            models.Ingredient(
                legacy_id="4",
                name="Горчица",
                status="",
                removed_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )

    def check(name: str) -> dict[str, dict[str, list[object]]]:
        reply = cook.get("/api/cards/name-check", params={"name": name})
        assert reply.status_code == 200
        body: dict[str, dict[str, list[object]]] = reply.json()
        return body

    exact = check("соус  барбекю")
    assert exact["cards"] == {
        "exact": [{"name": "Соус Барбекю", "supplier": "Метро"}],
        "similar": [],
    }
    assert exact["reference"] == {"exact": ["Соус барбекю"], "similar": []}

    typo = check("Соус барбекью")
    assert typo["cards"]["exact"] == []
    assert typo["cards"]["similar"] == [{"name": "Соус Барбекю", "supplier": "Метро"}]
    assert typo["reference"]["similar"] == ["Соус барбекю"]

    hidden = check("Сыр Моцарелла")
    assert hidden["cards"] == {"exact": [], "similar": []}
    assert hidden["hidden"]["exact"] == [{"name": "Сыр Моцарелла", "supplier": "Метро"}]
    assert hidden["reference"]["exact"] == ["Сыр Моцарелла"]

    assert check("Кетчуп")["reference"] == {"exact": [], "similar": []}
    assert check("Горчица")["reference"] == {"exact": [], "similar": []}


def test_name_check_gives_no_prices(cook: TestClient, sessions: sessionmaker[Session]) -> None:
    """Повару справочник не положен: только имена, без цен и id."""
    with sessions.begin() as session:
        session.add(
            models.Ingredient(legacy_id="1", name="Соус барбекю", price_per_kg=Decimal("123.45"))
        )

    reply = cook.get("/api/cards/name-check", params={"name": "Соус барбекю"})

    assert "123" not in reply.text
    assert reply.json()["reference"]["exact"] == ["Соус барбекю"]
