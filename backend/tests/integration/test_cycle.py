"""Цикл синхронизации на настоящем Postgres, без сети."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from google.auth.exceptions import RefreshError
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from kitchen.db import models
from kitchen.sync import specs
from kitchen.sync.cycle import SyncCycle
from kitchen.sync.importer import take_import_lock
from kitchen.sync.reader import BOOK_OPEN_FAILED, SheetsReader
from tests.fake_sheets import IDS, cards_sheet, kitchen_sheets, row, sheets_client

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.integration

ACCESS = "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"
OPEN_UNKNOWN = "не удалось открыть таблицу — причина не распознана — сообщите разработчику"
IMPORT_FAILED = "перенос в базу не удался на данных листа — сообщите разработчику"


class Clock:
    """Часы, которые идут только по команде."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, minutes: int = 5) -> None:
        self.now += timedelta(minutes=minutes)


class SlowReader(SheetsReader):
    """Чтение, за которое что-то успевает случиться: `meanwhile` вызывается,
    когда листы прочитаны, а цикл ещё не встал в очередь импорта."""

    def __init__(self, meanwhile: Callable[[], object], **sheets) -> None:
        super().__init__(sheets_client(**sheets), IDS)
        self._meanwhile = meanwhile

    def read_many(self, wanted):
        sheets = super().read_many(wanted)
        self._meanwhile()
        return sheets


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


def _tomato_supplier(sessions) -> str:
    """Поставщик в карточке ингредиента «Томаты»."""
    query = select(models.IngredientCard.supplier).where(models.IngredientCard.name == "Томаты")
    with sessions() as session:
        return session.scalar(query)


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
    read_started = clock.now

    # Чтение идёт минуту: иначе время начала чтения и время переноса совпали
    # бы, и тест не отличил бы одно от другого.
    result = SyncCycle(SlowReader(lambda: clock.tick(1)), sessions, clock).run()

    assert {b: o.action for b, o in result.outcomes.items()} == {
        "kitchen": "imported",
        "ingredient_cards": "imported",
    }
    kitchen = _state(sessions, "kitchen")
    after_read = read_started + timedelta(minutes=1)
    assert kitchen.read_started_at == read_started, "запомнено начало чтения, а не перенос"
    assert (kitchen.checked_at, kitchen.changed_at) == (after_read, after_read)
    assert kitchen.fingerprint is not None and len(kitchen.fingerprint) == 64
    assert kitchen.problem is None
    assert _runs(sessions) == 2, "перенос каждой книги — своё событие журнала"


def test_unchanged_sheets_write_only_checked_at(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    result = _cycle(sessions, clock).run()

    assert {o.action for o in result.outcomes.values()} == {"unchanged"}
    kitchen = _state(sessions, "kitchen")
    assert kitchen.checked_at == clock.now
    assert kitchen.changed_at == clock.now - timedelta(minutes=5)
    assert _runs(sessions) == 2, (
        "только переносы первого цикла; «ничего не изменилось» — не событие"
    )


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
    assert _tomato_supplier(sessions) == "Новый поставщик", "карточки в базе новые"
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

    assert _runs(sessions) == 3, "два переноса и один сбой; повтор той же причины — не событие"
    assert _state(sessions, "kitchen").problem_since == since


def test_new_problem_is_a_new_event(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()
    _cycle(sessions, clock, kitchen={"ING": _shifted(kitchen_sheets()["ING"])}).run()
    clock.tick()

    result = _cycle(sessions, clock, missing=("ТТК",)).run()

    assert "листа «ТТК» нет в таблице" in result.outcomes["kitchen"].problem
    assert _runs(sessions) == 4, "два переноса и два разных сбоя"
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
    assert _runs(sessions) == 4, "два цикла, в каждом — перенос обеих книг"
    assert _state(sessions, "kitchen").changed_at == clock.now - timedelta(minutes=5)


def test_force_does_not_import_broken_book(sessions) -> None:
    """Ручной импорт переносит и без изменений, но не книгу со сдвигом колонок:
    колонки читаются по позиции, и цена приехала бы из чужой колонки (спека 4.4)."""
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    result = _cycle(sessions, clock, kitchen={"ING": _shifted(_cheaper_tomato())}).run(force=True)

    assert result.outcomes["kitchen"].action == "failed"
    assert _tomato_price(sessions) == "177.00"


# ---------------------------------------------------------------------------
# Гонка с ручным импортом — без потоков: чужой цикл проходит, пока идёт наше
# чтение (спека 4.3)
# ---------------------------------------------------------------------------


def test_import_during_our_read_wins(sessions) -> None:
    """Пока воркер читал, ручной импорт прочитал лист новее и перенёс его.
    Чтение воркера старше: перенеси он его — в базу вернулась бы старая цена."""
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    def manual_import() -> None:
        clock.tick(1)
        _cycle(sessions, clock, kitchen={"ING": _cheaper_tomato()}).run(force=True)

    result = SyncCycle(SlowReader(manual_import), sessions, clock).run()

    assert result.outcomes["kitchen"].action == "stale"
    assert _tomato_price(sessions) == "170.00", "в базе — чтение новее"


def test_failed_read_older_than_import_is_not_a_problem(sessions) -> None:
    """Наше чтение упало, а ручной импорт тем временем прочитал книгу позже и
    перенёс. Сбой старого чтения — уже не правда о книге: ни причины на сайт,
    ни события в журнал."""
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    def manual_import() -> None:
        clock.tick(1)
        _cycle(sessions, clock).run(force=True)

    reader = SlowReader(manual_import, kitchen={"ING": _shifted(kitchen_sheets()["ING"])})
    result = SyncCycle(reader, sessions, clock).run()

    assert result.outcomes["kitchen"].action == "stale"
    state = _state(sessions, "kitchen")
    assert (state.problem, state.problem_since) == (None, None)
    assert _runs(sessions) == 4, "два цикла по два переноса; сбоя старого чтения в журнале нет"


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
    raw = f"{BOOK_OPEN_FAILED}PermissionError"
    assert (kitchen.action, kitchen.problem, kitchen.details) == ("failed", ACCESS, raw)
    assert _state(sessions, "kitchen").problem == ACCESS, "на сайт — только перевод"
    assert _failure_notes(sessions) == [f"kitchen: {ACCESS} — {raw}"]


def test_failure_note_does_not_repeat_the_problem(sessions) -> None:
    """Читатель уже сказал это словами шефа — исходник рядом не повторяем."""
    _cycle(sessions, Clock(), missing=("ТТК",)).run()

    assert _failure_notes(sessions) == ["kitchen: листа «ТТК» нет в таблице"]


class RevokedKey:
    """Ключ сервисного аккаунта отозван: google-auth отвечает `RefreshError` с
    ответом сервера внутри. Перевода у этого отказа нет."""

    def worksheets(self) -> list[object]:
        raise RefreshError(
            "invalid_grant: Invalid JWT Signature.",
            {"error": "invalid_grant", "error_description": "Invalid JWT Signature."},
        )


def test_unknown_failure_goes_to_journal_not_to_site(sessions) -> None:
    """Непереведённый отказ: на сайт — постоянная фраза, исходник — в журнал.

    Причину видит каждый вошедший на каждом экране; имя класса и ответ
    сервера ему ничего не скажут, а чинят по журналу."""
    client = sheets_client()
    client._spreadsheets["kitchen-id"] = RevokedKey()

    result = SyncCycle(SheetsReader(client, IDS), sessions, Clock()).run()

    kitchen = result.outcomes["kitchen"]
    raw = (
        f"{BOOK_OPEN_FAILED}RefreshError: ('invalid_grant: Invalid JWT Signature.', "
        "{'error': 'invalid_grant', 'error_description': 'Invalid JWT Signature.'})"
    )
    assert (kitchen.action, kitchen.problem, kitchen.details) == ("failed", OPEN_UNKNOWN, raw)
    assert _state(sessions, "kitchen").problem == OPEN_UNKNOWN, "на сайт — без сырого текста"
    assert _failure_notes(sessions) == [f"kitchen: {OPEN_UNKNOWN} — {raw}"]


# ---------------------------------------------------------------------------
# Пары карточек зависят и от справочника: перенос одной кухни их переподбирает
# ---------------------------------------------------------------------------


def _card(sessions, name: str) -> models.IngredientCard:
    query = select(models.IngredientCard).where(models.IngredientCard.name == name)
    with sessions() as session:
        return session.scalar(query)


def _ingredient_id(sessions, legacy_id: str) -> int:
    query = select(models.Ingredient.id).where(models.Ingredient.legacy_id == legacy_id)
    with sessions() as session:
        return session.scalar(query)


def _ing_without(*legacy_ids: str) -> list[list[str]]:
    """Лист ING, из которого шеф убрал строки с этими id."""
    ing = kitchen_sheets()["ING"]
    return [ing[0], *(line for line in ing[1:] if line[0] not in legacy_ids)]


def _import_notes(sessions) -> list[str]:
    """Записи журнала о переносах — по порядку."""
    query = select(models.SyncRun.note).where(models.SyncRun.ok.is_(True))
    with sessions() as session:
        return list(session.scalars(query.order_by(models.SyncRun.id)))


def test_kitchen_only_change_repairs_card_links(sessions) -> None:
    """Шеф убрал из ING лишний «Сахар» (id=3), чтобы снять спор, и «Томаты»
    (id=1). Лист карточек не менялся — его заполнял бот, а боты лежат. Пары
    карточек зависят и от справочника: спор снят, а пары к скрытой позиции нет
    (уточнение 4 спеки) — без правки листа карточек."""
    clock = Clock()
    _cycle(sessions, clock).run()
    assert _card(sessions, "Сахар").link_status == "ambiguous", "два активных тёзки"
    assert _card(sessions, "Томаты").link_status == "linked"
    clock.tick()

    result = _cycle(sessions, clock, kitchen={"ING": _ing_without("1", "3")}).run()

    assert result.outcomes["kitchen"].action == "imported"
    assert result.outcomes["ingredient_cards"].action == "unchanged"
    sugar = _card(sessions, "Сахар")
    assert (sugar.link_status, sugar.ingredient_id) == ("linked", _ingredient_id(sessions, "2"))
    tomato = _card(sessions, "Томаты")
    assert tomato.link_status != "linked"
    assert tomato.ingredient_id is None, "к скрытой позиции пары нет"
    note = _import_notes(sessions)[-1]
    assert "Карточки: получили пару в справочнике — 1" in note, "перемена пар — в журнале"
    assert "Карточки: потеряли пару в справочнике — 1" in note


def test_link_note_is_not_lost_among_other_warnings(sessions) -> None:
    """Перемена пар доходит до журнала и среди шума: в живом ING десятки строк
    без id, на каждую — замечание, а note прогона обрезается до двадцати
    записей. Массовая потеря пар — единственный её след в журнале."""
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()
    blanks = [row(specs.INGREDIENTS, name="Заготовка") for _ in range(25)]

    _cycle(sessions, clock, kitchen={"ING": [*_ing_without("1"), *blanks]}).run()

    assert "Карточки: потеряли пару в справочнике — 1" in _import_notes(sessions)[-1]


def test_kitchen_only_change_keeps_confirmed_link(sessions) -> None:
    """Переподбор идёт мимо пары, подтверждённой на сверке: это решение
    человека, а не предложение кода — даже когда позиция ушла из справочника."""
    clock = Clock()
    _cycle(sessions, clock).run()
    zero_sugar = _ingredient_id(sessions, "3")
    with sessions() as session, session.begin():
        card = session.scalar(
            select(models.IngredientCard).where(models.IngredientCard.name == "Сахар")
        )
        card.ingredient_id = zero_sugar
        card.link_status = "linked"
        card.link_confirmed_at = clock.now
    clock.tick()

    result = _cycle(sessions, clock, kitchen={"ING": _ing_without("1", "3")}).run()

    assert result.outcomes["ingredient_cards"].action == "unchanged"
    assert _card(sessions, "Томаты").ingredient_id is None, "неподтверждённые переподобраны"
    sugar = _card(sessions, "Сахар")
    assert (sugar.link_status, sugar.ingredient_id) == ("linked", zero_sugar)


def test_kitchen_only_change_links_new_ingredient(sessions) -> None:
    """Шеф завёл в ING позицию, которой не хватало карточке, — пара находится
    без правки листа карточек."""
    clock = Clock()
    _cycle(sessions, clock).run()
    assert _card(sessions, "Пастрами из индейки").link_status == "orphan"
    ing = kitchen_sheets()["ING"]
    ing.append(
        row(
            specs.INGREDIENTS,
            id="4",
            name="Пастрами из индейки",
            price_per_kg="900",
            status="активное",
        )
    )
    clock.tick()

    result = _cycle(sessions, clock, kitchen={"ING": ing}).run()

    assert result.outcomes["ingredient_cards"].action == "unchanged"
    pastrami = _card(sessions, "Пастрами из индейки")
    assert (pastrami.link_status, pastrami.ingredient_id) == (
        "linked",
        _ingredient_id(sessions, "4"),
    )


# ---------------------------------------------------------------------------
# База отвергла данные листа: книга не переносится, другая — переносится
# ---------------------------------------------------------------------------


def _losses_150() -> list[list[str]]:
    """ING, где в колонке «Общие потери, %» стоит «150» без знака процента.

    Без «%» читатель считает, что в ячейке уже доля (так Google хранит
    процентный формат), — 150, а доле в базе отведено меньше 100: numeric field
    overflow. Такую ячейку может оставить шеф, и сбой повторяется каждые пять
    минут, пока её не исправят.
    """
    ing = kitchen_sheets()["ING"]
    ing[1] = row(
        specs.INGREDIENTS,
        id="1",
        name="Томаты",
        price_per_kg="170",
        losses_total="150",
        status="активное",
    )
    return ing


def test_failed_import_of_one_book_spares_the_other(sessions) -> None:
    """Перенос кухни упал в базе — откатывается только кухня: карточки
    переносятся в той же транзакции, причина видна на сайте, исходник — в
    журнале (спека: причину видят все; сбой одной книги не мешает другой)."""
    clock = Clock()
    _cycle(sessions, clock).run()
    checked = clock.now
    clock.tick()
    cards = cards_sheet()
    cards[2] = row(specs.INGREDIENT_CARDS, name="Томаты", supplier="Новый поставщик")

    result = _cycle(sessions, clock, kitchen={"ING": _losses_150()}, cards=cards).run()

    kitchen = result.outcomes["kitchen"]
    assert (kitchen.action, kitchen.problem) == ("failed", IMPORT_FAILED)
    assert kitchen.details is not None
    assert "numeric field overflow" in kitchen.details
    assert _tomato_price(sessions) == "177.00", "кухня откатилась целиком, а не наполовину"
    state = _state(sessions, "kitchen")
    assert (state.problem, state.problem_since) == (IMPORT_FAILED, clock.now)
    assert state.checked_at == checked, "непереносимое не считается свежим"
    [note] = _failure_notes(sessions)
    assert note.startswith(f"kitchen: {IMPORT_FAILED} — DataError: ")
    assert "numeric field overflow" in note, "исходник — в журнал: по нему чинят"
    # После отката точки сохранения транзакция жива: карточки перенесены,
    # причина кухни и её событие журнала записаны — всё одним коммитом.
    assert result.outcomes["ingredient_cards"].action == "imported"
    assert _tomato_supplier(sessions) == "Новый поставщик"
    assert _state(sessions, "ingredient_cards").checked_at == clock.now

    clock.tick()
    _cycle(sessions, clock, kitchen={"ING": _losses_150()}, cards=cards).run()

    assert len(_failure_notes(sessions)) == 1, "повтор той же беды — не событие"


# ---------------------------------------------------------------------------
# Цикл не ждёт вечно
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("limits", "sqlstate"),
    [
        ({"lock_timeout": timedelta(seconds=1)}, "55P03"),  # lock_not_available
        ({"statement_timeout": timedelta(seconds=1)}, "57014"),  # query_canceled
    ],
    ids=["lock_timeout", "statement_timeout"],
)
def test_cycle_behind_foreign_lock_gives_up(sessions, limits, sqlstate) -> None:
    """Чужой импорт занял очередь и не отпускает — цикл падает по пределу
    ожидания и повторится через пять минут, а не висит: зависший воркер смоук
    не видит, контейнер-то работает. По умолчанию предел — минута, здесь —
    секунда."""
    holder = sessions()
    holder.begin()
    take_import_lock(holder)
    errors: list[Exception] = []

    def cycle() -> None:
        try:
            SyncCycle(SheetsReader(sheets_client(), IDS), sessions, Clock(), **limits).run()
        except Exception as error:  # ошибку потока показываем в утверждении
            errors.append(error)

    thread = threading.Thread(target=cycle, daemon=True)
    try:
        thread.start()
        thread.join(timeout=15)
        hung = thread.is_alive()
    finally:
        # Отпускаем очередь в любом случае: иначе и повисший цикл, и downgrade
        # в фикстуре ждали бы нас вечно.
        holder.rollback()
        holder.close()
    thread.join(timeout=30)

    assert not hung, "цикл за чужой блокировкой висит"
    [error] = errors
    assert isinstance(error, OperationalError), error
    assert error.orig.sqlstate == sqlstate
