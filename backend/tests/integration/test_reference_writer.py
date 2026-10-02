"""Писатель строки справочника и журнал на настоящем Postgres, без сети.

Офлайн-тесты проверяют решения писателя на журнале в памяти. Здесь — то,
чего в памяти нет: очередь писателей между двумя потоками (id выдаётся под
ней, и два переноса разом получают разные строки и id подряд), действие
`fill` в журнале `sheet_writes` с его CHECK и миграция этого CHECK
вверх-вниз-вверх на непустой базе.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError

from kitchen.db import models
from kitchen.db.journal import FAILED, PENDING, ROLLED_BACK, VERIFIED, DbJournal, NewWrite
from kitchen.db.links import known_reference_ids
from kitchen.domain.reference_row import ReferenceForm
from kitchen.sync.reference_writer import FillResult, ReferenceRowFiller
from tests.conftest import (
    FakeSheetsClient,
    FakeSpreadsheet,
    FakeWorksheet,
    ing_sheet,
    reference_client,
)
from tests.fake_sheets import IDS
from tests.integration.test_database import BACKEND, _url

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

KEY = "ing-fill:7"
BEFORE_FILL = "ff5c8d28b0a1"
"""Ревизия перед действием `fill` — голова до этой задачи."""


class SlowKitchen(FakeSpreadsheet):
    """Книга кухни, где чтение листа ING целиком идёт заметное время: за него
    второй писатель успевает встать в очередь — или, без очереди, прочитать
    тот же лист и выдать тот же id."""

    def __init__(self, sheets: dict[str, FakeWorksheet], *, delay: float) -> None:
        super().__init__(sheets)
        self._delay = delay

    def values_batch_get(
        self, ranges: list[str], params: dict[str, str] | None = None
    ) -> dict[str, object]:
        if ranges == ["'ING'"]:
            time.sleep(self._delay)
        return super().values_batch_get(ranges, params)


def _client(*, delay: float = 0.0) -> FakeSheetsClient:
    client = reference_client()
    if delay:
        client._spreadsheets[IDS["kitchen"]] = SlowKitchen({"ING": ing_sheet()}, delay=delay)
    return client


def _ing(client: FakeSheetsClient) -> FakeWorksheet:
    return client._spreadsheets[IDS["kitchen"]]._sheets["ING"]


def _requests(client: FakeSheetsClient) -> int:
    return sum(book.requests for book in client._spreadsheets.values())


def _filler(client: FakeSheetsClient, sessions: sessionmaker[Session]) -> ReferenceRowFiller:
    return ReferenceRowFiller(
        client,
        DbJournal(sessions),
        kitchen_id=IDS["kitchen"],
        cards_id=IDS["ingredient_cards"],
        known_ids=lambda: known_reference_ids(sessions),
    )


def _form() -> ReferenceForm:
    return ReferenceForm.parse(
        {"short_name": "Соус", "unit": "кг", "price_per_pack": "1250,5", "losses_unpacking": "12,5"}
    )


def _journal(sessions: sessionmaker[Session]) -> list[models.SheetWrite]:
    with sessions() as session:
        return list(session.scalars(select(models.SheetWrite).order_by(models.SheetWrite.id)))


def _profile(sessions: sessionmaker[Session]) -> uuid.UUID:
    profile_id = uuid.uuid4()
    with sessions() as session, session.begin():
        session.add(models.Profile(id=profile_id, email="chef@example.test", display_name="Шеф"))
    return profile_id


# ---------------------------------------------------------------------------
# Два переноса разом
# ---------------------------------------------------------------------------
def test_two_fills_at_once_get_rows_and_ids_one_after_another(sessions) -> None:
    """Шеф и коммерция переносят две карточки в одну секунду. Писатели идут
    по одному: второй читает лист уже после записи первого, и id — подряд.
    Без очереди оба увидели бы наибольший id 130 и выдали бы 131 дважды."""
    client = _client(delay=0.3)
    filler = _filler(client, sessions)
    results: dict[str, FillResult] = {}
    errors: list[Exception] = []

    def fill(name: str, key: str) -> None:
        try:
            results[name] = filler.fill(
                name, _form(), actor_id=None, request_key=key, confirmed=False
            )
        except Exception as error:  # ошибку потока показываем в утверждении
            errors.append(error)

    threads = [
        threading.Thread(target=fill, args=("Соус Барбекю", "ing-fill:1")),
        threading.Thread(target=fill, args=("Соус Сырный", "ing-fill:2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert {name: result.row for name, result in results.items()} == {
        "Соус Барбекю": 6,
        "Соус Сырный": 7,
    }
    assert sorted(result.ref_id for result in results.values()) == ["131", "132"]
    for result in results.values():
        assert _ing(client).cell(f"A{result.row}") == int(result.ref_id)
    assert sorted((w.row, w.action, w.status) for w in _journal(sessions)) == [
        (6, "fill", VERIFIED),
        (7, "fill", VERIFIED),
    ]


# ---------------------------------------------------------------------------
# Журнал
# ---------------------------------------------------------------------------
def test_fill_journal_on_postgres(sessions) -> None:
    """След заполнения в `sheet_writes`: действие fill, книга кухни, лист ING,
    отправленное числами JSON, автор. Повтор с тем же ключом отвечает по
    журналу, без запросов к Google."""
    actor = _profile(sessions)
    client = _client()
    filler = _filler(client, sessions)

    result = filler.fill("Соус Барбекю", _form(), actor_id=actor, request_key=KEY, confirmed=False)

    [record] = _journal(sessions)
    assert record.id == result.journal_id
    assert (record.book, record.sheet, record.row, record.action) == ("kitchen", "ING", 6, "fill")
    assert (record.status, record.request_key, record.actor_id) == (VERIFIED, KEY, actor)
    assert (record.values["id"], record.values["losses_unpacking"]) == (131, 0.125)
    assert record.values["price_per_pack"] == 1250.5
    assert record.before["anchor"] == 4
    assert "sheet" not in record.before
    assert record.finished_at is not None
    asked = _requests(client)

    again = filler.fill("Соус Барбекю", _form(), actor_id=actor, request_key=KEY, confirmed=False)

    assert again == FillResult(row=6, ref_id="131", journal_id=record.id, already=True)
    assert _requests(client) == asked


def _new(key: str, action: str) -> NewWrite:
    return NewWrite(
        book="kitchen",
        sheet="ING",
        row=6,
        request_key=key,
        actor_id=None,
        before={"rows": {}},
        values={"id": 131},
        action=action,
    )


def _config() -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", _url())
    return config


def test_fill_migration_round_trip_with_data(sessions) -> None:
    """upgrade → downgrade → upgrade на непустой базе. Откат возвращает
    прежний CHECK и стирает завершённые записи `fill` (verified,
    rolled_back) — прежний CHECK их не допускает; записи `append` остаются.
    Повторный подъём снова принимает `fill`."""
    journal = DbJournal(sessions)
    journal.start(_new("card-draft:1", "append"))
    journal.finish(journal.start(_new("ing-fill:1", "fill")), status=VERIFIED, content_hash="0")
    journal.finish(journal.start(_new("ing-fill:2", "fill")), status=ROLLED_BACK, error="чужое")
    config = _config()
    engine = create_engine(_url())
    try:
        command.downgrade(config, BEFORE_FILL)

        assert [(w.request_key, w.action) for w in _journal(sessions)] == [
            ("card-draft:1", "append")
        ]
        with pytest.raises(IntegrityError):
            journal.start(_new("ing-fill:2", "fill"))

        command.upgrade(config, "head")

        journal.start(_new("ing-fill:3", "fill"))
        assert [w.action for w in _journal(sessions)] == ["append", "fill"]
        checks = {
            c["name"]: c["sqltext"] for c in inspect(engine).get_check_constraints("sheet_writes")
        }
        assert "fill" in checks["ck_sheet_writes_action"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("status", [PENDING, FAILED])
def test_fill_migration_refuses_downgrade_while_snapshots_remain(sessions, status: str) -> None:
    """Запись `fill`, которая не закончилась (pending) или не подтвердилась
    (failed), хранит снимок листа ING — единственный материал, чтобы вернуть
    затёртое руками. Откат её не стирает: отказывает понятной ошибкой, и
    база остаётся на новой ревизии."""
    journal = DbJournal(sessions)
    write_id = journal.start(_new("ing-fill:1", "fill"))
    if status == FAILED:
        journal.finish(write_id, status=FAILED, error="раскладка не подтверждена")
    config = _config()

    with pytest.raises(RuntimeError, match="Откат невозможен") as caught:
        command.downgrade(config, BEFORE_FILL)

    assert f"№{write_id}" in str(caught.value)
    [record] = _journal(sessions)
    assert (record.action, record.status) == ("fill", status)
    journal.start(_new("ing-fill:2", "fill"))  # CHECK новый: база осталась на этой ревизии
