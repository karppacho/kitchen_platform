"""Ручки «Сверки справочника» на настоящей базе: ``/api/reconciliation/*``.

Лист — фальшивые таблицы (``tests/conftest.py``): книга кухни с листом ING,
устроенным как настоящий, и книга карточек с порядком «Да». Писателя строки,
журнал записей и перенос книги кухни в базу собирает само приложение, как в
бою; подменяется только вход в Google (и в паре тестов — писатель с коротким
ожиданием очереди или упавший перенос).

Что стерегут эти тесты:

* права — шеф, коммерция и разработчик; повар — 403, без входа — 401;
  изменяющие запросы без ``X-Kitchen-Csrf`` — 403, и ни один из отказов не
  доходит ни до таблицы, ни до базы;
* на каждую строку таблицы «Ответы человеку» спеки — свой код и текст;
* числа в ответах — строками, потери для формы — в процентах;
* повтор после обрыва второй записи не даёт — «уже в справочнике»;
* список «Сверки» — кандидаты у спорных пар обоих видов, ``approved`` и
  ``actions``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from kitchen.cards.reference import (
    CARD_NOT_FOUND,
    NOT_A_CANDIDATE,
    NOT_CONFIGURED,
    NOT_IMPORTED,
    WRITING_CLOSED,
)
from kitchen.cards.submit import IMPORT_GOOGLE_TIMEOUT
from kitchen.config import Settings
from kitchen.db import models
from kitchen.db.journal import DbJournal
from kitchen.db.links import known_reference_ids
from kitchen.domain.cards import APPROVED, REJECTED
from kitchen.domain.reference_row import NotFound
from kitchen.sync import ownership, specs
from kitchen.sync.reference_writer import BUSY, HEADER_DRIFT, NOT_CONFIRMED, ReferenceRowFiller
from kitchen.sync.sheet_write import SHEET_WRITE_LOCK_KEY
from kitchen.web import auth, reconciliation
from kitchen.web.app import create_app
from kitchen.web.csrf import REJECTED as CSRF_REJECTED
from tests.conftest import CARDS_ORDER, FakeSheetsClient, Formula, ing_sheet
from tests.fake_sheets import IDS, cards_sheet, row, sheets_client
from tests.integration.test_database import _url
from tests.integration.test_reference_flow import (
    FORM,
    _card,
    _client,
    _cycle,
    _import,
    _ing,
    _ingredient_id,
    _pair,
    _writes,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import httpx
    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

CHEF = uuid.UUID("33333333-3333-3333-3333-333333333333")
CSRF = {"X-Kitchen-Csrf": "1"}
LIST = "/api/reconciliation"
ROLES = ("chef", "commerce", "developer")
ACTIONS = ("list", "preview", "confirm", "transfer")

OLD = ((1, "Лук"), (98, "Сахар"), (99, "Сахар"))
"""Старые строки ING: два «Сахара» — карточка «Сахар» спорная, выбрать есть
из кого. Зона QUERY — с 5-й строки: Кетчуп (5), Моцарелла (6), Соус Барбекю
(7), Соус Сырный (8), Сахар (9)."""


# ---------------------------------------------------------------------------
# Окружение
# ---------------------------------------------------------------------------
def _sheets(*, cards: Sequence[tuple[str, str]] = CARDS_ORDER) -> FakeSheetsClient:
    """Книга кухни с листом ING и книга карточек. ``cards`` — какой книгу
    карточек увидит свежее чтение писателя."""
    return _client(ing=ing_sheet(old=OLD), cards=cards)


def settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": _url(),
        "sheets_id_kitchen": IDS["kitchen"],
        "sheets_id_ingredient_cards": IDS["ingredient_cards"],
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def make_client(
    sheets: FakeSheetsClient,
    *,
    user: uuid.UUID | None = CHEF,
    roles: tuple[str, ...] = ("chef",),
    config: Settings | None = None,
    filler: Callable[[], ReferenceRowFiller | None] | None = None,
    books: Callable[[], object] | None = None,
) -> TestClient:
    """Приложение с фальшивыми таблицами вместо входа в Google. Писателя и
    перенос собирает само приложение; ``filler`` и ``books`` — подменить их."""
    app = create_app(config or settings())
    app.state.google = lambda: sheets
    if filler is not None:
        app.dependency_overrides[reconciliation.get_reference_filler] = filler
    if books is not None:
        app.dependency_overrides[reconciliation.get_kitchen_import] = books
    if user is not None:
        app.dependency_overrides[auth.current_user] = lambda: auth.CurrentUser(
            id=user, email="chef@example.com", display_name="Шеф", roles=frozenset(roles)
        )
    return TestClient(app)


@pytest.fixture
def world(sessions: sessionmaker[Session]) -> FakeSheetsClient:
    """Таблицы, перенесённые в базу, и профиль шефа. Справочник: Лук (1),
    Сахар (98 и 99), Кетчуп (129), Моцарелла (130). Кетчуп и Моцарелла
    связаны, «Сахар» спорный, остальные карточки — без пары."""
    with sessions.begin() as session:
        session.add(models.Profile(id=CHEF, email="chef@example.com", display_name="Шеф"))
    sheets = _sheets()
    _import(sheets, sessions)
    return sheets


@pytest.fixture
def chef(world: FakeSheetsClient) -> TestClient:
    return make_client(world)


def confirm(
    client: TestClient, card_id: int, ingredient_id: int, headers: dict[str, str] = CSRF
) -> httpx.Response:
    return client.post(
        f"{LIST}/{card_id}/confirm", json={"ingredient_id": ingredient_id}, headers=headers
    )


def preview(client: TestClient, card_id: int) -> httpx.Response:
    return client.get(f"{LIST}/{card_id}/reference-row")


def transfer(
    client: TestClient,
    card_id: int,
    form: dict[str, str] = FORM,
    headers: dict[str, str] = CSRF,
) -> httpx.Response:
    return client.post(f"{LIST}/{card_id}/to-reference", json=form, headers=headers)


def _call(client: TestClient, sessions: sessionmaker[Session], which: str) -> httpx.Response:
    """Одна из четырёх ручек — с настоящими id: дойди запрос до ручки, она
    бы сработала."""
    sugar = _card(sessions, "Сахар").id
    barbecue = _card(sessions, "Соус Барбекю").id
    if which == "list":
        return client.get(LIST)
    if which == "preview":
        return preview(client, barbecue)
    if which == "confirm":
        return confirm(client, sugar, _ingredient_id(sessions, "98"))
    return transfer(client, barbecue)


def _untouched(sessions: sessionmaker[Session], sheets: FakeSheetsClient) -> None:
    """Ни записи в лист, ни журнала, ни подтверждённой пары."""
    assert _writes(sheets) == []
    with sessions() as session:
        assert session.scalars(select(models.SheetWrite)).all() == []
    assert _pair(_card(sessions, "Сахар")) == (None, "ambiguous", False)
    assert _pair(_card(sessions, "Соус Барбекю")) == (None, "orphan", False)


def _row_of(client: TestClient, name: str) -> dict[str, object] | None:
    rows = client.get(LIST).json()["rows"]
    return next((line for line in rows if line["name"] == name), None)


# ---------------------------------------------------------------------------
# Список «Сверки»
# ---------------------------------------------------------------------------
def test_list_gives_candidates_approved_and_actions(sessions: sessionmaker[Session]) -> None:
    """Кандидаты — у спорных пар обоих видов: тёзки у «Сахара», похожие у
    «Томата». ``approved`` — «Да» карточки; ``actions`` — «Это он», если есть
    из кого выбрать, и форма переноса, если карточка согласована. Без пары и
    не «Да» — действий нет."""
    cards = [
        *cards_sheet()[:3],
        row(specs.INGREDIENT_CARDS, name="Сахар"),
        row(specs.INGREDIENT_CARDS, name="Пастрами из индейки", approval_status=APPROVED),
        row(specs.INGREDIENT_CARDS, name="Томат", approval_status=APPROVED),
        row(specs.INGREDIENT_CARDS, name="Горчица", approval_status=REJECTED),
    ]
    sheets = sheets_client(cards=cards)
    _cycle(sheets, sessions).run(force=True)
    client = make_client(sheets)

    reply = client.get(LIST)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["total"], body["linked"], body["needs_human"]) == (5, 1, 4)
    by_name = {line["name"]: line for line in body["rows"]}
    assert [line["name"] for line in body["rows"]] == [
        "Сахар",
        "Томат",
        "Горчица",
        "Пастрами из индейки",
    ]
    sugar = by_name["Сахар"]
    assert (sugar["link_status"], sugar["approved"], sugar["actions"]) == (
        "ambiguous",
        False,
        ["confirm"],
    )
    assert sugar["candidates"] == [
        {
            "ingredient_id": _ingredient_id(sessions, legacy_id),
            "legacy_id": legacy_id,
            "name": "Сахар",
            "score": None,
        }
        for legacy_id in ("2", "3")
    ]
    tomato = by_name["Томат"]
    assert (tomato["link_status"], tomato["approved"], tomato["actions"]) == (
        "candidate",
        True,
        ["confirm", "to_reference"],
    )
    [similar] = tomato["candidates"]
    assert (similar["ingredient_id"], similar["legacy_id"], similar["name"]) == (
        _ingredient_id(sessions, "1"),
        "1",
        "Томаты",
    )
    assert isinstance(similar["score"], float) and 0.82 <= similar["score"] < 1
    assert {name: (by_name[name]["approved"], by_name[name]["actions"]) for name in by_name} == {
        "Сахар": (False, ["confirm"]),
        "Томат": (True, ["confirm", "to_reference"]),
        "Горчица": (False, []),
        "Пастрами из индейки": (True, ["to_reference"]),
    }
    assert by_name["Горчица"]["candidates"] == by_name["Пастрами из индейки"]["candidates"] == []


def test_disputed_card_with_no_one_to_choose_offers_only_the_form(
    sessions: sessionmaker[Session], chef: TestClient
) -> None:
    """«Это он» — только когда есть из кого выбрать: спорная карточка, у
    которой кандидатов сейчас нет, получает лишь форму переноса."""
    with sessions.begin() as session:
        session.execute(
            update(models.IngredientCard)
            .where(models.IngredientCard.name == "Соус Барбекю")
            .values(link_status="ambiguous")
        )

    line = _row_of(chef, "Соус Барбекю")

    assert line is not None
    assert (line["candidates"], line["approved"], line["actions"]) == ([], True, ["to_reference"])


# ---------------------------------------------------------------------------
# Права и защита от подделки
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("which", ACTIONS)
def test_without_login_is_401(
    sessions: sessionmaker[Session], world: FakeSheetsClient, which: str
) -> None:
    """Вход проверяется раньше, чем заводится вход в Google: отказ не стоит
    ни соединения, ни запроса к таблице."""
    opened, closed = len(world.opened), world.closed

    reply = _call(make_client(world, user=None), sessions, which)

    assert reply.status_code == 401
    assert (len(world.opened), world.closed) == (opened, closed)
    _untouched(sessions, world)


@pytest.mark.parametrize("which", ACTIONS)
def test_cook_is_403(sessions: sessionmaker[Session], world: FakeSheetsClient, which: str) -> None:
    """«Сверка» — цены справочника и запись в книгу кухни: повару её не
    положено, и отказ не доходит ни до Google, ни до базы."""
    opened, closed = len(world.opened), world.closed

    reply = _call(make_client(world, roles=("cook",)), sessions, which)

    assert reply.status_code == 403
    assert reply.json() == {"detail": "нужна роль: chef, commerce, developer"}
    assert (len(world.opened), world.closed) == (opened, closed)
    _untouched(sessions, world)


@pytest.mark.parametrize("role", ROLES)
def test_chef_commerce_and_developer_may_do_everything(
    sessions: sessionmaker[Session], world: FakeSheetsClient, role: str
) -> None:
    client = make_client(world, roles=(role,))

    replies = [_call(client, sessions, which) for which in ACTIONS]

    assert [reply.status_code for reply in replies] == [200, 200, 200, 200], [
        reply.text for reply in replies
    ]


@pytest.mark.parametrize("which", ["confirm", "transfer"])
def test_changing_request_without_csrf_header_is_403(
    sessions: sessionmaker[Session], world: FakeSheetsClient, which: str
) -> None:
    """С кукой сессии и без заголовка запрос до ручки не доходит: ни пары,
    ни записи в лист."""
    client = make_client(world)
    client.cookies.set(auth.ACCESS_COOKIE, "token-from-browser")
    sugar = _card(sessions, "Сахар").id
    barbecue = _card(sessions, "Соус Барбекю").id
    opened = len(world.opened)

    if which == "confirm":
        reply = confirm(client, sugar, _ingredient_id(sessions, "98"), headers={})
    else:
        reply = transfer(client, barbecue, headers={})

    assert reply.status_code == 403
    assert reply.json() == {"detail": CSRF_REJECTED}
    assert len(world.opened) == opened
    _untouched(sessions, world)


# ---------------------------------------------------------------------------
# «Это он»
# ---------------------------------------------------------------------------
def test_this_is_it_confirms_the_pair_and_card_leaves_the_list(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    """Пара подтверждена только в базе — таблица не тронута; двойное нажатие
    — «уже»; карточка уходит со «Сверки»."""
    card = _card(sessions, "Сахар")
    chosen = _ingredient_id(sessions, "98")
    opened = len(world.opened)

    first = confirm(chef, card.id, chosen)
    again = confirm(chef, card.id, chosen)

    assert first.status_code == 200, first.text
    assert first.json() == {
        "card_id": card.id,
        "ingredient_id": chosen,
        "legacy_id": "98",
        "name": "Сахар",
        "already": False,
        "message": "Пара подтверждена: ингредиент «Сахар», id 98",
    }
    assert (again.status_code, again.json()["already"]) == (200, True)
    assert _pair(_card(sessions, "Сахар")) == (chosen, "linked", True)
    assert len(world.opened) == opened, "«Это он» в Google не ходит"
    assert _writes(world) == []
    assert _row_of(chef, "Сахар") is None


def test_this_is_it_with_not_a_candidate_is_409(
    sessions: sessionmaker[Session], chef: TestClient
) -> None:
    card = _card(sessions, "Сахар")

    reply = confirm(chef, card.id, _ingredient_id(sessions, "129"))

    assert reply.status_code == 409
    assert reply.json() == {"detail": NOT_A_CANDIDATE, "reason": "not_candidate", "row": None}
    assert _pair(_card(sessions, "Сахар")) == (None, "ambiguous", False)


def test_id_beyond_the_database_is_422_not_a_server_error(chef: TestClient) -> None:
    """id больше, чем вмещает bigint базы, база встретила бы «bigint out of
    range» — ошибкой сервера. Отказ — до базы."""
    huge = 2**63
    replies = [
        preview(chef, huge),
        transfer(chef, huge),
        confirm(chef, huge, 1),
        confirm(chef, 1, huge),
    ]

    assert [reply.status_code for reply in replies] == [422] * 4


def test_unknown_card_is_404_everywhere(chef: TestClient) -> None:
    replies = [confirm(chef, 999_999, 1), preview(chef, 999_999), transfer(chef, 999_999)]

    assert [(reply.status_code, reply.json()) for reply in replies] == [
        (404, {"detail": CARD_NOT_FOUND})
    ] * 3


# ---------------------------------------------------------------------------
# Строка ING для формы
# ---------------------------------------------------------------------------
def test_preview_gives_numbers_as_strings_and_losses_in_percent(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    """Строка ждёт переноса: следующий id, L — формула шефа, подтянутое
    QUERY и что сейчас в ручных ячейках. Числа — строками; потери — в
    процентах, как их вводят в форму: 0,125 в ячейке — «12.5». Без записи."""
    _ing(world).put("R8", 0.125)
    card = _card(sessions, "Соус Сырный")

    reply = preview(chef, card.id)

    assert reply.status_code == 200, reply.text
    assert reply.json() == {
        "next_id": "131",
        "row": 8,
        "ready": True,
        "reason": None,
        "message": None,
        "ref_id": None,
        "formulas": ["L", "P"],
        "pulled": {
            "category": "Соусы",
            "name": "Соус Сырный",
            "full_name": "Соус Сырный, полное наименование",
            "manufacturer": "Завод",
            "composition": "по ТУ",
            "protein": "1.5",
            "fat": "12.5",
            "carbs": "3",
            "kcal": "150",
        },
        "current": {
            "short_name": "",
            "unit": "кг",
            "status": "активный",
            "price_per_kg": "0",
            "price_per_pack": "0",
            "weight_per_piece_g": None,
            "losses_unpacking": "0",
            "losses_cutting": "12.5",
            "losses_thermal": "0",
        },
    }
    assert _writes(world) == []


def test_preview_of_a_row_already_in_reference(
    sessions: sessionmaker[Session], chef: TestClient
) -> None:
    """id уже стоит: «уже в справочнике», и что в строке — цены и потери 5 %."""
    reply = preview(chef, _card(sessions, "Кетчуп").id)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["row"], body["ready"], body["ref_id"]) == (5, False, "129")
    assert body["reason"] == "already"
    assert body["message"] == "Ингредиент уже в справочнике — id 129"
    assert body["current"] == {
        "short_name": "Кетчуп",
        "unit": "кг",
        "status": "активный",
        "price_per_kg": "250",
        "price_per_pack": "1250",
        "weight_per_piece_g": None,
        "losses_unpacking": "5",
        "losses_cutting": "0",
        "losses_thermal": "0",
    }


def test_preview_without_a_row_says_why(sessions: sessionmaker[Session], chef: TestClient) -> None:
    reply = preview(chef, _card(sessions, "Майонез").id)

    assert reply.status_code == 200, reply.text
    assert reply.json() == {
        "next_id": "131",
        "row": None,
        "ready": False,
        "reason": "not_approved",
        "message": NotFound.NOT_APPROVED.message,
        "ref_id": None,
        "formulas": [],
        "pulled": None,
        "current": None,
    }


# ---------------------------------------------------------------------------
# Перенос в справочник — по строкам таблицы «Ответы человеку»
# ---------------------------------------------------------------------------
def test_transfer_answers_row_and_id(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    """Успех: «Записано в справочник: строка N, id X»; ингредиент на сайте,
    пара подтверждена, карточка ушла со «Сверки». Один вход в Google на
    запрос, перенос — с короткими таймаутами, вход закрыт после ответа."""
    card = _card(sessions, "Соус Барбекю")
    closed, timeouts = world.closed, len(world.timeouts)

    reply = transfer(chef, card.id)

    assert reply.status_code == 200, reply.text
    new = _ingredient_id(sessions, "131")
    assert reply.json() == {
        "row": 7,
        "ref_id": "131",
        "already": False,
        "imported": True,
        "linked": True,
        "ingredient_id": new,
        "message": "Записано в справочник: строка 7, id 131",
        "shifted": None,
        "notes": [],
    }
    assert (_ing(world).cell("A7"), _ing(world).cell("E7")) == (131, "Соус")
    assert _pair(_card(sessions, "Соус Барбекю")) == (new, "linked", True)
    with sessions() as session:
        [write] = session.scalars(select(models.SheetWrite)).all()
    assert (write.request_key, write.actor_id) == (f"ing-fill:{card.id}", CHEF)
    assert world.timeouts[timeouts:] == [IMPORT_GOOGLE_TIMEOUT]
    assert world.closed == closed + 1
    assert _row_of(chef, "Соус Барбекю") is None


def test_new_id_never_repeats_an_id_the_database_remembers(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    """Шеф удалил из листа строку с id 131 — в листе наибольший 130, а база
    помнит 131: ингредиент скрыт отметкой, его ТТК и пары на месте. Новый
    перенос получает 132 — и в предпросмотре, и при записи: с 131 импорт
    «воскресил» бы удалённый ингредиент под новым названием и ценами."""
    with sessions.begin() as session:
        session.add(
            models.Ingredient(legacy_id="131", name="Соус Устричный", removed_at=func.now())
        )
    card = _card(sessions, "Соус Барбекю")

    seen = preview(chef, card.id)
    reply = transfer(chef, card.id)

    assert seen.json()["next_id"] == "132"
    assert reply.status_code == 200, reply.text
    assert (reply.json()["row"], reply.json()["ref_id"]) == (7, "132")
    assert _ing(world).cell("A7") == 132
    new = _ingredient_id(sessions, "132")
    assert _pair(_card(sessions, "Соус Барбекю")) == (new, "linked", True)
    with sessions() as session:
        old = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "131"))
        assert old is not None
        assert (old.name, old.removed_at is not None) == ("Соус Устричный", True)


def test_repeat_after_lost_answer_is_already_without_second_write(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    """Ответ потерялся в сети — человек нажал ещё раз: «уже в справочнике»,
    без второй записи и без второго id."""
    card = _card(sessions, "Соус Барбекю")
    assert transfer(chef, card.id).status_code == 200

    again = transfer(chef, card.id)

    assert again.status_code == 200, again.text
    body = again.json()
    assert (body["row"], body["ref_id"], body["already"], body["linked"]) == (7, "131", True, True)
    assert body["message"] == "Ингредиент уже в справочнике — id 131"
    assert len(_writes(world)) == 1


def test_row_already_in_reference_is_200_without_writing(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    """В строке уже есть id: 200 «Ингредиент уже в справочнике — id N», пара
    подтверждена, записи нет."""
    card = _card(sessions, "Кетчуп")

    reply = transfer(chef, card.id)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["message"] == "Ингредиент уже в справочнике — id 129"
    assert (body["row"], body["ref_id"], body["already"], body["imported"], body["linked"]) == (
        5,
        "129",
        True,
        True,
        True,
    )
    assert _writes(world) == []
    assert _pair(_card(sessions, "Кетчуп")) == (_ingredient_id(sessions, "129"), "linked", True)


@pytest.mark.parametrize(
    ("cards", "name", "reason"),
    [
        (CARDS_ORDER, "Майонез", NotFound.NOT_APPROVED),
        ((*CARDS_ORDER[:-1], ("Горчица", APPROVED)), "Горчица", NotFound.NOT_YET),
        ((("Горчица", APPROVED), *CARDS_ORDER[:-1]), "Соус Барбекю", NotFound.SHIFTED),
        ((*CARDS_ORDER, ("Соус Барбекю", APPROVED)), "Соус Барбекю", NotFound.AMBIGUOUS),
    ],
    ids=["not-approved", "not-yet", "shifted", "ambiguous"],
)
def test_row_not_found_is_409_and_preview_says_the_same(
    sessions: sessionmaker[Session],
    world: FakeSheetsClient,
    cards: Sequence[tuple[str, str]],
    name: str,
    reason: NotFound,
) -> None:
    """Карточка не «Да»; строки ещё нет (``IMPORTRANGE`` не подтянул «Горчицу»,
    согласованную минуту назад); название и место расходятся («Горчицу»
    согласовали в начале книги карточек); «Да» с этим названием две. Свежее
    чтение решает, а не база: в базе «Горчица» ещё не «Да». 409 словами спеки,
    записи нет; предпросмотр говорит то же самое кодом 200."""
    fresh = _sheets(cards=cards)
    client = make_client(fresh)
    card = _card(sessions, name)

    reply = transfer(client, card.id)
    seen = preview(client, card.id)

    assert reply.status_code == 409, reply.text
    assert reply.json() == {"detail": reason.message, "reason": reason.value, "row": None}
    assert (seen.status_code, seen.json()["reason"], seen.json()["message"]) == (
        200,
        reason.value,
        reason.message,
    )
    assert _writes(fresh) == []


def test_formula_in_a_manual_cell_is_409(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    _ing(world).put("E7", Formula("=C7", "Соус Барбекю"))

    reply = transfer(chef, _card(sessions, "Соус Барбекю").id)

    assert reply.status_code == 409, reply.text
    body = reply.json()
    assert (body["reason"], body["row"]) == ("formula", 7)
    assert "в ручной ячейке E — формула" in body["detail"]
    assert _writes(world) == []


def test_shifted_header_is_503(sessions: sessionmaker[Session], world: FakeSheetsClient) -> None:
    """Шапка ING сдвинута — «сообщите разработчику», и у предпросмотра тоже."""
    _ing(world).put("C1", "Что-то другое")
    client = make_client(world)
    card = _card(sessions, "Соус Барбекю")

    replies = [transfer(client, card.id), preview(client, card.id)]

    assert [(reply.status_code, reply.json()) for reply in replies] == [
        (503, {"detail": HEADER_DRIFT})
    ] * 2
    assert _writes(world) == []


def test_busy_sheet_is_503(sessions: sessionmaker[Session], world: FakeSheetsClient) -> None:
    """Очередь писателей занята дольше ожидания — «Таблица занята»; в Google
    запрос не ушёл."""
    client = make_client(
        world,
        filler=lambda: ReferenceRowFiller(
            world,
            DbJournal(sessions),
            kitchen_id=IDS["kitchen"],
            cards_id=IDS["ingredient_cards"],
            known_ids=lambda: known_reference_ids(sessions),
            lock_timeout=timedelta(seconds=1),
        ),
    )
    card = _card(sessions, "Соус Барбекю")
    opened = len(world.opened)
    holder = sessions()
    holder.begin()
    holder.execute(select(func.pg_advisory_xact_lock(SHEET_WRITE_LOCK_KEY)))
    try:
        reply = transfer(client, card.id)
    finally:
        holder.rollback()
        holder.close()

    assert reply.status_code == 503
    assert reply.json() == {"detail": BUSY}
    assert len(world.opened) == opened


def test_google_down_before_writing_is_502(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    world._spreadsheets[IDS["kitchen"]].fail_next("values_batch_get", applied=False)

    reply = transfer(chef, _card(sessions, "Соус Барбекю").id)

    assert reply.status_code == 502
    assert reply.json() == {
        "detail": "Google-таблица не ответила — в справочнике ничего не изменилось, нажмите ещё раз"
    }
    assert _writes(world) == []


def test_unclear_outcome_is_502_and_repeat_is_already_written(
    sessions: sessionmaker[Session],
    world: FakeSheetsClient,
    chef: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Запись ушла, ответ потерялся, перечитать не удалось — исход неясен:
    502 «нажмите ещё раз: второй записи не будет» со строкой и номером
    журнала. Повтор перечитывает строку, видит свою запись — «уже в
    справочнике», без второй записи."""
    kitchen = world._spreadsheets[IDS["kitchen"]]
    write = kitchen.values_batch_update

    def lost(body: dict[str, object]) -> dict[str, object]:
        kitchen.fail_next("values_batch_update", applied=True)
        kitchen.fail_next("values_batch_get", applied=False)
        return write(body)

    monkeypatch.setattr(kitchen, "values_batch_update", lost)
    card = _card(sessions, "Соус Барбекю")

    first = transfer(chef, card.id)
    monkeypatch.undo()
    again = transfer(chef, card.id)

    assert first.status_code == 502, first.text
    assert first.json() == {
        "detail": f"Google-таблица не ответила. {NOT_CONFIRMED}",
        "row": 7,
        "journal_id": 1,
    }
    assert again.status_code == 200, again.text
    body = again.json()
    assert (body["row"], body["ref_id"], body["already"], body["linked"]) == (7, "131", True, True)
    assert body["message"] == "Ингредиент уже в справочнике — id 131"
    assert len(_writes(world)) == 1
    assert _ing(world).cell("A7") == 131


def test_form_errors_are_422_by_field_before_writing(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    """Ошибка в поле формы — 422 с полями по-русски, все сразу, до записи:
    ни запроса записи, ни следа в журнале."""
    reply = transfer(chef, _card(sessions, "Соус Барбекю").id, {"unit": "ящик"})

    assert reply.status_code == 422
    assert reply.json() == {
        "detail": "Короткое имя для iiko — обязательно; Единица измерения — кг, л или шт",
        "errors": {
            "short_name": "Короткое имя для iiko — обязательно",
            "unit": "Единица измерения — кг, л или шт",
        },
    }
    _untouched(sessions, world)


def test_row_already_in_reference_needs_no_form(
    sessions: sessionmaker[Session], world: FakeSheetsClient, chef: TestClient
) -> None:
    """«Связать с карточкой»: id в строке уже стоит — форма не нужна, и
    значения, которые она не приняла бы (пустое короткое имя, «уп»), не
    мешают: 200, пара подтверждена, записи нет."""
    reply = transfer(chef, _card(sessions, "Кетчуп").id, {"short_name": "", "unit": "уп"})

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["row"], body["ref_id"], body["already"], body["linked"]) == (5, "129", True, True)
    assert _writes(world) == []


def test_unknown_form_field_is_422(sessions: sessionmaker[Session], chef: TestClient) -> None:
    """Чужое поле в форме — отказ, а не молча отброшенное: статус и id
    выдаёт сервер, а не форма."""
    reply = transfer(chef, _card(sessions, "Соус Барбекю").id, {**FORM, "id": "7"})

    assert reply.status_code == 422


def test_transfer_whose_import_failed_is_200_with_note(
    sessions: sessionmaker[Session], world: FakeSheetsClient
) -> None:
    """Перенос книги кухни в базу упал — строка уже в листе: 200,
    ``imported=false`` и оговорка, что ингредиент появится позже."""

    class Down:
        def run(self, *, force: bool = False, books: object = None) -> object:
            raise RuntimeError("Google-таблица не ответила")

    client = make_client(world, books=Down)

    reply = transfer(client, _card(sessions, "Соус Барбекю").id)

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["row"], body["ref_id"], body["already"]) == (7, "131", False)
    assert (body["imported"], body["linked"], body["notes"]) == (False, False, [NOT_IMPORTED])


@pytest.mark.parametrize("missing", ["sheets_id_kitchen", "sheets_id_ingredient_cards"])
def test_book_not_configured_is_503(
    sessions: sessionmaker[Session], world: FakeSheetsClient, missing: str
) -> None:
    client = make_client(world, config=settings(**{missing: ""}))
    card = _card(sessions, "Соус Барбекю")

    replies = [transfer(client, card.id), preview(client, card.id)]

    assert [(reply.status_code, reply.json()) for reply in replies] == [
        (503, {"detail": NOT_CONFIGURED})
    ] * 2
    assert _writes(world) == []


def test_writing_closed_by_ownership_rule_is_503(
    sessions: sessionmaker[Session],
    world: FakeSheetsClient,
    chef: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Аварийный рычаг: боты ожили — запись в книгу кухни закрыта."""
    monkeypatch.setattr(ownership, "BOTS_ALIVE", True)

    reply = transfer(chef, _card(sessions, "Соус Барбекю").id)

    assert reply.status_code == 503
    assert reply.json() == {"detail": WRITING_CLOSED}
    assert _writes(world) == []
