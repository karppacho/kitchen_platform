"""Отправка карточки в таблицу: ``POST /api/cards/drafts/{id}/submit`` на настоящей базе.

Лист — фальшивые таблицы (``tests/conftest.py``), писатель строки и журнал
записей — настоящие, на Postgres; Drive — фальшивка. После записи карточка
попадает в базу обычным переносом одной книги карточек.

Главное, что стерегут эти тесты:

* черновик становится отправленным только после удачной записи — любой
  отказ оставляет его активным, с понятным текстом повару;
* повтор отправки (сеть оборвалась, ответ потерялся) второй строки не даёт:
  он идёт через журнал по ключу черновика;
* перенос в базу после записи — не часть записи: упал — ответ всё равно
  удачный, с ``imported=false``;
* пока карточка отправляется, её фото не меняются и не уходят в корзину:
  ссылки на них уже летят в лист.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from kitchen.cards.recognize import STALE_AFTER
from kitchen.cards.submit import (
    IMPORT_GOOGLE_TIMEOUT,
    IMPORT_LOCK_WAIT,
    NOT_IMPORTED,
    UNCONFIRMED,
    submit_window,
)
from kitchen.config import Settings
from kitchen.db import drafts as draft_store
from kitchen.db import models
from kitchen.db.journal import FAILED, LAYOUT_UNCONFIRMED, VERIFIED, DbJournal
from kitchen.db.session import make_session_factory
from kitchen.domain.cards import drive_view_url
from kitchen.sync import ownership, specs
from kitchen.sync.cycle import SyncCycle
from kitchen.sync.drive import DriveClient
from kitchen.sync.importer import take_import_lock
from kitchen.sync.reader import SheetsReader
from kitchen.sync.writer import SHEET_WRITE_LOCK_KEY, CardSheetWriter, hold_limit
from kitchen.web import auth, cards
from kitchen.web.app import create_app
from tests.conftest import FakeSheetsClient, FakeSpreadsheet, FakeWorksheet
from tests.fake_drive import FOLDER_ID, FakeDrive
from tests.fake_sheets import IDS, cards_sheet, header, sheets_client
from tests.integration.test_cards_drafts_api import (
    CSRF,
    JPEG,
    JPEG_2,
    current,
    patch,
    photo_url,
    put_photo,
    start,
    trashed,
    uploaded,
)
from tests.integration.test_database import _url

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx
    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

COOK = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")
CARDS = IDS["ingredient_cards"]
WIDTH = len(specs.INGREDIENT_CARDS.columns)
P, Q, R, S, T, U, V = 15, 16, 17, 18, 19, 20, 21
"""Индексы колонок листа с нуля."""

FREE_ROW = 6
"""Первая свободная строка листа ``cards_sheet()``: шапка в две строки и три
карточки в строках 3–5."""

BUSY = "Таблица занята — попробуйте ещё раз через минуту"
DATABASE_DOWN = "Сервер временно не может записать — черновик сохранён"


# ---------------------------------------------------------------------------
# Окружение
# ---------------------------------------------------------------------------
class HookedSpreadsheet(FakeSpreadsheet):
    """Книга карточек, где в момент чтения листа целиком что-то происходит —
    один раз: так другой запрос успевает прийти посреди записи."""

    def __init__(self, sheets: dict[str, FakeWorksheet], meanwhile: Callable[[], None]) -> None:
        super().__init__(sheets)
        self._meanwhile: Callable[[], None] | None = meanwhile

    def values_batch_get(
        self, ranges: list[str], params: dict[str, str] | None = None
    ) -> dict[str, object]:
        if ranges == ["'Лист1'"] and self._meanwhile is not None:
            meanwhile, self._meanwhile = self._meanwhile, None
            meanwhile()
        return super().values_batch_get(ranges, params)


@pytest.fixture
def drive() -> FakeDrive:
    return FakeDrive()


@pytest.fixture
def sheets() -> FakeSheetsClient:
    return sheets_client()


@pytest.fixture
def people(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        session.add(models.Profile(id=COOK, email="cook@example.com", display_name="Повар"))
        session.add(models.Profile(id=OTHER, email="cook2@example.com", display_name="Повар 2"))


def settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": _url(),
        "drive_cards_folder_id": FOLDER_ID,
        "sheets_id_ingredient_cards": CARDS,
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def make_client(
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
    *,
    user: uuid.UUID | None = COOK,
    roles: tuple[str, ...] = ("cook",),
    config: Settings | None = None,
    writer: Callable[[], CardSheetWriter | None] | None = None,
    cycle: Callable[[], SyncCycle] | None = None,
) -> TestClient:
    """Приложение с фальшивыми Drive и таблицами.

    Подменяется только вход в Google — ``sheets`` вместо ``GspreadClient``;
    писателя, журнал и перенос в базу собирает само приложение, как в бою
    (короткое ожидание замка и таймауты переноса — его), и оно же закрывает
    вход после ответа. ``writer`` и ``cycle`` — подменить и их."""
    config = config or settings()
    app = create_app(config)
    app.dependency_overrides[cards.get_drive] = lambda: DriveClient(
        drive.session, folder_id=FOLDER_ID, timeout=(5, 30)
    )
    app.state.google = lambda: sheets
    if writer is not None:
        app.dependency_overrides[cards.get_card_writer] = writer
    if cycle is not None:
        app.dependency_overrides[cards.get_sync_cycle] = cycle
    if user is not None:
        app.dependency_overrides[auth.current_user] = lambda: auth.CurrentUser(
            id=user, email="cook@example.com", display_name="Повар", roles=frozenset(roles)
        )
    return TestClient(app)


@pytest.fixture
def cook(
    people: None, drive: FakeDrive, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> TestClient:
    return make_client(drive, sheets, sessions)


def ready(
    client: TestClient,
    *,
    approval: str = "Да",
    photos: tuple[str, ...] = ("label", "package", "before", "after"),
    **fields: str,
) -> dict[str, object]:
    """Черновик, готовый к отправке: поля, ответ «согласован?» и фото."""
    draft = start(client)
    values = {
        "supplier": "Метро",
        "category": "Соусы",
        "name": "Соус Барбекю",
        "composition": "томатная паста, сахар",
        "protein": "1,5",
        "fat": "12,5",
        "description": "Для бургеров",
        "approval": approval,
        **fields,
    }
    assert patch(client, draft["id"], **values).status_code == 200
    for kind in photos:
        assert put_photo(client, draft["id"], kind).status_code == 200
    return draft


def submit(client: TestClient, draft_id: object) -> httpx.Response:
    return client.post(f"/api/cards/drafts/{draft_id}/submit", headers=CSRF)


def book(sheets: FakeSheetsClient) -> FakeSpreadsheet:
    return sheets._spreadsheets[CARDS]


def hook(sheets: FakeSheetsClient, meanwhile: Callable[[], None]) -> None:
    """Книга карточек, где посреди записи происходит ``meanwhile``."""
    sheets._spreadsheets[CARDS] = HookedSpreadsheet(
        {"Лист1": book(sheets)._sheets["Лист1"]}, meanwhile
    )


def line(sheets: FakeSheetsClient, number: int) -> list[str]:
    cells = book(sheets)._sheets["Лист1"].get_all_values()
    got = [str(cell) for cell in cells[number - 1]] if number <= len(cells) else []
    return got + [""] * (WIDTH - len(got))


def writes(sheets: FakeSheetsClient) -> int:
    """Сколько раз в книгу карточек писали значения."""
    return sum(1 for method, _ in book(sheets).calls if method == "values_batch_update")


def draft_row(sessions: sessionmaker[Session], draft_id: object) -> models.CardDraft:
    with sessions() as session:
        draft = session.get(models.CardDraft, uuid.UUID(str(draft_id)))
        assert draft is not None
        return draft


def journal(sessions: sessionmaker[Session]) -> list[models.SheetWrite]:
    with sessions() as session:
        return list(session.scalars(select(models.SheetWrite).order_by(models.SheetWrite.id)))


def card(sessions: sessionmaker[Session], name: str) -> models.IngredientCard | None:
    with sessions() as session:
        return session.scalar(
            select(models.IngredientCard).where(models.IngredientCard.name == name)
        )


def mark_submitting(sessions: sessionmaker[Session], draft_id: object, ago: timedelta) -> None:
    """Отправка «идёт» с момента ``ago`` назад — по часам базы."""
    with sessions.begin() as session:
        session.execute(
            update(models.CardDraft)
            .where(models.CardDraft.id == uuid.UUID(str(draft_id)))
            .values(submit_started_at=func.now() - ago)
        )


def assert_still_active(sessions: sessionmaker[Session], draft_id: object) -> None:
    """Отказ записи: черновик активен, не отмечен отправленным, отправка не висит."""
    record = draft_row(sessions, draft_id)
    assert (record.status, record.submitted_row, record.submitted_at) == ("active", None, None)
    assert record.sheet_write_id is None
    assert record.submit_started_at is None


# ---------------------------------------------------------------------------
# Удачная запись
# ---------------------------------------------------------------------------
def test_card_lands_in_free_row_with_drive_view_links(
    cook: TestClient, drive: FakeDrive, sheets: FakeSheetsClient
) -> None:
    draft = ready(cook)
    label, package, before, after = uploaded(drive)

    reply = submit(cook, draft["id"])

    assert reply.status_code == 200, reply.text
    assert reply.json() == {
        "row": FREE_ROW,
        "already_written": False,
        "imported": True,
        "name": "Соус Барбекю",
        "not_written": [],
        "shifted": None,
        "notes": [],
    }
    written = line(sheets, FREE_ROW)
    assert written[:3] == ["Соусы", "Соус Барбекю", ""]
    assert written[P] == drive_view_url(label)
    assert written[S : U + 1] == [
        drive_view_url(package),
        drive_view_url(before),
        drive_view_url(after),
    ]
    assert written[V] == "Да"
    assert written[Q : R + 1] == ["", ""], "Q и R — колонки людей, их не пишем"


def test_one_google_login_per_submit_closed_after_answer(
    cook: TestClient, sheets: FakeSheetsClient
) -> None:
    """Писатель и перенос ходят одним входом в Google; перенос — с короткими
    таймаутами; после ответа вход закрыт."""
    draft = ready(cook)
    closed = sheets.closed

    assert submit(cook, draft["id"]).status_code == 200

    assert sheets.timeouts == [IMPORT_GOOGLE_TIMEOUT]
    assert sheets.closed == closed + 1


def test_draft_is_submitted_and_card_is_in_database_right_after(
    cook: TestClient, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    draft = ready(cook)
    label = uploaded(drive)[0]

    assert submit(cook, draft["id"]).status_code == 200

    [write] = journal(sessions)
    assert (write.status, write.row, write.actor_id) == (VERIFIED, FREE_ROW, COOK)
    assert write.request_key == f"card-draft:{draft['id']}"
    record = draft_row(sessions, draft["id"])
    assert (record.status, record.submitted_row, record.sheet_write_id) == (
        "submitted",
        FREE_ROW,
        write.id,
    )
    assert record.submitted_at is not None
    assert record.submit_started_at is None
    assert current(cook) is None, "активного черновика больше нет — можно «Добавить ещё»"
    imported = card(sessions, "Соус Барбекю")
    assert imported is not None, "карточка в базе сразу после ответа"
    assert (imported.source_row, imported.label_url, imported.approval_status) == (
        FREE_ROW,
        drive_view_url(label),
        "Да",
    )
    assert (imported.protein, imported.fat) == (Decimal("1.500"), Decimal("12.500"))
    assert imported.content_hash == write.content_hash


def test_rejected_without_photos_leaves_s_to_u_empty(
    cook: TestClient, sheets: FakeSheetsClient
) -> None:
    """«Отбракован» ведёт сразу к итогу: три фото продукта пропущены."""
    draft = ready(cook, approval="Отбракован", photos=("label",))

    assert submit(cook, draft["id"]).status_code == 200

    written = line(sheets, FREE_ROW)
    assert written[S : U + 1] == ["", "", ""]
    assert written[V] == "Отбракован"
    assert written[P].startswith("https://drive.google.com/file/d/")


def test_repeat_is_already_written_without_second_row(
    cook: TestClient, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    """Ответ потерялся в сети, повар нажал ещё раз: та же строка, без записи.
    Повтор идёт через журнал по ключу черновика — ключ на черновик один."""
    draft = ready(cook)
    first = submit(cook, draft["id"]).json()

    again = submit(cook, draft["id"])

    assert again.status_code == 200
    assert again.json() == {**first, "already_written": True}
    assert writes(sheets) == 1
    assert line(sheets, FREE_ROW + 1) == [""] * WIDTH
    assert len(journal(sessions)) == 1


@pytest.mark.parametrize("in_database", [True, False], ids=["imported", "not-imported"])
def test_repeat_does_not_go_to_google_and_tells_import_by_database(
    cook: TestClient,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
    in_database: bool,
) -> None:
    """Повтор отправленного черновика — ни одного запроса к Google: строка из
    журнала, «на сайте ли» — по базе. Иначе повтор после потерянного ответа
    снова ждал бы перенос и мог не дождаться никогда."""
    draft = ready(cook)
    assert submit(cook, draft["id"]).json()["imported"] is True
    if not in_database:
        with sessions.begin() as session:
            session.execute(
                update(models.IngredientCard)
                .where(models.IngredientCard.name == "Соус Барбекю")
                .values(removed_at=func.now())
            )
    asked = len(book(sheets).calls)

    again = submit(cook, draft["id"])

    assert again.status_code == 200
    assert book(sheets).calls[asked:] == [], "к Google — ни шага"
    assert again.json()["already_written"] is True
    assert again.json()["imported"] is in_database
    assert (NOT_IMPORTED in again.json()["notes"]) is not in_database


def test_two_submits_at_once(
    cook: TestClient, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    """Двойное нажатие: вторая отправка, пока идёт первая, — 409, а повтор
    после первой — та же строка, без второй записи."""
    draft = ready(cook)
    during: list[httpx.Response] = []
    hook(sheets, lambda: during.append(submit(cook, draft["id"])))

    first = submit(cook, draft["id"])
    after = submit(cook, draft["id"])

    assert [reply.status_code for reply in during] == [409]
    assert (
        during[0]
        .json()["detail"]
        .startswith("Карточка отправляется или отправка прервалась — попробуйте через")
    )
    assert first.status_code == 200
    assert after.status_code == 200
    assert after.json()["already_written"] is True
    assert after.json()["row"] == first.json()["row"] == FREE_ROW
    assert writes(sheets) == 1
    assert len(journal(sessions)) == 1


# ---------------------------------------------------------------------------
# Отказы записи: черновик остаётся
# ---------------------------------------------------------------------------
def test_missing_fields_is_422_with_titles(
    cook: TestClient, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    draft = start(cook)
    patch(cook, draft["id"], name="Соус Барбекю")

    reply = submit(cook, draft["id"])

    assert reply.status_code == 422
    missing = ["Поставщик", "Категория", "Фото этикетки", "Согласован ли продукт"]
    assert reply.json()["missing"] == missing
    assert all(title in reply.json()["detail"] for title in missing)
    assert book(sheets).calls == []
    assert journal(sessions) == []
    assert_still_active(sessions, draft["id"])


def test_duplicate_in_sheet_not_yet_in_database_is_409(
    cook: TestClient, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    """«Томаты» уже в листе (строка 3), а в базу ещё не перенесены — подсказка
    названия их не видела. Писатель видит лист и отказывает."""
    draft = ready(cook, name="томаты ")

    reply = submit(cook, draft["id"])

    assert reply.status_code == 409
    assert "уже есть в таблице — строка 3" in reply.json()["detail"]
    assert reply.json()["row"] == 3
    assert writes(sheets) == 0
    assert_still_active(sessions, draft["id"])


def test_shifted_header_is_503_and_draft_stays(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    cards = cards_sheet()
    shifted = header(specs.INGREDIENT_CARDS)
    shifted[7], shifted[8] = shifted[8], shifted[7]
    cards[0] = shifted
    sheets = sheets_client(cards=cards)
    client = make_client(drive, sheets, sessions)
    draft = ready(client)

    reply = submit(client, draft["id"])

    assert reply.status_code == 503
    assert "сдвинулись колонки" in reply.json()["detail"]
    assert "Черновик сохранён" in reply.json()["detail"]
    assert writes(sheets) == 0
    assert_still_active(sessions, draft["id"])


def test_google_refusal_is_502_draft_saved(
    cook: TestClient, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    """Черновик становится отправленным только после удачной записи."""
    draft = ready(cook)
    book(sheets).fail_next("values_batch_update", applied=False)

    reply = submit(cook, draft["id"])

    assert reply.status_code == 502
    assert "Черновик сохранён" in reply.json()["detail"]
    assert_still_active(sessions, draft["id"])
    [write] = journal(sessions)
    assert write.status == FAILED
    assert submit(cook, draft["id"]).status_code == 200, "повтор пишет заново"


def test_busy_sheet_is_503(
    people: None,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
) -> None:
    client = make_client(
        drive,
        sheets,
        sessions,
        writer=lambda: CardSheetWriter(
            sheets, CARDS, DbJournal(sessions), lock_timeout=timedelta(seconds=1)
        ),
    )
    draft = ready(client)
    holder = sessions()
    holder.begin()
    holder.execute(select(func.pg_advisory_xact_lock(SHEET_WRITE_LOCK_KEY)))
    try:
        reply = submit(client, draft["id"])
    finally:
        holder.rollback()
        holder.close()

    assert reply.status_code == 503
    assert reply.json() == {"detail": BUSY}
    assert book(sheets).calls == []
    assert_still_active(sessions, draft["id"])


def test_database_down_at_writers_queue_is_503(
    people: None,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
) -> None:
    """Очередь писателей не открылась — база не ответила. Повару — понятный
    текст, а не сырая ошибка базы; в лист ничего не ушло."""
    dead = make_session_factory("postgresql+psycopg://postgres@127.0.0.1:1/kitchen_test")
    client = make_client(
        drive, sheets, sessions, writer=lambda: CardSheetWriter(sheets, CARDS, DbJournal(dead))
    )
    draft = ready(client)

    reply = submit(client, draft["id"])

    assert reply.status_code == 503
    assert reply.json() == {"detail": DATABASE_DOWN}
    assert book(sheets).calls == []
    assert_still_active(sessions, draft["id"])


def test_sheet_not_configured_is_503(
    people: None, drive: FakeDrive, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    client = make_client(drive, sheets, sessions, config=settings(sheets_id_ingredient_cards=""))
    draft = ready(client)

    reply = submit(client, draft["id"])

    assert reply.status_code == 503
    assert "не настроена" in reply.json()["detail"]
    assert_still_active(sessions, draft["id"])


# ---------------------------------------------------------------------------
# Перенос в базу — не часть записи
# ---------------------------------------------------------------------------
def test_import_lock_busy_answers_quickly_with_imported_false(
    cook: TestClient, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    """Импортный замок занят (идёт перенос воркера или застрял ручной импорт)
    дольше короткого ожидания — 55P03. Строка уже в листе — это успех, и повар
    видит «Записано в строку N» за ~10 с, а не ждёт минуту или вечно;
    карточку перенесёт воркер. Сборка переноса — боевая, из приложения."""
    draft = ready(cook)
    importer = sessions()
    importer.begin()
    take_import_lock(importer)
    try:
        started = time.monotonic()
        reply = submit(cook, draft["id"])
        took = time.monotonic() - started
    finally:
        importer.rollback()
        importer.close()

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["row"], body["already_written"], body["imported"]) == (FREE_ROW, False, False)
    assert NOT_IMPORTED in body["notes"]
    # Граница — абсолютная, а не от константы: фронтенд ждёт отправку 60 с
    # вместе с записью, и ответ «записано» обязан прийти задолго до этого.
    assert 9 <= took < 20, f"ответ через {took:.1f} с"
    assert timedelta(seconds=9) <= IMPORT_LOCK_WAIT <= timedelta(seconds=15)
    assert draft_row(sessions, draft["id"]).status == "submitted"
    assert line(sheets, FREE_ROW)[1] == "Соус Барбекю"
    assert card(sessions, "Соус Барбекю") is None


def test_import_that_could_not_read_the_book_is_imported_false(
    people: None,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
) -> None:
    """Перенос сам не упал, но книгу прочитать не смог: причина — на сайте,
    как у любого цикла; повару — что карточка появится позже."""
    client = make_client(
        drive,
        sheets,
        sessions,
        cycle=lambda: SyncCycle(SheetsReader(FakeSheetsClient({}), IDS), sessions),
    )
    draft = ready(client)

    reply = submit(client, draft["id"])

    assert reply.status_code == 200, reply.text
    assert reply.json()["imported"] is False
    assert draft_row(sessions, draft["id"]).status == "submitted"


def test_writing_closed_by_ownership_rule_is_503(
    cook: TestClient,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Аварийный рычаг: боты ожили, запись в общие листы закрыта. Повару —
    понятный текст, а не ошибка сервера; в лист ничего не ушло."""
    draft = ready(cook)
    monkeypatch.setattr(ownership, "BOTS_ALIVE", True)

    reply = submit(cook, draft["id"])

    assert reply.status_code == 503
    assert "Запись в таблицу сейчас закрыта" in reply.json()["detail"]
    assert book(sheets).calls == []
    assert_still_active(sessions, draft["id"])


def _database_gone(*args: object, **kwargs: object) -> bool:
    raise OperationalError("update card_drafts", {}, Exception("server closed the connection"))


@pytest.mark.parametrize("release_fails", [False, True], ids=["mark-only", "mark-and-release"])
def test_row_written_but_draft_not_marked_tells_the_row_and_repeat_is_safe(
    cook: TestClient,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    release_fails: bool,
) -> None:
    """Строка легла, а база не ответила, когда черновик отмечался отправленным:
    повару — номер строки и «нажмите ещё раз»; повтор второй строки не даёт.

    Упала база целиком — не снялась и отметка «отправка идёт» (так же
    выглядит перезапуск контейнера посреди отправки). Повтор не ждёт её
    предела 16 минут: запись по ключу в журнале состоялась — черновик
    доводится до конца коротким путём, без записи."""
    draft = ready(cook)
    real_mark, real_end = draft_store.mark_submitted, draft_store.end_submit

    monkeypatch.setattr(draft_store, "mark_submitted", _database_gone)
    if release_fails:
        monkeypatch.setattr(draft_store, "end_submit", _database_gone)
    first = submit(cook, draft["id"])
    monkeypatch.setattr(draft_store, "mark_submitted", real_mark)
    monkeypatch.setattr(draft_store, "end_submit", real_end)
    stuck = draft_row(sessions, draft["id"]).submit_started_at is not None
    asked = len(book(sheets).calls)
    again = submit(cook, draft["id"])

    assert first.status_code == 503
    assert f"Карточка записана в строку {FREE_ROW}" in first.json()["detail"]
    assert stuck is release_fails
    assert again.status_code == 200, again.text
    assert (again.json()["row"], again.json()["already_written"]) == (FREE_ROW, True)
    assert writes(sheets) == 1
    record = draft_row(sessions, draft["id"])
    assert (record.status, record.submitted_row, record.submit_started_at) == (
        "submitted",
        FREE_ROW,
        None,
    )
    if release_fails:
        assert book(sheets).calls[asked:] == [], "доведение — из журнала, без Google"


def test_journal_trouble_after_the_write_asks_to_repeat(
    people: None,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
) -> None:
    """База не ответила, когда писатель закрывал запись журнала, — а строка уже
    легла (журнал ``pending``). Повару не «сервер не может записать», а
    «нажмите ещё раз»: повтор перечитает строку и второй не напишет."""

    class JournalFailsOnce(DbJournal):
        failed = False

        def finish(self, write_id: int, **changes: object) -> None:  # type: ignore[override]
            if not JournalFailsOnce.failed:
                JournalFailsOnce.failed = True
                raise OperationalError("update sheet_writes", {}, Exception("connection lost"))
            super().finish(write_id, **changes)  # type: ignore[arg-type]

    client = make_client(
        drive,
        sheets,
        sessions,
        writer=lambda: CardSheetWriter(sheets, CARDS, JournalFailsOnce(sessions)),
    )
    draft = ready(client)

    first = submit(client, draft["id"])
    [write] = journal(sessions)
    again = submit(client, draft["id"])

    assert first.status_code == 502
    assert first.json() == {"detail": UNCONFIRMED}
    assert write.status == "pending"
    assert again.status_code == 200, again.text
    assert (again.json()["row"], again.json()["already_written"]) == (FREE_ROW, True)
    assert writes(sheets) == 1


def test_unreadable_journal_after_database_trouble_asks_to_repeat(
    people: None,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """База не ответила посреди записи, и журнал записей тоже не прочитать —
    легла ли строка, неизвестно. Повару не «сервер не может записать», а
    «нажмите ещё раз»: повтор перечитает строку и второй не напишет."""

    class JournalFailsOnce(DbJournal):
        failed = False

        def finish(self, write_id: int, **changes: object) -> None:  # type: ignore[override]
            if not JournalFailsOnce.failed:
                JournalFailsOnce.failed = True
                raise OperationalError("update sheet_writes", {}, Exception("connection lost"))
            super().finish(write_id, **changes)  # type: ignore[arg-type]

    client = make_client(
        drive,
        sheets,
        sessions,
        writer=lambda: CardSheetWriter(sheets, CARDS, JournalFailsOnce(sessions)),
    )
    draft = ready(client)
    real_read = draft_store.open_sheet_write

    monkeypatch.setattr(draft_store, "open_sheet_write", _database_gone)
    first = submit(client, draft["id"])
    monkeypatch.setattr(draft_store, "open_sheet_write", real_read)
    again = submit(client, draft["id"])

    assert first.status_code == 502
    assert first.json() == {"detail": UNCONFIRMED}
    assert again.status_code == 200, again.text
    assert (again.json()["row"], again.json()["already_written"]) == (FREE_ROW, True)
    assert writes(sheets) == 1


class WriterThenMeanwhile:
    """Писатель, после записи которого — до отметки черновика — что-то
    происходит, один раз: так второе нажатие приходит между ``verified`` в
    журнале и «черновик отправлен»."""

    def __init__(self, writer: CardSheetWriter, meanwhile: list[Callable[[], None]]) -> None:
        self._writer = writer
        self._meanwhile = meanwhile

    def append(self, values: object, **keys: object) -> object:
        result = self._writer.append(values, **keys)  # type: ignore[arg-type]
        if self._meanwhile:
            self._meanwhile.pop()()
        return result


def test_second_press_between_write_and_mark_is_not_an_error(
    people: None,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Второе нажатие пришло, когда строка уже легла (журнал ``verified``), а
    черновик ещё не отмечен. Второй запрос доводит отправку из журнала, первый
    находит черновик уже отправленным той же строкой — это не ошибка: ни
    ERROR, ни WARNING в журнале сервера, одна строка в листе."""
    during: list[httpx.Response] = []
    meanwhile: list[Callable[[], None]] = []
    client = make_client(
        drive,
        sheets,
        sessions,
        writer=lambda: WriterThenMeanwhile(
            CardSheetWriter(sheets, CARDS, DbJournal(sessions)), meanwhile
        ),
    )
    draft = ready(client)
    meanwhile.append(lambda: during.append(submit(client, draft["id"])))

    with caplog.at_level(logging.INFO, logger="kitchen"):
        first = submit(client, draft["id"])

    assert first.status_code == 200, first.text
    [second] = during
    assert second.status_code == 200, second.text
    assert first.json()["row"] == second.json()["row"] == FREE_ROW
    assert second.json()["already_written"] is True
    assert writes(sheets) == 1
    loud = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert loud == []
    record = draft_row(sessions, draft["id"])
    assert (record.status, record.submitted_row) == ("submitted", FREE_ROW)


def test_submit_waits_for_running_recognition(
    cook: TestClient, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    """Пока модель читает этикетку, отправлять рано: её итог лёг бы в
    черновик, уже ушедший в лист, и пропал. Зависшее «идёт» — не помеха."""
    draft = ready(cook)

    def running(ago: timedelta) -> None:
        with sessions.begin() as session:
            session.execute(
                update(models.CardDraft)
                .where(models.CardDraft.id == uuid.UUID(str(draft["id"])))
                .values(recognition_status="running", recognition_started_at=func.now() - ago)
            )

    running(timedelta(seconds=5))
    fresh = submit(cook, draft["id"])
    running(STALE_AFTER + timedelta(minutes=1))
    stale = submit(cook, draft["id"])

    assert fresh.status_code == 409
    assert fresh.json() == {"detail": "Дождитесь окончания распознавания"}
    assert stale.status_code == 200, stale.text
    assert writes(sheets) == 1


# ---------------------------------------------------------------------------
# Повтор после неподтверждённой записи (Ruling 15H) и правки, не попавшие в лист
# ---------------------------------------------------------------------------
def test_failed_write_that_landed_is_already_written_on_repeat(
    cook: TestClient,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
) -> None:
    """Шеф вставил строку в окне записи: раскладку не подтвердили, журнал —
    ``failed``, а строка легла. Повтор находит её, второй не пишет и говорит,
    какую строку показать шефу. Фото в корзину не идут."""
    draft = ready(cook)
    book(sheets).chef_inserts_rows("Лист1", above=3, moment="before_write")

    first = submit(cook, draft["id"])

    assert first.status_code == 502
    assert "Таблицу меняли в ту же секунду" in first.json()["detail"]
    assert_still_active(sessions, draft["id"])
    [attempt] = journal(sessions)
    assert (attempt.status, attempt.note) == (FAILED, LAYOUT_UNCONFIRMED)
    sent = len(drive.sent)

    again = submit(cook, draft["id"])

    assert again.status_code == 200, again.text
    body = again.json()
    assert (body["row"], body["already_written"]) == (attempt.row, True)
    assert body["shifted"] == {"row": attempt.row, "journal_id": attempt.id}
    assert (
        f"При первой попытке таблицу меняли — покажите шефу строку {attempt.row} "
        f"(запись журнала №{attempt.id})"
    ) in body["notes"]
    assert writes(sheets) == 1
    record = draft_row(sessions, draft["id"])
    assert (record.status, record.submitted_row) == ("submitted", attempt.row)
    assert drive.sent[sent:] == [], "ни одного запроса к Drive — фото не в корзину"
    assert trashed(drive) == []


def test_edit_that_did_not_reach_the_sheet_is_named(
    cook: TestClient, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    draft = ready(cook)
    book(sheets).chef_inserts_rows("Лист1", above=3, moment="before_write")
    assert submit(cook, draft["id"]).status_code == 502
    [attempt] = journal(sessions)
    assert patch(cook, draft["id"], fat="13").status_code == 200

    reply = submit(cook, draft["id"])

    assert reply.status_code == 200
    body = reply.json()
    assert body["not_written"] == ["Жиры"]
    assert (
        f"Карточка уже в строке {attempt.row} в первой версии; правки «Жиры» не попали — "
        "скажите шефу"
    ) in body["notes"]


# ---------------------------------------------------------------------------
# Гонка отправки с фото и отменой
# ---------------------------------------------------------------------------
def test_photos_and_cancel_wait_while_card_is_being_sent(
    cook: TestClient,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
) -> None:
    """Пока идёт отправка, ссылки на фото уже летят в лист: заменить, убрать
    фото или отменить черновик нельзя — 409, фото на месте и не в корзине."""
    draft = ready(cook)
    files = uploaded(drive)
    _, package, before, after = files
    during: dict[str, httpx.Response] = {}

    def meanwhile() -> None:
        during["replace"] = put_photo(cook, draft["id"], "package", JPEG_2)
        during["remove"] = cook.delete(photo_url(draft["id"], "before"), headers=CSRF)
        during["cancel"] = cook.delete(f"/api/cards/drafts/{draft['id']}", headers=CSRF)
        during["edit"] = patch(cook, draft["id"], composition="другой состав")

    hook(sheets, meanwhile)

    reply = submit(cook, draft["id"])

    assert reply.status_code == 200
    assert {name: r.status_code for name, r in during.items()} == {
        "replace": 409,
        "remove": 409,
        "cancel": 409,
        "edit": 409,
    }
    assert draft_row(sessions, draft["id"]).composition == "томатная паста, сахар", (
        "правка не пропала молча: её не приняли, и повар это видел"
    )
    assert {r.json()["detail"] for r in during.values()} == {"Карточка отправляется — подождите"}
    assert uploaded(drive) == files, "новое фото не загружалось"
    assert trashed(drive) == []
    written = line(sheets, FREE_ROW)
    assert written[S : U + 1] == [
        drive_view_url(package),
        drive_view_url(before),
        drive_view_url(after),
    ]


def test_upload_that_finished_after_submit_started_is_409(
    cook: TestClient,
    drive: FakeDrive,
    sessions: sessionmaker[Session],
) -> None:
    """Фото летело в Drive, а повар тем временем нажал «Отправить»: новое фото
    в слот не ложится (ссылка на прежнее уже уходит в лист) и уходит в корзину,
    прежнее — на месте."""
    draft = ready(cook, photos=("label", "package"))
    _, package = uploaded(drive)

    class SubmitStartedMeanwhile(DriveClient):
        def upload_jpeg(
            self, content: bytes, *, name: str, app_properties: dict[str, str] | None = None
        ) -> str:
            file_id = super().upload_jpeg(content, name=name, app_properties=app_properties)
            mark_submitting(sessions, draft["id"], timedelta(0))
            return file_id

    cook.app.dependency_overrides[cards.get_drive] = lambda: SubmitStartedMeanwhile(  # type: ignore[attr-defined]
        drive.session, folder_id=FOLDER_ID, timeout=(5, 30)
    )

    reply = put_photo(cook, draft["id"], "package", JPEG_2)

    assert reply.status_code == 409
    assert reply.json() == {"detail": "Карточка отправляется — подождите"}
    [_, _, fresh] = uploaded(drive)
    assert trashed(drive) == [fresh]
    assert draft_row(sessions, draft["id"]).package_file_id == package
    assert drive.files[package].content == JPEG


def test_stale_submit_mark_does_not_lock_the_draft(
    cook: TestClient, sessions: sessionmaker[Session]
) -> None:
    """Процесс умер посреди отправки — отметка осталась. Свежая — 409; старше
    предела (худшая запись писателя с запасом) — отправка снова разрешена."""
    window = submit_window(settings())
    assert window > hold_limit(settings())
    draft = ready(cook)

    mark_submitting(sessions, draft["id"], timedelta(seconds=10))
    fresh = submit(cook, draft["id"])
    mark_submitting(sessions, draft["id"], window + timedelta(minutes=1))
    stale = submit(cook, draft["id"])

    assert fresh.status_code == 409
    minutes = math.ceil((window - timedelta(seconds=10)) / timedelta(minutes=1))
    assert fresh.json() == {
        "detail": "Карточка отправляется или отправка прервалась — попробуйте через "
        f"{minutes} минут"
    }
    assert stale.status_code == 200, stale.text


# ---------------------------------------------------------------------------
# Вход, роли, чужой и закрытый черновик
# ---------------------------------------------------------------------------
def test_without_login_is_401(
    people: None, drive: FakeDrive, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    client = make_client(drive, sheets, sessions, user=None)

    assert submit(client, uuid.uuid4()).status_code == 401


def test_commerce_is_403(
    people: None, drive: FakeDrive, sheets: FakeSheetsClient, sessions: sessionmaker[Session]
) -> None:
    client = make_client(drive, sheets, sessions, roles=("commerce",))

    assert submit(client, uuid.uuid4()).status_code == 403
    assert book(sheets).calls == []


@pytest.mark.parametrize("role", ["chef", "developer"])
def test_chef_and_developer_may_submit(
    people: None,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
    role: str,
) -> None:
    client = make_client(drive, sheets, sessions, roles=(role,))
    draft = ready(client)

    assert submit(client, draft["id"]).status_code == 200


def test_foreign_and_cancelled_drafts_are_404(
    cook: TestClient,
    drive: FakeDrive,
    sheets: FakeSheetsClient,
    sessions: sessionmaker[Session],
) -> None:
    draft = ready(cook)
    other = make_client(drive, sheets, sessions, user=OTHER)

    foreign = submit(other, draft["id"])
    assert cook.delete(f"/api/cards/drafts/{draft['id']}", headers=CSRF).status_code == 204
    cancelled = submit(cook, draft["id"])

    assert (foreign.status_code, cancelled.status_code) == (404, 404)
    assert book(sheets).calls == []


def test_without_csrf_header_is_403(cook: TestClient, sheets: FakeSheetsClient) -> None:
    draft = ready(cook)
    cook.cookies.set(auth.ACCESS_COOKIE, "token-from-browser")

    reply = cook.post(f"/api/cards/drafts/{draft['id']}/submit")

    assert reply.status_code == 403
    assert book(sheets).calls == []
