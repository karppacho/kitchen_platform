"""Писатель строки и журнал записей на настоящем Postgres, без сети.

Офлайн-тесты проверяют решения писателя на журнале в памяти. Здесь — то,
чего в памяти нет: advisory-блокировка писателей между двумя потоками,
журнал `sheet_writes` со своими ограничениями и путь карточки в базу
обычным переносом книги.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from kitchen.db import models
from kitchen.db.journal import FAILED, PENDING, VERIFIED, DbJournal, NewWrite
from kitchen.domain.cards import APPROVED, CardDraftData, card_row
from kitchen.sync import specs
from kitchen.sync.cycle import BookOutcome, SyncCycle
from kitchen.sync.importer import take_import_lock
from kitchen.sync.reader import SheetsReader
from kitchen.sync.writer import SHEET_WRITE_LOCK_KEY, CardSheetWriter, SheetBusyError
from tests.conftest import FakeSheetsClient, FakeSpreadsheet, FakeWorksheet
from tests.fake_sheets import IDS, cards_sheet, kitchen_sheets, row, sheets_client
from tests.integration.test_database import BACKEND, _url

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

KEY = "card-draft:1"
WIDTH = len(specs.INGREDIENT_CARDS.columns)
BEFORE_JOURNAL = "7f3c2a9e5d41"
"""Ревизия перед журналом записей — голова до этой задачи."""


def _values(name: str = "Соус Барбекю", label: str = "lbl1") -> dict[str, str | Decimal]:
    return card_row(
        CardDraftData(
            supplier="Север",
            category="Соусы",
            name=name,
            protein=Decimal("0.1"),
            fat=Decimal("12.5"),
            carbs=Decimal("12.000"),
            approval=APPROVED,
            label_file_id=label,
        )
    )


class SlowSpreadsheet(FakeSpreadsheet):
    """Книга карточек, чтение листа целиком в которой идёт заметное время: за
    него второй писатель успевает встать в очередь — или, без блокировки,
    прочитать тот же лист и выбрать ту же строку."""

    def values_batch_get(
        self, ranges: list[str], params: dict[str, str] | None = None
    ) -> dict[str, object]:
        if ranges == ["'Лист1'"]:
            time.sleep(0.3)
        return super().values_batch_get(ranges, params)


def _client(*, slow: bool = False) -> FakeSheetsClient:
    """Обе книги; лист карточек — три карточки в строках 3–5."""
    client = sheets_client()
    kind = SlowSpreadsheet if slow else FakeSpreadsheet
    client._spreadsheets[IDS["ingredient_cards"]] = kind(
        {"Лист1": FakeWorksheet(cards_sheet(), "Лист1")}
    )
    return client


def _writer(
    client: FakeSheetsClient, sessions: sessionmaker[Session], **options
) -> CardSheetWriter:
    return CardSheetWriter(client, IDS["ingredient_cards"], DbJournal(sessions), **options)


def _cards_book(client: FakeSheetsClient) -> FakeSpreadsheet:
    return client._spreadsheets[IDS["ingredient_cards"]]


def _line(client: FakeSheetsClient, number: int) -> list[str]:
    cells = _cards_book(client)._sheets["Лист1"].get_all_values()
    line = cells[number - 1] if number <= len(cells) else []
    return line + [""] * (WIDTH - len(line))


def _profile(sessions: sessionmaker[Session]) -> uuid.UUID:
    profile_id = uuid.uuid4()
    with sessions() as session, session.begin():
        session.add(models.Profile(id=profile_id, email="cook@example.test", display_name="Повар"))
    return profile_id


def _journal(sessions: sessionmaker[Session]) -> list[models.SheetWrite]:
    with sessions() as session:
        return list(session.scalars(select(models.SheetWrite).order_by(models.SheetWrite.id)))


# ---------------------------------------------------------------------------
# Два писателя разом
# ---------------------------------------------------------------------------
def test_two_writers_take_rows_one_after_another(sessions) -> None:
    """Два повара отправили карточки в одну секунду. Писатели идут по одному:
    второй читает лист уже после записи первого и берёт следующую строку.
    Без блокировки оба выбрали бы строку 6, и второй затёр бы первого."""
    client = _client(slow=True)
    writer = _writer(client, sessions)
    results: dict[str, int] = {}
    errors: list[Exception] = []

    def submit(name: str, label: str, key: str) -> None:
        try:
            results[name] = writer.append(_values(name, label), actor_id=None, request_key=key).row
        except Exception as error:  # ошибку потока показываем в утверждении
            errors.append(error)

    threads = [
        threading.Thread(target=submit, args=("Соус Барбекю", "lbl1", "card-draft:1")),
        threading.Thread(target=submit, args=("Соус Сырный", "lbl2", "card-draft:2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert sorted(results.values()) == [6, 7]
    for name, number in results.items():
        assert _line(client, number)[1] == name
    assert [(w.row, w.status) for w in sorted(_journal(sessions), key=lambda w: w.row)] == [
        (6, VERIFIED),
        (7, VERIFIED),
    ]


def test_writer_waits_for_writers_not_for_import(sessions) -> None:
    """Блокировка писателей своя: идущий перенос в базу запись не держит, а
    чужой писатель, застрявший дольше предела, — «таблица занята», и в
    Google не уходит ни одного запроса."""
    client = _client()
    importer = sessions()
    importer.begin()
    take_import_lock(importer)
    try:
        assert _writer(client, sessions).append(_values(), actor_id=None, request_key=KEY).row == 6
    finally:
        importer.rollback()
        importer.close()

    holder = sessions()
    holder.begin()
    holder.execute(text("select pg_advisory_xact_lock(:key)"), {"key": SHEET_WRITE_LOCK_KEY})
    asked = _cards_book(client).requests
    try:
        with pytest.raises(SheetBusyError, match="Таблица занята"):
            _writer(client, sessions, lock_timeout=timedelta(seconds=1)).append(
                _values("Соус Сырный", "lbl2"), actor_id=None, request_key="card-draft:2"
            )
    finally:
        holder.rollback()
        holder.close()
    assert _cards_book(client).requests == asked


# ---------------------------------------------------------------------------
# Журнал
# ---------------------------------------------------------------------------
def test_journal_keeps_values_snapshot_and_author(sessions) -> None:
    actor = _profile(sessions)
    client = _client()

    result = _writer(client, sessions).append(_values(), actor_id=actor, request_key=KEY)

    [record] = _journal(sessions)
    assert record.id == result.journal_id
    assert (record.book, record.sheet, record.row, record.action) == (
        "ingredient_cards",
        "Лист1",
        6,
        "append",
    )
    assert (record.status, record.request_key, record.actor_id) == (VERIFIED, KEY, actor)
    assert record.values["name"] == "Соус Барбекю"
    assert (record.values["protein"], record.values["fat"], record.values["carbs"]) == (
        0.1,
        12.5,
        12,
    )
    assert record.values["approval_status"] == APPROVED
    assert record.before == {"rows": {"5": _line_of(cards_sheet()[4]), "6": [""] * WIDTH}}
    assert record.after is not None
    assert record.after["rows"]["6"][1] == "Соус Барбекю"
    assert record.content_hash is not None and len(record.content_hash) == 64
    assert record.finished_at is not None and record.finished_at >= record.created_at


def _line_of(line: list[str]) -> list[str]:
    return line + [""] * (WIDTH - len(line))


def test_card_reaches_database_by_one_book_import(sessions) -> None:
    """Карточка попадает в базу обычным переносом книги карточек, и импорт
    узнаёт в ней записанное: номер строки и хеш — те же, что в журнале."""
    client = _client()
    SyncCycle(SheetsReader(client, IDS), sessions).run()

    result = _writer(client, sessions).append(_values(), actor_id=None, request_key=KEY)
    cycle = SyncCycle(SheetsReader(client, IDS), sessions).run(
        force=True, books=("ingredient_cards",)
    )

    assert cycle.outcomes == {"ingredient_cards": BookOutcome("imported")}
    query = select(models.IngredientCard).where(models.IngredientCard.name == "Соус Барбекю")
    with sessions() as session:
        card = session.scalar(query)
        record = session.get(models.SheetWrite, result.journal_id)
    assert card is not None and record is not None
    assert card.source_row == result.row
    assert card.content_hash == record.content_hash
    assert (card.protein, card.fat, card.carbs, card.kcal) == (
        Decimal("0.100"),
        Decimal("12.500"),
        Decimal("12.000"),
        None,
    )


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_one_book_import_leaves_kitchen_alone(sessions) -> None:
    """Перенос одной книги карточек не читает и не трогает кухню: цена в
    справочнике — прежняя, хотя в листе уже новая, состояние кухни не
    сдвинулось."""
    clock = Clock()
    SyncCycle(SheetsReader(sheets_client(), IDS), sessions, clock).run()
    with sessions() as session:
        kitchen = session.get(models.SyncState, "kitchen")
        runs = session.scalar(select(func.count()).select_from(models.SyncRun))
    clock.now += timedelta(minutes=5)
    ing = kitchen_sheets()["ING"]
    ing[1] = row(specs.INGREDIENTS, id="1", name="Томаты", price_per_kg="170", status="активное")
    cards = cards_sheet()
    cards[2] = row(specs.INGREDIENT_CARDS, name="Томаты", supplier="Новый поставщик")
    reader = SheetsReader(sheets_client(kitchen={"ING": ing}, cards=cards), IDS)

    result = SyncCycle(reader, sessions, clock).run(force=True, books=("ingredient_cards",))

    assert set(result.outcomes) == {"ingredient_cards"}
    with sessions() as session:
        tomato = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "1"))
        supplier = session.scalar(
            select(models.IngredientCard.supplier).where(models.IngredientCard.name == "Томаты")
        )
        kitchen_now = session.get(models.SyncState, "kitchen")
        cards_state = session.get(models.SyncState, "ingredient_cards")
        runs_now = session.scalar(select(func.count()).select_from(models.SyncRun))
    assert tomato is not None and str(tomato.price_per_kg) == "177.00"
    assert supplier == "Новый поставщик"
    assert kitchen_now is not None and kitchen is not None
    assert (kitchen_now.checked_at, kitchen_now.fingerprint) == (
        kitchen.checked_at,
        kitchen.fingerprint,
    )
    assert cards_state is not None and cards_state.checked_at == clock.now
    assert runs_now == runs + 1, "одно событие журнала — перенос карточек"


# ---------------------------------------------------------------------------
# Схема журнала
# ---------------------------------------------------------------------------
def _new(key: str = KEY, actor: uuid.UUID | None = None) -> NewWrite:
    return NewWrite(
        book="ingredient_cards",
        sheet="Лист1",
        row=6,
        request_key=key,
        actor_id=actor,
        before={"rows": {"5": ["a"], "6": [""]}},
        values={"name": "Соус", "fat": 12.5},
    )


def test_one_open_write_per_request_key(sessions) -> None:
    """Открытая запись (pending или verified) на ключ запроса — одна: это
    держит частичный уникальный индекс. Неудачная ключ освобождает — повтор
    отправки начинает новую попытку."""
    journal = DbJournal(sessions)
    first = journal.start(_new())

    with pytest.raises(IntegrityError):
        journal.start(_new())

    journal.finish(first, status=FAILED, error="не легла")
    second = journal.start(_new())
    found = journal.find_open(KEY)
    assert found is not None
    assert (found.id, found.status, found.row) == (second, PENDING, 6)
    assert found.values == {"name": "Соус", "fat": 12.5}


def test_unknown_status_is_refused_by_database(sessions) -> None:
    journal = DbJournal(sessions)
    write_id = journal.start(_new())

    with pytest.raises(IntegrityError):
        journal.finish(write_id, status="почти")


def test_deleted_profile_keeps_the_journal(sessions) -> None:
    """Уволенного повара удаляют — след его записи остаётся, без автора."""
    actor = _profile(sessions)
    write_id = DbJournal(sessions).start(_new(actor=actor))

    with sessions() as session, session.begin():
        session.delete(session.get(models.Profile, actor))

    with sessions() as session:
        record = session.get(models.SheetWrite, write_id)
    assert record is not None and record.actor_id is None


def test_journal_migration_round_trip_with_data(sessions) -> None:
    """upgrade → downgrade → upgrade на непустой базе: в журнале есть записи,
    откат сносит таблицу целиком, повторный подъём создаёт её с индексами."""
    DbJournal(sessions).start(_new())
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", _url())
    engine = create_engine(_url())
    try:
        command.downgrade(config, BEFORE_JOURNAL)
        assert "sheet_writes" not in inspect(engine).get_table_names()

        command.upgrade(config, "head")
        inspector = inspect(engine)
        indexes = {index["name"]: index for index in inspector.get_indexes("sheet_writes")}
        assert indexes["ix_sheet_writes_actor_id"]["column_names"] == ["actor_id"]
        open_keys = indexes["ux_sheet_writes_open_request_key"]
        assert open_keys["unique"]
        assert open_keys["column_names"] == ["request_key"]
        [foreign] = inspector.get_foreign_keys("sheet_writes")
        assert (foreign["referred_table"], foreign["options"].get("ondelete")) == (
            "profiles",
            "SET NULL",
        )
    finally:
        engine.dispose()
