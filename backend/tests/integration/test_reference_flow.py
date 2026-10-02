"""«Сверка» на настоящей базе: «Это он», предпросмотр строки и перенос в справочник.

Лист — фальшивые таблицы (``tests/conftest.py``): книга кухни с листом ING,
устроенным как настоящий, и прочими листами кухни; книга карточек с порядком
«Да». Писатель строки, журнал записей и перенос книг в базу — настоящие, на
Postgres.

Главное, что стерегут эти тесты:

* «Это он» подтверждает пару только с кандидатом карточки — и подтверждённая
  пара переживает переимпорт;
* перенос в справочник — запись, перенос книги кухни в базу и подтверждение
  пары с новым ингредиентом; перенос в базу упал — ответ всё равно удачный,
  ``imported=false``;
* id, который уже стоит в строке, подтверждает пару, только если в базе он
  того же ингредиента: при сдвиге строк ING рядом с нашим названием может
  стоять чужой id;
* предпросмотр — свежее чтение без записи: строка, следующий id, формулы,
  подтянутое и что сейчас в ручных ячейках.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from kitchen.cards.reference import (
    CARD_NOT_FOUND,
    NOT_A_CANDIDATE,
    NOT_CONFIGURED,
    NOT_IMPORTED,
    PAIR_NOT_CONFIRMED,
    REFERENCE_BUSY,
    WRITING_CLOSED,
    ReferenceCardNotFoundError,
    ReferenceConflictError,
    ReferenceFailedError,
    ReferenceFormInvalidError,
    ReferenceUnavailableError,
    confirm_pair,
    preview,
    to_reference,
)
from kitchen.cards.submit import IMPORT_LOCK_WAIT
from kitchen.db import models
from kitchen.db.journal import VERIFIED, DbJournal
from kitchen.db.links import card_candidates, known_reference_ids
from kitchen.domain.cards import APPROVED, REJECTED
from kitchen.domain.reference_row import NotFound, ReferenceFormError
from kitchen.sync import specs
from kitchen.sync.cycle import BookOutcome, CycleResult, SyncCycle
from kitchen.sync.importer import take_import_lock
from kitchen.sync.ownership import ForbiddenWriteError
from kitchen.sync.reader import SheetsReader
from kitchen.sync.reference_writer import (
    ALREADY,
    BUSY,
    HEADER_DRIFT,
    NOT_CONFIRMED,
    REASON_FORMULA,
    ReferenceRowFiller,
    RowRefusedError,
    SheetLayoutError,
)
from kitchen.sync.sheet_write import (
    SheetBusyError,
    SheetUnavailableError,
    WriteNotConfirmedError,
)
from kitchen.web.cards import status_for
from tests.conftest import (
    CARDS_ORDER,
    FakeSheetsClient,
    FakeSpreadsheet,
    FakeWorksheet,
    Formula,
    IngLine,
    cards_book_sheet,
    ing_sheet,
)
from tests.fake_sheets import IDS, cards_sheet, kitchen_sheets, row, sheets_client

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

FORM = {"short_name": "Соус", "unit": "кг", "price_per_pack": "1250,5", "losses_unpacking": "5"}
"""Форма переноса: короткое имя, единица, цена упаковки, потери 5 %."""

SHORT_WAIT = timedelta(seconds=1)


# ---------------------------------------------------------------------------
# Таблицы и база
# ---------------------------------------------------------------------------
def _client(
    *, ing: FakeWorksheet | None = None, cards: Sequence[tuple[str, str]] = CARDS_ORDER
) -> FakeSheetsClient:
    """Книга кухни — лист ING как настоящий и прочие листы кухни, — и книга
    карточек с порядком «Да» (``tests.conftest``)."""
    kitchen = {
        title: FakeWorksheet(values, title)
        for title, values in kitchen_sheets().items()
        if title != "ING"
    }
    kitchen["ING"] = ing if ing is not None else ing_sheet()
    return FakeSheetsClient(
        {
            IDS["kitchen"]: FakeSpreadsheet(kitchen),
            IDS["ingredient_cards"]: FakeSpreadsheet({"Лист1": cards_book_sheet(cards)}),
        }
    )


def _ing(client: FakeSheetsClient) -> FakeWorksheet:
    return client._spreadsheets[IDS["kitchen"]]._sheets["ING"]


def _writes(client: FakeSheetsClient) -> list[object]:
    """Тела запросов записи в книгу кухни."""
    return [
        body for method, body in client._spreadsheets[IDS["kitchen"]].calls if "update" in method
    ]


def _cycle(client: FakeSheetsClient, sessions: sessionmaker[Session]) -> SyncCycle:
    """Перенос книг в базу — как после отправки карточки: короткое ожидание замка."""
    return SyncCycle(SheetsReader(client, IDS), sessions, lock_timeout=IMPORT_LOCK_WAIT)


def _broken_cycle(sessions: sessionmaker[Session]) -> SyncCycle:
    """Перенос, который не может открыть ни одну книгу."""
    return SyncCycle(SheetsReader(FakeSheetsClient({}), IDS), sessions)


def _filler(client: FakeSheetsClient, sessions: sessionmaker[Session]) -> ReferenceRowFiller:
    return ReferenceRowFiller(
        client,
        DbJournal(sessions),
        kitchen_id=IDS["kitchen"],
        cards_id=IDS["ingredient_cards"],
        known_ids=lambda: known_reference_ids(sessions),
    )


def _import(client: FakeSheetsClient, sessions: sessionmaker[Session]) -> None:
    result = _cycle(client, sessions).run(force=True)
    assert {book: outcome.action for book, outcome in result.outcomes.items()} == {
        "kitchen": "imported",
        "ingredient_cards": "imported",
    }


def _card(sessions: sessionmaker[Session], name: str) -> models.IngredientCard:
    with sessions() as session:
        card = session.scalar(
            select(models.IngredientCard).where(models.IngredientCard.name == name)
        )
        assert card is not None, f"карточки «{name}» нет в базе"
        return card


def _ingredient(sessions: sessionmaker[Session], legacy_id: str) -> models.Ingredient | None:
    with sessions() as session:
        return session.scalar(
            select(models.Ingredient).where(models.Ingredient.legacy_id == legacy_id)
        )


def _ingredient_id(sessions: sessionmaker[Session], legacy_id: str) -> int:
    found = _ingredient(sessions, legacy_id)
    assert found is not None, f"ингредиента id {legacy_id} нет в базе"
    return found.id


def _profile(sessions: sessionmaker[Session]) -> uuid.UUID:
    profile_id = uuid.uuid4()
    with sessions() as session, session.begin():
        session.add(models.Profile(id=profile_id, email="chef@example.test", display_name="Шеф"))
    return profile_id


def _pair(card: models.IngredientCard) -> tuple[int | None, str, bool]:
    return card.ingredient_id, card.link_status, card.link_confirmed_at is not None


@pytest.fixture
def client(sessions: sessionmaker[Session]) -> FakeSheetsClient:
    """Таблицы, перенесённые в базу: справочник — Лук (1), Сахар (99), Кетчуп
    (129), Моцарелла (130); у карточек Кетчуп, Моцарелла и Сахар пары
    предложены импортом, остальные — без пары."""
    client = _client()
    _import(client, sessions)
    return client


@pytest.fixture
def disputed(sessions: sessionmaker[Session]) -> FakeSheetsClient:
    """Спорные пары: «Сахар» — два активных тёзки (id 2 и 3), «Томат» похож
    на «Томаты» (id 1), у «Пастрами из индейки» пары нет, «Томаты» связаны."""
    client = sheets_client(cards=[*cards_sheet(), row(specs.INGREDIENT_CARDS, name="Томат")])
    result = _cycle(client, sessions).run(force=True)
    assert {outcome.action for outcome in result.outcomes.values()} == {"imported"}
    return client


# ---------------------------------------------------------------------------
# Кандидаты и «Это он»
# ---------------------------------------------------------------------------
def test_candidates_of_disputed_cards(sessions, disputed) -> None:
    """Тёзкам — тёзки, похожим — похожие, самые похожие первыми; связанной и
    карточке без пары выбирать не из чего."""
    with sessions() as session:
        cards = session.scalars(select(models.IngredientCard)).all()
        found = card_candidates(session, cards)
        by_name = {card.name: found[card.id] for card in cards}

    assert [(c.legacy_id, c.name, c.score) for c in by_name["Сахар"]] == [
        ("2", "Сахар", None),
        ("3", "Сахар", None),
    ]
    [tomatoes] = by_name["Томат"]
    assert (tomatoes.legacy_id, tomatoes.name) == ("1", "Томаты")
    assert tomatoes.ingredient_id == _ingredient_id(sessions, "1")
    assert tomatoes.score is not None and 0.82 <= tomatoes.score < 1
    assert by_name["Пастрами из индейки"] == ()
    assert by_name["Томаты"] == ()


@pytest.mark.parametrize(("name", "legacy_id"), [("Сахар", "3"), ("Томат", "1")])
def test_this_is_it_confirms_the_pair_and_it_survives_reimport(
    sessions, disputed, name: str, legacy_id: str
) -> None:
    """«Это он»: пара подтверждена только в базе — таблица не тронута, — и
    переимпорт её не пересчитывает: без подтверждения «Сахар» снова стал бы
    спорным, а «Томат» — снова кандидатом."""
    card = _card(sessions, name)
    chosen = _ingredient_id(sessions, legacy_id)
    assert card.link_status in ("ambiguous", "candidate")

    with sessions() as session:
        result = confirm_pair(session, card.id, chosen)

    assert (result.card_id, result.ingredient_id, result.legacy_id) == (card.id, chosen, legacy_id)
    assert result.already is False
    confirmed = _card(sessions, name)
    assert _pair(confirmed) == (chosen, "linked", True)
    assert _writes(disputed) == []

    _cycle(disputed, sessions).run(force=True)

    again = _card(sessions, name)
    assert _pair(again) == (chosen, "linked", True)
    assert again.link_confirmed_at == confirmed.link_confirmed_at


def test_not_a_candidate_is_refused(sessions, disputed) -> None:
    """Ингредиент не из кандидатов карточки — отказ, и карточка не меняется:
    у «Сахара» выбирать можно только из тёзок, у карточки без пары — не из
    чего."""
    sugar = _card(sessions, "Сахар")
    pastrami = _card(sessions, "Пастрами из индейки")
    tomatoes = _ingredient_id(sessions, "1")

    for card in (sugar, pastrami):
        with sessions() as session, pytest.raises(ReferenceConflictError) as caught:
            confirm_pair(session, card.id, tomatoes)
        assert str(caught.value) == NOT_A_CANDIDATE
        assert caught.value.extra() == {"reason": "not_candidate", "row": None}
        assert status_for(type(caught.value)) == 409

    assert _pair(_card(sessions, "Сахар")) == (None, "ambiguous", False)
    assert _pair(_card(sessions, "Пастрами из индейки")) == (None, "orphan", False)


def test_this_is_it_twice_answers_already(sessions, disputed) -> None:
    """Двойное нажатие: второй запрос видит пару подтверждённой этим же
    ингредиентом — ответ «уже», а не отказ «не кандидат»."""
    card = _card(sessions, "Сахар")
    chosen = _ingredient_id(sessions, "2")
    with sessions() as session:
        confirm_pair(session, card.id, chosen)
    first = _card(sessions, "Сахар").link_confirmed_at

    with sessions() as session:
        again = confirm_pair(session, card.id, chosen)

    assert (again.ingredient_id, again.legacy_id, again.already) == (chosen, "2", True)
    assert _card(sessions, "Сахар").link_confirmed_at == first


def test_this_is_it_waits_for_the_import_and_gives_up(sessions, disputed) -> None:
    """Подтверждение стоит в очереди импорта: импорт, начатый раньше и
    записавший позже, вернул бы карточке «спорную» пару поверх подтверждённой.
    Импорт держит очередь дольше ожидания — «попробуйте ещё раз», карточка не
    тронута."""
    card = _card(sessions, "Сахар")
    importer = sessions()
    importer.begin()
    take_import_lock(importer)
    try:
        with sessions() as session, pytest.raises(ReferenceUnavailableError) as caught:
            confirm_pair(session, card.id, _ingredient_id(sessions, "2"), wait=SHORT_WAIT)
    finally:
        importer.rollback()
        importer.close()

    assert str(caught.value) == REFERENCE_BUSY
    assert status_for(type(caught.value)) == 503
    assert _pair(_card(sessions, "Сахар")) == (None, "ambiguous", False)


def test_this_is_it_on_a_pair_confirmed_with_another_is_refused(sessions, disputed) -> None:
    """Обратный случай: пару уже подтвердили с другим ингредиентом — другой
    человек нажал «Это он» или перенёс карточку, а здесь список устарел. 409
    «обновите страницу», пара не тронута."""
    card = _card(sessions, "Сахар")
    first = _ingredient_id(sessions, "2")
    with sessions() as session:
        confirm_pair(session, card.id, first)

    with sessions() as session, pytest.raises(ReferenceConflictError) as caught:
        confirm_pair(session, card.id, _ingredient_id(sessions, "3"))

    assert str(caught.value) == "Пара уже подтверждена — обновите страницу"
    assert caught.value.extra() == {"reason": "confirmed", "row": None}
    assert status_for(type(caught.value)) == 409
    assert _pair(_card(sessions, "Сахар")) == (first, "linked", True)


def test_unknown_card_is_not_found(sessions, client) -> None:
    """Карточки нет (или её убрали из листа) — 404 у всех трёх действий."""
    filler = _filler(client, sessions)
    opened = len(client.opened)
    with sessions() as session:
        for action in (
            lambda: confirm_pair(session, 999_999, 1),
            lambda: preview(session, filler, 999_999),
            lambda: to_reference(
                session, filler, _cycle(client, sessions), 999_999, FORM, actor_id=None
            ),
        ):
            with pytest.raises(ReferenceCardNotFoundError) as caught:
                action()
            assert str(caught.value) == CARD_NOT_FOUND
            assert status_for(type(caught.value)) == 404
    assert len(client.opened) == opened


# ---------------------------------------------------------------------------
# Предпросмотр
# ---------------------------------------------------------------------------
def test_preview_of_a_row_waiting_for_transfer(sessions, client) -> None:
    """Строка найдена и ждёт переноса: следующий id, L — формула шефа (поле
    «считается в таблице»), подтянутое ``QUERY`` и умолчания заготовки. Потери
    — долями из FORMULA-чтения: 12,5 % в ячейке с форматом «0%» шеф видит как
    «13%», а форма начнётся с точных 12,5 %. Без единой записи."""
    _ing(client).put("R7", 0.125)
    card = _card(sessions, "Соус Сырный")

    with sessions() as session:
        seen = preview(session, _filler(client, sessions), card.id)

    assert (seen.row, seen.ready, seen.reason, seen.message) == (7, True, None, None)
    assert (seen.next_id, seen.ref_id, seen.formulas) == ("131", None, ("L", "P"))
    assert seen.pulled == {
        "category": "Соусы",
        "name": "Соус Сырный",
        "full_name": "Соус Сырный, полное наименование",
        "manufacturer": "Завод",
        "composition": "по ТУ",
        "protein": Decimal("1.5"),
        "fat": Decimal("12.5"),
        "carbs": Decimal("3"),
        "kcal": Decimal("150"),
    }
    assert seen.current == {
        "short_name": "",
        "price_per_kg": Decimal("0"),
        "price_per_pack": Decimal("0"),
        "unit": "кг",
        "weight_per_piece_g": None,
        "losses_unpacking": Decimal("0"),
        "losses_cutting": Decimal("0.125"),
        "losses_thermal": Decimal("0"),
        "status": "активный",
    }
    assert _writes(client) == []


def test_preview_of_a_row_already_in_reference(sessions, client) -> None:
    """id в строке уже стоит: форма не нужна — что в строке сейчас."""
    card = _card(sessions, "Кетчуп")

    with sessions() as session:
        seen = preview(session, _filler(client, sessions), card.id)

    assert (seen.row, seen.ready, seen.reason, seen.ref_id) == (4, False, ALREADY, "129")
    assert seen.message == "Ингредиент уже в справочнике — id 129"
    assert seen.formulas == ("P",)
    assert seen.pulled["name"] == "Кетчуп"
    assert seen.current == {
        "short_name": "Кетчуп",
        "price_per_kg": Decimal("250"),
        "price_per_pack": Decimal("1250"),
        "unit": "кг",
        "weight_per_piece_g": None,
        "losses_unpacking": Decimal("0.05"),
        "losses_cutting": Decimal("0"),
        "losses_thermal": Decimal("0"),
        "status": "активный",
    }


def test_preview_says_why_there_is_no_row(sessions, client) -> None:
    """Строки нет — почему, словами спеки; следующий id всё равно виден."""
    card = _card(sessions, "Майонез")

    with sessions() as session:
        seen = preview(session, _filler(client, sessions), card.id)

    assert (seen.row, seen.ready, seen.reason) == (None, False, "not_approved")
    assert seen.message == NotFound.NOT_APPROVED.message
    assert (seen.next_id, seen.formulas, seen.pulled, seen.current) == ("131", (), {}, {})


def test_preview_says_why_the_row_cannot_be_filled(sessions, client) -> None:
    """Строка найдена, но писатель её откажет — предпросмотр говорит это до
    того, как человек заполнит форму."""
    _ing(client).put("E6", Formula("=C6", "Соус Барбекю"))
    card = _card(sessions, "Соус Барбекю")

    with sessions() as session:
        seen = preview(session, _filler(client, sessions), card.id)

    assert (seen.row, seen.ready, seen.reason) == (6, False, REASON_FORMULA)
    assert seen.message is not None and "в ручной ячейке E — формула" in seen.message
    assert seen.formulas == ("E", "P")
    assert seen.current["short_name"] == "Соус Барбекю"


# ---------------------------------------------------------------------------
# Перенос в справочник
# ---------------------------------------------------------------------------
def test_transfer_writes_imports_and_confirms_the_pair(sessions, client) -> None:
    """«Добавить в справочник» у «Сахара» из зоны QUERY: строка 8 заполнена,
    книга кухни перенесена — ингредиент 131 на сайте, пара подтверждена. Без
    подтверждения перенос сделал бы карточку спорной: «Сахар» теперь два —
    старый (99) и новый (131) — и переимпорт держит пару."""
    actor = _profile(sessions)
    card = _card(sessions, "Сахар")
    assert _pair(card) == (_ingredient_id(sessions, "99"), "linked", False)

    with sessions() as session:
        done = to_reference(
            session,
            _filler(client, sessions),
            _cycle(client, sessions),
            card.id,
            FORM,
            actor_id=actor,
        )

    assert (done.row, done.ref_id, done.already) == (8, "131", False)
    assert (done.imported, done.linked) == (True, True)
    assert done.message == "Записано в справочник: строка 8, id 131"
    assert (done.shifted, done.notes) == (None, ())
    new = _ingredient(sessions, "131")
    assert new is not None
    assert (new.name, new.short_name, new.unit, new.status) == ("Сахар", "Соус", "кг", "активный")
    assert (new.price_per_pack, new.losses_unpacking) == (Decimal("1250.50"), Decimal("0.05"))
    assert done.ingredient_id == new.id
    assert _pair(_card(sessions, "Сахар")) == (new.id, "linked", True)
    with sessions() as session:
        [write] = session.scalars(select(models.SheetWrite)).all()
    assert (write.request_key, write.action, write.status) == (
        f"ing-fill:{card.id}",
        "fill",
        VERIFIED,
    )
    assert write.actor_id == actor

    _cycle(client, sessions).run(force=True)

    assert _pair(_card(sessions, "Сахар")) == (new.id, "linked", True)


def test_import_failure_is_success_with_imported_false(sessions, client) -> None:
    """Перенос в базу не удался — строка уже в листе: ответ удачный,
    ``imported=false``, пару подтвердить не с чем. Повторное нажатие — без
    второй записи (журнал), с переносом и подтверждением пары."""
    card = _card(sessions, "Соус Барбекю")
    filler = _filler(client, sessions)

    with sessions() as session:
        done = to_reference(session, filler, _broken_cycle(sessions), card.id, FORM, actor_id=None)

    assert (done.row, done.ref_id, done.already) == (6, "131", False)
    assert (done.imported, done.linked, done.ingredient_id) == (False, False, None)
    assert done.notes == (NOT_IMPORTED,)
    assert _ing(client).cell("A6") == 131
    assert _ingredient(sessions, "131") is None
    assert _pair(_card(sessions, "Соус Барбекю")) == (None, "orphan", False)

    with sessions() as session:
        again = to_reference(
            session, filler, _cycle(client, sessions), card.id, FORM, actor_id=None
        )

    assert (again.row, again.ref_id, again.already) == (6, "131", True)
    assert (again.imported, again.linked, again.notes) == (True, True, ())
    assert len(_writes(client)) == 1
    assert _pair(_card(sessions, "Соус Барбекю")) == (
        _ingredient_id(sessions, "131"),
        "linked",
        True,
    )


def test_row_already_filled_confirms_the_pair_without_writing(sessions, client) -> None:
    """id в строке уже стоит, и в базе он того же ингредиента: «уже в
    справочнике», пара подтверждена, в лист — ни одной записи."""
    card = _card(sessions, "Кетчуп")
    ketchup = _ingredient_id(sessions, "129")
    assert _pair(card) == (ketchup, "linked", False)

    with sessions() as session:
        done = to_reference(
            session,
            _filler(client, sessions),
            _cycle(client, sessions),
            card.id,
            FORM,
            actor_id=None,
        )

    assert (done.row, done.ref_id, done.already, done.imported, done.linked) == (
        4,
        "129",
        True,
        True,
        True,
    )
    assert done.message == "Ингредиент уже в справочнике — id 129"
    assert _writes(client) == []
    assert _pair(_card(sessions, "Кетчуп")) == (ketchup, "linked", True)


def test_id_of_another_ingredient_in_the_row_is_refused_as_shifted(sessions, client) -> None:
    """Шеф согласовал «Горчицу», которая в книге карточек выше «Кетчупа»: вывод
    QUERY съехал на строку вниз, а ручные колонки остались. В строке
    «Горчицы» — id 129, в базе это «Кетчуп». Подтвердить такую пару нельзя —
    «сдвинуты», и перенос книги кухни в базу не начинается: он переименовал
    бы 129 в «Горчицу», и проверка названия прошла бы."""
    shifted = _client(
        ing=ing_sheet(
            (
                IngLine("Горчица", ref_id=129),
                IngLine("Кетчуп", ref_id=130, l_formula=True),
                IngLine("Моцарелла", category="Сыры"),
                IngLine("Соус Барбекю"),
                IngLine("Соус Сырный", l_formula=True),
                IngLine("Сахар", category="Бакалея"),
            )
        ),
        cards=[("Горчица", APPROVED), *CARDS_ORDER[:-1]],
    )
    card = _card(sessions, "Горчица")

    with sessions() as session, pytest.raises(ReferenceConflictError) as caught:
        to_reference(
            session,
            _filler(shifted, sessions),
            _cycle(shifted, sessions),
            card.id,
            FORM,
            actor_id=None,
        )

    assert str(caught.value) == NotFound.SHIFTED.message
    assert caught.value.extra() == {"reason": "shifted", "row": 4}
    assert _pair(_card(sessions, "Горчица")) == (None, "orphan", False)
    ketchup = _ingredient(sessions, "129")
    assert ketchup is not None and ketchup.name == "Кетчуп"
    assert _writes(shifted) == []


def _confirm(sessions: sessionmaker[Session], name: str, legacy_id: str) -> int:
    """Человек подтвердил пару карточки ``name`` с ингредиентом ``legacy_id``
    — «Это он» у другого человека, мимо этого теста."""
    ingredient_id = _ingredient_id(sessions, legacy_id)
    with sessions.begin() as session:
        session.execute(
            update(models.IngredientCard)
            .where(models.IngredientCard.name == name)
            .values(ingredient_id=ingredient_id, link_status="linked", link_confirmed_at=func.now())
        )
    return ingredient_id


def test_transfer_of_a_confirmed_pair_is_refused(sessions, client) -> None:
    """Два человека на «Сверке»: один нажал «Это он» — пара «Сахар — 99»
    подтверждена; у другого список устарел, и он нажал «Это новый». Запись
    дала бы третий «Сахар» в листе шефа и затёрла бы решение человека. 409
    до записи, без журнала; предпросмотр говорит то же."""
    sugar = _confirm(sessions, "Сахар", "99")
    card = _card(sessions, "Сахар")
    filler = _filler(client, sessions)

    with sessions() as session:
        seen = preview(session, filler, card.id)
    with sessions() as session, pytest.raises(ReferenceConflictError) as caught:
        to_reference(session, filler, _NoImport(), card.id, FORM, actor_id=None)

    assert str(caught.value) == "Пара уже подтверждена — обновите страницу"
    assert caught.value.extra() == {"reason": "confirmed", "row": 8}
    assert status_for(type(caught.value)) == 409
    assert (seen.row, seen.ready, seen.reason) == (8, False, "confirmed")
    assert _writes(client) == []
    assert _journal(sessions) == []
    assert _pair(_card(sessions, "Сахар")) == (sugar, "linked", True)


def test_pair_confirmed_meanwhile_is_not_overwritten(sessions, client) -> None:
    """Пока шла запись, пару подтвердили с другим ингредиентом. Запись
    состоялась — ответ удачный, но решение человека не затирается: пара не
    тронута, и сказано, что проверить."""
    card = _card(sessions, "Соус Барбекю")
    other: list[int] = []
    books = _TransferThen(
        _cycle(client, sessions), lambda: other.append(_confirm(sessions, "Соус Барбекю", "1"))
    )

    with sessions() as session:
        done = to_reference(session, _filler(client, sessions), books, card.id, FORM, actor_id=None)

    assert (done.row, done.ref_id, done.already) == (6, "131", False)
    assert (done.imported, done.linked, done.ingredient_id) == (True, False, None)
    assert done.notes == (
        "Пара карточки уже подтверждена с другим ингредиентом — её не меняли. Проверьте "
        "строку 6 листа ING: ингредиент может оказаться в справочнике дважды",
    )
    assert _pair(_card(sessions, "Соус Барбекю")) == (other[0], "linked", True)


def test_id_that_is_not_a_whole_number_is_refused_as_shifted(sessions, client) -> None:
    """В A строки — не целое число: это не id справочника, пару не подтвердить."""
    _ing(client).put("A6", "131а")
    card = _card(sessions, "Соус Барбекю")
    books = _cycle(client, sessions)

    with sessions() as session, pytest.raises(ReferenceConflictError) as caught:
        to_reference(session, _filler(client, sessions), books, card.id, FORM, actor_id=None)

    assert str(caught.value) == NotFound.SHIFTED.message
    assert _pair(_card(sessions, "Соус Барбекю")) == (None, "orphan", False)
    assert _writes(client) == []


def test_id_typed_by_the_chef_waits_for_the_import(sessions, client) -> None:
    """Шеф сам вписал id 131 в строку минуту назад — в базе его ещё нет, а
    перенос не удался: «уже в справочнике», ``imported=false``, пара не
    подтверждена. Когда перенос проходит — подтверждена."""
    _ing(client).put("A6", 131)
    card = _card(sessions, "Соус Барбекю")
    filler = _filler(client, sessions)

    with sessions() as session:
        done = to_reference(session, filler, _broken_cycle(sessions), card.id, FORM, actor_id=None)

    assert (done.ref_id, done.already, done.imported, done.linked) == ("131", True, False, False)
    assert done.notes == (NOT_IMPORTED,)
    assert _pair(_card(sessions, "Соус Барбекю")) == (None, "orphan", False)

    with sessions() as session:
        again = to_reference(
            session, filler, _cycle(client, sessions), card.id, FORM, actor_id=None
        )

    assert (again.imported, again.linked) == (True, True)
    assert _pair(_card(sessions, "Соус Барбекю")) == (
        _ingredient_id(sessions, "131"),
        "linked",
        True,
    )
    assert _writes(client) == []


# ---------------------------------------------------------------------------
# imported — «наш ингредиент виден на сайте под этим id»
# ---------------------------------------------------------------------------
_OTHER_ON_SITE = (
    "Пара карточки и ингредиента не подтверждена: на сайте у id 131 другое название или его "
    "нет — проверьте строку 6 листа ING"
)


class _TransferThen:
    """Перенос книги кухни в базу — настоящий, а сразу за ним что-то
    происходит: лист поменяли, воркер занял очередь импорта."""

    def __init__(self, cycle: SyncCycle, then: Callable[[], None]) -> None:
        self.cycle = cycle
        self.then = then

    def run(self, *, force=False, books=None):
        result = self.cycle.run(force=force, books=books)
        self.then()
        return result


class _FailedTransfer:
    """Перенос, который упал, не дойдя до базы."""

    def run(self, *, force=False, books=None):
        raise RuntimeError("Google-таблица не ответила")


@pytest.fixture
def worker(sessions: sessionmaker[Session]) -> Iterator[Callable[[], None]]:
    """Воркер, который по знаку теста занимает очередь импорта и держит её
    до конца теста."""
    held: list[Session] = []

    def take() -> None:
        session = sessions()
        session.begin()
        take_import_lock(session)
        held.append(session)

    yield take
    for session in held:
        session.rollback()
        session.close()


def _change_ingredient(sessions: sessionmaker[Session], legacy_id: str, **values: object) -> None:
    with sessions.begin() as session:
        session.execute(
            update(models.Ingredient)
            .where(models.Ingredient.legacy_id == legacy_id)
            .values(**values)
        )


@pytest.mark.parametrize(
    "change",
    [{"name": "Горчица"}, {"removed_at": func.now()}],
    ids=["other-name", "removed"],
)
def test_written_row_but_not_our_ingredient_on_site_is_imported_false(
    sessions, client, caplog, change: dict[str, object]
) -> None:
    """Строку записали мы, перенос прошёл, а на сайте под id 131 не наш
    ингредиент: лист меняли между записью и переносом — у id другое название
    или его убрали. Запись состоялась — ответ удачный, а не «не сделано»;
    наш ингредиент на сайте не виден — ``imported=false``, пара не
    подтверждена. Оговорка — что проверить в листе, без обещания «появится
    через несколько минут»: само это не пройдёт. И ERROR в лог: экрана
    журнала пока нет."""
    card = _card(sessions, "Соус Барбекю")
    books = _TransferThen(
        _cycle(client, sessions), lambda: _change_ingredient(sessions, "131", **change)
    )

    with (
        caplog.at_level(logging.ERROR, logger="kitchen.cards"),
        sessions() as session,
    ):
        done = to_reference(session, _filler(client, sessions), books, card.id, FORM, actor_id=None)

    assert (done.row, done.ref_id, done.already) == (6, "131", False)
    assert (done.imported, done.linked, done.ingredient_id) == (False, False, None)
    assert done.notes == (_OTHER_ON_SITE,)
    assert _card(sessions, "Соус Барбекю").link_confirmed_at is None
    assert "строка 6 листа ING записана (id 131" in caplog.text


def test_pair_not_confirmed_but_our_ingredient_on_site_is_imported_true(
    sessions, client, worker
) -> None:
    """Перенос прошёл, а очередь импорта сразу занял воркер — пару подтвердить
    не дали. Наш ингредиент на сайте виден: ``imported=true``, оговорка —
    только о паре."""
    card = _card(sessions, "Соус Барбекю")
    books = _TransferThen(_cycle(client, sessions), worker)

    with sessions() as session:
        done = to_reference(
            session,
            _filler(client, sessions),
            books,
            card.id,
            FORM,
            actor_id=None,
            wait=SHORT_WAIT,
        )

    assert (done.row, done.ref_id, done.already) == (6, "131", False)
    assert (done.imported, done.linked, done.ingredient_id) == (True, False, None)
    assert done.notes == (PAIR_NOT_CONFIRMED,)
    assert _ingredient(sessions, "131") is not None
    assert _card(sessions, "Соус Барбекю").link_confirmed_at is None


@pytest.mark.parametrize(
    "change",
    [{"name": "Горчица"}, {"removed_at": func.now()}],
    ids=["other-name", "removed"],
)
def test_pair_not_confirmed_and_not_our_ingredient_on_site_is_imported_false(
    sessions, client, worker, change: dict[str, object]
) -> None:
    """Пару подтвердить не дали, а под id 131 на сайте не наш ингредиент —
    другое название или убран из листа. «Есть ингредиент с этим id» мало:
    ``imported=false`` и оговорка «пока не перенесён»."""
    card = _card(sessions, "Соус Барбекю")

    def meanwhile() -> None:
        _change_ingredient(sessions, "131", **change)
        worker()

    books = _TransferThen(_cycle(client, sessions), meanwhile)

    with sessions() as session:
        done = to_reference(
            session,
            _filler(client, sessions),
            books,
            card.id,
            FORM,
            actor_id=None,
            wait=SHORT_WAIT,
        )

    assert (done.imported, done.linked, done.ingredient_id) == (False, False, None)
    assert done.notes == (NOT_IMPORTED, PAIR_NOT_CONFIRMED)


def test_pair_not_confirmed_and_transfer_failed_is_imported_false(sessions, client, worker) -> None:
    """Перенос упал, и пару подтвердить не дали: ингредиента на сайте нет —
    ``imported=false`` и оговорка «пока не перенесён» вместе с «нажмите ещё
    раз»."""
    card = _card(sessions, "Соус Барбекю")
    worker()

    with sessions() as session:
        done = to_reference(
            session,
            _filler(client, sessions),
            _FailedTransfer(),
            card.id,
            FORM,
            actor_id=None,
            wait=SHORT_WAIT,
        )

    assert (done.row, done.ref_id, done.already) == (6, "131", False)
    assert (done.imported, done.linked, done.ingredient_id) == (False, False, None)
    assert done.notes == (NOT_IMPORTED, PAIR_NOT_CONFIRMED)
    assert _ingredient(sessions, "131") is None


class _Imported:
    """Перенос, который отвечает «перенесено», ничего не делая: база уже
    такая, какой её сделал бы перенос."""

    def run(self, *, force=False, books=None):
        return CycleResult(outcomes={book: BookOutcome("imported") for book in books or ()})


def test_pair_is_confirmed_only_in_the_import_queue(sessions, client, worker) -> None:
    """Подтверждение пары после переноса стоит в очереди импорта: импорт,
    начатый раньше и записавший позже, вернул бы карточке «спорную» пару
    поверх подтверждённой — и навсегда. Перенос ответил «перенесено», а
    очередь держит воркер дольше ожидания — пару не подтверждаем: пара не
    тронута, оговорка — только о паре (наш «Кетчуп» 129 на сайте виден)."""
    card = _card(sessions, "Кетчуп")
    ketchup = _ingredient_id(sessions, "129")
    worker()

    with sessions() as session:
        done = to_reference(
            session,
            _filler(client, sessions),
            _Imported(),
            card.id,
            FORM,
            actor_id=None,
            wait=SHORT_WAIT,
        )

    assert (done.row, done.ref_id, done.already) == (4, "129", True)
    assert (done.imported, done.linked, done.ingredient_id) == (True, False, None)
    assert done.notes == (PAIR_NOT_CONFIRMED,)
    assert _pair(_card(sessions, "Кетчуп")) == (ketchup, "linked", False)


def test_id_long_in_the_database_confirms_the_pair_even_if_this_transfer_failed(
    sessions, client
) -> None:
    """«Уже в справочнике»: «Кетчуп» с id 129 давно на сайте, перенос сейчас
    упал, очередь импорта свободна. Ингредиент в базе тот же — пару
    подтверждаем: ``imported=true``, без оговорок."""
    card = _card(sessions, "Кетчуп")
    ketchup = _ingredient_id(sessions, "129")

    with sessions() as session:
        done = to_reference(
            session, _filler(client, sessions), _FailedTransfer(), card.id, FORM, actor_id=None
        )

    assert (done.row, done.ref_id, done.already) == (4, "129", True)
    assert (done.imported, done.linked, done.ingredient_id) == (True, True, ketchup)
    assert done.notes == ()
    assert _pair(_card(sessions, "Кетчуп")) == (ketchup, "linked", True)
    assert _writes(client) == []


def test_ingredient_long_on_site_is_imported_even_if_this_transfer_failed(
    sessions, client, worker
) -> None:
    """«Уже в справочнике»: «Кетчуп» с id 129 давно на сайте. Перенос сейчас
    упал, и пару подтвердить не дали, — но наш ингредиент под этим id виден:
    ``imported=true``, «пока не перенесён» было бы неправдой."""
    card = _card(sessions, "Кетчуп")
    worker()

    with sessions() as session:
        done = to_reference(
            session,
            _filler(client, sessions),
            _FailedTransfer(),
            card.id,
            FORM,
            actor_id=None,
            wait=SHORT_WAIT,
        )

    assert (done.row, done.ref_id, done.already) == (4, "129", True)
    assert (done.imported, done.linked) == (True, False)
    assert done.notes == (PAIR_NOT_CONFIRMED,)


# ---------------------------------------------------------------------------
# Отказы — текстом для человека и кодом ответа
# ---------------------------------------------------------------------------
class _Refusing:
    """Писатель, который отказывает — проверяется перевод отказа."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def fill(self, card_name, form, *, actor_id, request_key, confirmed):
        raise self.error

    def preview(self, card_name, *, confirmed):
        raise self.error


class _NoImport:
    """Перенос книги в базу, которого после отказа быть не должно."""

    def __init__(self) -> None:
        self.runs = 0

    def run(self, *, force=False, books=None):
        self.runs += 1
        raise AssertionError("перенос в базу после отказа записи")


_UNAVAILABLE = "Google-таблица не ответила — в справочнике ничего не изменилось, нажмите ещё раз"
_UNCONFIRMED = "Лист ING меняли в ту же секунду — запись не подтверждена. Покажите шефу строку 6"


@pytest.mark.parametrize(
    ("error", "kind", "code", "text", "extra"),
    [
        (
            RowRefusedError(NotFound.NOT_YET.message, reason="not_yet"),
            ReferenceConflictError,
            409,
            NotFound.NOT_YET.message,
            {"reason": "not_yet", "row": None},
        ),
        (
            RowRefusedError("В строке 6 листа ING …", reason=REASON_FORMULA, row=6),
            ReferenceConflictError,
            409,
            "В строке 6 листа ING …",
            {"reason": REASON_FORMULA, "row": 6},
        ),
        (
            SheetLayoutError(HEADER_DRIFT, ["колонка C"]),
            ReferenceUnavailableError,
            503,
            HEADER_DRIFT,
            {},
        ),
        (SheetBusyError(BUSY), ReferenceUnavailableError, 503, BUSY, {}),
        (ForbiddenWriteError("ворота закрыты"), ReferenceUnavailableError, 503, WRITING_CLOSED, {}),
        (SheetUnavailableError(_UNAVAILABLE), ReferenceFailedError, 502, _UNAVAILABLE, {}),
        (
            WriteNotConfirmedError(_UNCONFIRMED, row=6, journal_id=5, layout_confirmed=False),
            ReferenceFailedError,
            502,
            _UNCONFIRMED,
            {"row": 6, "journal_id": 5},
        ),
        (
            OperationalError("insert into sheet_writes", {}, Exception("server closed")),
            ReferenceFailedError,
            502,
            NOT_CONFIRMED,
            {},
        ),
        (
            ReferenceFormError({"price_per_pack": "Цена за упаковку: число не записать точно"}),
            ReferenceFormInvalidError,
            422,
            "Цена за упаковку: число не записать точно",
            {"errors": {"price_per_pack": "Цена за упаковку: число не записать точно"}},
        ),
    ],
    ids=[
        "not-yet",
        "formula",
        "header",
        "busy",
        "closed",
        "google",
        "unconfirmed",
        "database",
        "inexact",
    ],
)
def test_writer_refusal_reaches_the_human(
    sessions, client, error, kind, code: int, text: str, extra: dict[str, object]
) -> None:
    """Отказ писателя — текстом спеки и кодом ответа; переноса в базу и
    подтверждения пары после него нет."""
    card = _card(sessions, "Соус Барбекю")
    books = _NoImport()

    with sessions() as session, pytest.raises(kind) as caught:
        to_reference(session, _Refusing(error), books, card.id, FORM, actor_id=None)

    assert str(caught.value) == text
    assert caught.value.extra() == extra
    assert status_for(type(caught.value)) == code
    assert books.runs == 0
    assert _pair(_card(sessions, "Соус Барбекю")) == (None, "orphan", False)


@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (SheetLayoutError(HEADER_DRIFT), ReferenceUnavailableError, 503),
        (SheetUnavailableError(_UNAVAILABLE), ReferenceFailedError, 502),
    ],
    ids=["header", "google"],
)
def test_preview_refusal_reaches_the_human(sessions, client, error, kind, code: int) -> None:
    card = _card(sessions, "Соус Барбекю")

    with sessions() as session, pytest.raises(kind) as caught:
        preview(session, _Refusing(error), card.id)

    assert str(caught.value) == str(error)
    assert status_for(type(caught.value)) == code


def _journal(sessions: sessionmaker[Session]) -> list[models.SheetWrite]:
    with sessions() as session:
        return list(session.scalars(select(models.SheetWrite)))


def test_form_errors_come_before_the_write(sessions, client) -> None:
    """Строка ждёт переноса — ошибки формы все сразу, по полям, и до записи:
    ни запроса записи, ни следа в журнале, ни переноса в базу."""
    card = _card(sessions, "Соус Барбекю")

    with sessions() as session, pytest.raises(ReferenceFormInvalidError) as caught:
        to_reference(
            session,
            _filler(client, sessions),
            _NoImport(),
            card.id,
            {"unit": "ящик", "losses_cutting": "100"},
            actor_id=None,
        )

    assert set(caught.value.extra()["errors"]) == {"short_name", "unit", "losses_cutting"}
    assert status_for(type(caught.value)) == 422
    assert _writes(client) == []
    assert _journal(sessions) == []


BROKEN_FORM = {"short_name": "", "unit": "уп"}
"""Значения строки шефа, которые форма не примет: короткого имени нет,
единица «уп» — не кг, л или шт."""


def test_row_already_filled_needs_no_form(sessions, client) -> None:
    """id в строке уже стоит — писать нечего, и форма не нужна: «Связать с
    карточкой» шлёт значения строки шефа как есть, и даже те, что форма не
    приняла бы, не мешают подтвердить пару. Строка, ждущая переноса, с той же
    формой — 422 до записи."""
    ketchup = _card(sessions, "Кетчуп")
    barbecue = _card(sessions, "Соус Барбекю")
    filler = _filler(client, sessions)

    with sessions() as session:
        done = to_reference(
            session, filler, _cycle(client, sessions), ketchup.id, BROKEN_FORM, actor_id=None
        )
    with sessions() as session, pytest.raises(ReferenceFormInvalidError) as caught:
        to_reference(session, filler, _NoImport(), barbecue.id, BROKEN_FORM, actor_id=None)

    assert (done.row, done.ref_id, done.already, done.linked) == (4, "129", True, True)
    assert _pair(_card(sessions, "Кетчуп")) == (_ingredient_id(sessions, "129"), "linked", True)
    assert set(caught.value.errors) == {"short_name", "unit"}
    assert _writes(client) == []
    assert _journal(sessions) == []


def test_not_configured_book_is_503(sessions, client) -> None:
    """Книга кухни или карточек не настроена — писателя нет: ни предпросмотра,
    ни переноса."""
    card = _card(sessions, "Соус Барбекю")
    with sessions() as session:
        for action in (
            lambda: preview(session, None, card.id),
            lambda: to_reference(session, None, _NoImport(), card.id, FORM, actor_id=None),
        ):
            with pytest.raises(ReferenceUnavailableError) as caught:
                action()
            assert str(caught.value) == NOT_CONFIGURED
            assert status_for(type(caught.value)) == 503


def test_rejected_card_is_refused_by_the_sheet(sessions, client) -> None:
    """Согласованность решает свежее чтение книги карточек, а не база:
    «Майонез» отбракован — 409 словами спеки, записи нет."""
    card = _card(sessions, "Майонез")
    assert dict(CARDS_ORDER)["Майонез"] == REJECTED

    with sessions() as session, pytest.raises(ReferenceConflictError) as caught:
        to_reference(session, _filler(client, sessions), _NoImport(), card.id, FORM, actor_id=None)

    assert str(caught.value) == NotFound.NOT_APPROVED.message
    assert caught.value.extra() == {"reason": "not_approved", "row": None}
    assert _writes(client) == []
