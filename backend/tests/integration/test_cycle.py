"""Цикл синхронизации на настоящем Postgres, без сети."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from kitchen.db import models
from kitchen.sync import specs
from kitchen.sync.cycle import SyncCycle
from kitchen.sync.reader import BOOK_OPEN_FAILED, SheetsReader
from tests.fake_sheets import IDS, cards_sheet, kitchen_sheets, row, sheets_client

pytestmark = pytest.mark.integration


class Clock:
    """Часы, которые идут только по команде."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, minutes: int = 5) -> None:
        self.now += timedelta(minutes=minutes)


def _cycle(sessions, clock, **sheets) -> SyncCycle:
    return SyncCycle(SheetsReader(sheets_client(**sheets), IDS), sessions, clock)


def _state(sessions, book: str) -> models.SyncState:
    with sessions() as session:
        return session.get(models.SyncState, book)


def _runs(sessions) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(models.SyncRun))


def _tomato_price(sessions) -> str:
    with sessions() as session:
        tomato = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "1"))
        return str(tomato.price_per_kg)


def _cheaper_tomato() -> list[list[str]]:
    ing = kitchen_sheets()["ING"]
    ing[1] = row(specs.INGREDIENTS, id="1", name="Томаты", price_per_kg="170", status="активное")
    return ing


def _shifted(ing: list[list[str]]) -> list[list[str]]:
    ing[0] = list(ing[0])
    ing[0][1] = "Совсем другая колонка"
    return ing


def test_first_cycle_imports_both_books(sessions) -> None:
    clock = Clock()

    result = _cycle(sessions, clock).run()

    assert {b: o.action for b, o in result.outcomes.items()} == {
        "kitchen": "imported",
        "ingredient_cards": "imported",
    }
    kitchen = _state(sessions, "kitchen")
    assert (kitchen.checked_at, kitchen.changed_at) == (clock.now, clock.now)
    assert kitchen.fingerprint is not None and len(kitchen.fingerprint) == 64
    assert kitchen.problem is None
    assert _runs(sessions) == 1, "перенос — событие журнала"


def test_unchanged_sheets_write_only_checked_at(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    result = _cycle(sessions, clock).run()

    assert {o.action for o in result.outcomes.values()} == {"unchanged"}
    kitchen = _state(sessions, "kitchen")
    assert kitchen.checked_at == clock.now
    assert kitchen.changed_at == clock.now - timedelta(minutes=5)
    assert _runs(sessions) == 1, "«ничего не изменилось» — не событие"


def test_changed_cell_is_imported(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    result = _cycle(sessions, clock, kitchen={"ING": _cheaper_tomato()}).run()

    assert result.outcomes["kitchen"].action == "imported"
    assert result.outcomes["ingredient_cards"].action == "unchanged"
    assert _state(sessions, "kitchen").changed_at == clock.now
    assert _tomato_price(sessions) == "170.00"


def test_shifted_columns_block_book_but_not_the_other(sessions) -> None:
    """Сдвиг колонок в кухне не мешает перенести карточки: книги независимы."""
    clock = Clock()
    _cycle(sessions, clock).run()
    checked = clock.now
    clock.tick()
    cards = cards_sheet()
    cards[2] = row(specs.INGREDIENT_CARDS, name="Томаты", supplier="Новый поставщик")

    result = _cycle(
        sessions, clock, kitchen={"ING": _shifted(_cheaper_tomato())}, cards=cards
    ).run()

    kitchen = result.outcomes["kitchen"]
    assert kitchen.action == "failed"
    assert "в листе «ING» сдвинулись колонки" in kitchen.problem
    assert result.outcomes["ingredient_cards"].action == "imported"
    state = _state(sessions, "kitchen")
    assert state.checked_at == checked, "непроверенное не считается свежим"
    assert state.problem_since == clock.now
    assert _tomato_price(sessions) == "177.00", "книга со сдвигом не переносится"


def test_same_problem_is_journaled_once(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()
    _cycle(sessions, clock, kitchen={"ING": _shifted(kitchen_sheets()["ING"])}).run()
    since = clock.now
    clock.tick()

    _cycle(sessions, clock, kitchen={"ING": _shifted(kitchen_sheets()["ING"])}).run()

    assert _runs(sessions) == 2, "перенос и один сбой; повтор той же причины — не событие"
    assert _state(sessions, "kitchen").problem_since == since


def test_new_problem_is_a_new_event(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()
    _cycle(sessions, clock, kitchen={"ING": _shifted(kitchen_sheets()["ING"])}).run()
    clock.tick()

    result = _cycle(sessions, clock, missing=("ТТК",)).run()

    assert "листа «ТТК» нет в таблице" in result.outcomes["kitchen"].problem
    assert _runs(sessions) == 3
    assert _state(sessions, "kitchen").problem_since == clock.now


def test_recovery_clears_problem(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()
    _cycle(sessions, clock, kitchen={"ING": _shifted(kitchen_sheets()["ING"])}).run()
    clock.tick()

    result = _cycle(sessions, clock).run()

    assert result.outcomes["kitchen"].action == "unchanged"
    state = _state(sessions, "kitchen")
    assert (state.problem, state.problem_since) == (None, None)
    assert state.checked_at == clock.now


def test_older_read_does_not_overwrite_newer(sessions) -> None:
    """Пока мы читали, другой импорт перенёс чтение новее нашего."""
    clock = Clock()
    _cycle(sessions, clock).run()
    with sessions() as session, session.begin():
        session.get(models.SyncState, "kitchen").read_started_at = clock.now + timedelta(minutes=1)

    result = _cycle(sessions, clock, kitchen={"ING": _cheaper_tomato()}).run()

    assert result.outcomes["kitchen"].action == "stale"
    assert _tomato_price(sessions) == "177.00"


def test_force_imports_even_unchanged(sessions) -> None:
    """Ручной импорт переносит всегда, но время изменения двигает только изменение."""
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    result = _cycle(sessions, clock).run(force=True)

    assert {o.action for o in result.outcomes.values()} == {"imported"}
    assert _runs(sessions) == 2
    assert _state(sessions, "kitchen").changed_at == clock.now - timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Запись о сбое в журнале: причина словами шефа и исходный текст ошибки
# ---------------------------------------------------------------------------


def _failure_notes(sessions) -> list[str]:
    """Записи журнала о сбоях — по порядку."""
    query = select(models.SyncRun.note).where(models.SyncRun.ok.is_(False))
    with sessions() as session:
        return list(session.scalars(query.order_by(models.SyncRun.id)))


class ClosedSpreadsheet:
    """Таблица, к которой сервисному аккаунту закрыт доступ: gspread отвечает
    голым `PermissionError()`, без текста."""

    def worksheets(self) -> list[object]:
        raise PermissionError


def test_failure_note_keeps_raw_error(sessions) -> None:
    """Сайту — перевод, журналу — ещё и исходник: по одному «доступ закрыт» не
    отличить закрытый доступ от выключенного API, а чинить на бою — по журналу."""
    client = sheets_client()
    client._spreadsheets["kitchen-id"] = ClosedSpreadsheet()

    result = SyncCycle(SheetsReader(client, IDS), sessions, Clock()).run()

    kitchen = result.outcomes["kitchen"]
    assert kitchen.action == "failed"
    assert kitchen.problem.startswith("доступ платформы к таблице закрыт")
    assert kitchen.details == f"{BOOK_OPEN_FAILED}PermissionError"
    assert _failure_notes(sessions) == [
        f"kitchen: {kitchen.problem} — {BOOK_OPEN_FAILED}PermissionError"
    ]


def test_failure_note_does_not_repeat_the_problem(sessions) -> None:
    """Читатель уже сказал это словами шефа — исходник рядом не повторяем."""
    _cycle(sessions, Clock(), missing=("ТТК",)).run()

    assert _failure_notes(sessions) == ["kitchen: листа «ТТК» нет в таблице"]
