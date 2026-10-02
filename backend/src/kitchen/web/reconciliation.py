"""Ручки «Сверки справочника»: список дел, «Это он», строка ING для формы и
перенос карточки в справочник.

Тонкие, как ручки карточек: разобрать запрос, позвать слой приложения
(:mod:`kitchen.cards.reference`), отдать ответ. Решает всё там. Писатель
строки ING и перенос книги кухни в базу приходят зависимостями из
``app.state`` — тест подменяет их; вход в Google — один на запрос
(:func:`kitchen.web.cards.get_google`), закрывается после ответа.

«Сверка» — для шефа, коммерции и разработчика (спека, решение 6): здесь цены
справочника и запись в книгу кухни. Повару — 403, без входа — 401. Вход
проверяется раньше, чем открывается Google: отказ не стоит ни запроса к
таблице, ни соединения.

Изменяющие запросы с кукой сессии стоят за общей защитой от подделки
(:mod:`kitchen.web.csrf`): без ``X-Kitchen-Csrf: 1`` до ручки они не доходят.

Отказы слоя — наследники ``CardsError``: текст для человека и код отдаёт общий
обработчик (:func:`kitchen.web.cards.cards_error`, ``ERROR_STATUS``), ручкам
ловить нечего.

Числа — строками, как во всём API: двоичная дробь JSON исказила бы цены и
проценты. Потери в строке для формы — в процентах, как их вводят в форму.

Перенос в справочник идёт десятки секунд — запись, перенос книги кухни,
ожидание очередей; nginx ждёт ответа ``/api/`` до 300 с.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from kitchen.cards import reference
from kitchen.db import models
from kitchen.db.links import card_candidates
from kitchen.domain.cards import APPROVED
from kitchen.sync.cycle import SyncCycle
from kitchen.sync.reference_writer import PreviewValue, ReferenceRowFiller, RowPreview
from kitchen.web.auth import CurrentUser, SessionDep, require
from kitchen.web.cards import GoogleDep, ShiftedOut

# Типы зависимостей импортируются в рантайме, а не под TYPE_CHECKING: с
# `from __future__ import annotations` FastAPI разрешает их по именам модуля
# (см. kitchen.web.auth).

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reconciliation", tags=["сверка"])

ReconcilerDep = Annotated[CurrentUser, Depends(require("chef", "commerce", "developer"))]

_ID_MAX = 2**63 - 1
"""id в базе — bigint. Число больше psycopg отправляет как bigint, и база
отвечает «bigint out of range» — ошибкой сервера. Поэтому граница — до базы:
422."""

CardId = Annotated[int, Path(ge=1, le=_ID_MAX, description="id карточки")]

_HUNDRED = Decimal(100)

Action = Literal["confirm", "to_reference"]
"""Что можно сделать с карточкой на «Сверке»:

* ``confirm`` — «Это он»: выбрать ингредиент из ``candidates``;
* ``to_reference`` — форма переноса в справочник: «Добавить в справочник» у
  карточки без пары, «Это новый» у спорной.
"""


def get_reference_filler(request: Request, google: GoogleDep) -> ReferenceRowFiller | None:
    """Писатель строки ING; ``None`` — книга кухни или карточек не настроена."""
    filler: ReferenceRowFiller | None = request.app.state.reference_filler(google)
    return filler


def get_kitchen_import(request: Request, google: GoogleDep) -> SyncCycle:
    """Перенос книги кухни в базу после записи — с коротким ожиданием."""
    cycle: SyncCycle = request.app.state.kitchen_import(google)
    return cycle


FillerDep = Annotated[ReferenceRowFiller | None, Depends(get_reference_filler)]
KitchenImportDep = Annotated[SyncCycle, Depends(get_kitchen_import)]


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------
class CandidateRow(BaseModel):
    """Ингредиент, который можно выбрать парой карточке («Это он»)."""

    ingredient_id: int
    legacy_id: str
    name: str
    score: float | None = None
    """Похожесть названий, 0–1, — у похожих (``candidate``); у тёзок
    (``ambiguous``) ``null``: название совпало точно. Оценка, а не величина
    — числом, как и была."""


class ReconciliationRow(BaseModel):
    """Карточка, требующая решения человека."""

    card_id: int
    name: str
    link_status: str
    supplier: str
    approved: bool
    """Карточка согласована («Да» в книге карточек — по последнему переносу в
    базу). В справочник попадают только такие; запись всё равно сверяет «Да»
    свежим чтением."""
    candidates: list[CandidateRow]
    """Из кого выбирать «Это он»: у ``ambiguous`` — тёзки, у ``candidate`` —
    похожие, самые похожие первыми; у ``orphan`` — пусто."""
    actions: list[Action]
    """Доступные действия; пусто — только пояснение, без кнопки."""


class ReconciliationSummary(BaseModel):
    total: int
    linked: int
    needs_human: int
    rows: list[ReconciliationRow]


class ConfirmIn(BaseModel):
    """«Это он»: выбранный ингредиент."""

    model_config = ConfigDict(extra="forbid")

    ingredient_id: int = Field(ge=1, le=_ID_MAX)


class PairOut(BaseModel):
    """Пара «карточка — ингредиент» подтверждена."""

    card_id: int
    ingredient_id: int
    legacy_id: str
    """id ингредиента в листе ING."""
    name: str
    """Название ингредиента."""
    already: bool
    """Пара была подтверждена этим же ингредиентом раньше — двойное нажатие."""
    message: str
    """«Пара подтверждена: ингредиент «…», id N»."""


class PulledOut(BaseModel):
    """Что ``QUERY`` уже вывела в строку — как видит шеф."""

    category: str
    name: str
    full_name: str
    manufacturer: str
    composition: str
    protein: str | None
    fat: str | None
    carbs: str | None
    kcal: str | None


class CurrentOut(BaseModel):
    """Ручные ячейки строки сейчас — чем заполнить форму. Поля и единицы —
    те же, что у формы переноса: потери — в процентах («12.5» — 12,5 %)."""

    short_name: str
    unit: str
    status: str
    price_per_kg: str | None
    """L. Если L — формула (``"L" in formulas``), то, что она показывает."""
    price_per_pack: str | None
    weight_per_piece_g: str | None
    losses_unpacking: str | None
    losses_cutting: str | None
    losses_thermal: str | None


class ReferenceRowOut(BaseModel):
    """Строка ING карточки для формы переноса — свежим чтением, без записи."""

    next_id: str
    """id, который получила бы строка сейчас, — справочно: при записи его
    выдают заново."""
    row: int | None
    """Строка листа ING; ``null`` — строку не нашли (почему — ``reason``)."""
    ready: bool
    """Строку можно заполнить."""
    reason: str | None
    """Почему нельзя: ``not_approved``, ``not_yet``, ``shifted``,
    ``ambiguous``, ``formula``, ``percent``, ``losses_empty`` или
    ``already`` — id уже стоит."""
    message: str | None
    """То же словами для человека."""
    ref_id: str | None
    """id из колонки A — при ``already``."""
    formulas: list[str]
    """Ручные ячейки строки с формулой, буквами, и всегда P. ``L`` здесь —
    поле «цена за единицу» скрыть с пометкой «считается в таблице»."""
    pulled: PulledOut | None
    """``null``, если строки нет."""
    current: CurrentOut | None
    """``null``, если строки нет."""


class ReferenceFormIn(BaseModel):
    """Форма переноса — поля строками, как их ввёл человек. Потери — в
    процентах: «5» — 5 %, пусто — 0. Статус не вводится («активный»), id
    выдаёт сервер. Чужие поля — 422."""

    model_config = ConfigDict(extra="forbid")

    short_name: str | None = None
    unit: str | None = None
    price_per_kg: str | None = None
    price_per_pack: str | None = None
    weight_per_piece_g: str | None = None
    losses_unpacking: str | None = None
    losses_cutting: str | None = None
    losses_thermal: str | None = None


class TransferOut(BaseModel):
    """Карточка в справочнике: «Записано в справочник: строка N, id X»."""

    row: int
    ref_id: str
    already: bool
    """Ингредиент был в справочнике и до этого нажатия."""
    imported: bool
    """Наш ингредиент виден на сайте под этим id; ``false`` — в ``notes``
    оговорка, что появится позже."""
    linked: bool
    """Пара «карточка — ингредиент» подтверждена — карточка ушла со «Сверки»."""
    ingredient_id: int | None
    message: str
    shifted: ShiftedOut | None
    """Прежняя попытка, раскладку которой не подтвердили: строку и запись
    журнала человек показывает шефу."""
    notes: list[str]
    """Оговорки готовыми фразами — показать как есть."""


# ---------------------------------------------------------------------------
# Список «Сверки»
# ---------------------------------------------------------------------------
def _actions(approved: bool, candidates: tuple[object, ...]) -> list[Action]:
    """«Это он» — когда есть из кого выбрать; форма переноса — только для
    согласованной карточки: иначе её откажет запись."""
    found: list[Action] = []
    if candidates:
        found.append("confirm")
    if approved:
        found.append("to_reference")
    return found


@router.get("", response_model=ReconciliationSummary)
def reconciliation(session: SessionDep, user: ReconcilerDep) -> ReconciliationSummary:
    """Карточки, по которым решение принимает человек, — список дел.

    Автоматически склеенное сюда не попадает. Здесь только спорное: тёзки,
    похожие имена и то, чему пары нет вовсе. Подставить не тот ингредиент
    хуже, чем не подставить никакого.
    """
    # Явным циклом, а не через dict(): у SQLAlchemy строка результата
    # типизирована как Row, и dict() от неё mypy не принимает, а
    # dict-comprehension не принимает ruff.
    counts: dict[str, int] = {}
    for link_status, number in session.execute(
        select(models.IngredientCard.link_status, func.count())
        .where(models.IngredientCard.removed_at.is_(None))
        .group_by(models.IngredientCard.link_status)
    ).all():
        counts[link_status] = number

    cards = session.scalars(
        select(models.IngredientCard)
        .where(
            models.IngredientCard.link_status != "linked",
            models.IngredientCard.removed_at.is_(None),
        )
        .order_by(models.IngredientCard.link_status, models.IngredientCard.name)
    ).all()
    # Кандидаты — тем же подбором, что у импорта и у проверки «Это он»: из
    # этого списка человек выбирает, по нему же проверяют выбор.
    candidates = card_candidates(session, cards)

    rows: list[ReconciliationRow] = []
    for card in cards:
        approved = card.approval_status == APPROVED
        offered = candidates[card.id]
        rows.append(
            ReconciliationRow(
                card_id=card.id,
                name=card.name,
                link_status=card.link_status,
                supplier=card.supplier,
                approved=approved,
                candidates=[
                    CandidateRow(
                        ingredient_id=c.ingredient_id,
                        legacy_id=c.legacy_id,
                        name=c.name,
                        score=c.score,
                    )
                    for c in offered
                ],
                actions=_actions(approved, offered),
            )
        )

    total = sum(counts.values())
    linked = counts.get("linked", 0)
    return ReconciliationSummary(total=total, linked=linked, needs_human=total - linked, rows=rows)


# ---------------------------------------------------------------------------
# «Это он»
# ---------------------------------------------------------------------------
@router.post("/{card_id}/confirm", response_model=PairOut)
def confirm(card_id: CardId, user: ReconcilerDep, body: ConfirmIn, session: SessionDep) -> PairOut:
    """«Это он»: пара подтверждается только в базе и только с кандидатом
    карточки. Таблица не меняется."""
    pair = reference.confirm_pair(session, card_id, body.ingredient_id)
    if not pair.already:
        # Кто решил: пару импорт больше не пересчитывает, а в базе остаётся
        # только время подтверждения.
        log.info(
            "«Это он»: карточка %s — ингредиент %s (id %s), подтвердил профиль %s",
            card_id,
            pair.ingredient_id,
            pair.legacy_id,
            user.id,
        )
    return PairOut(
        card_id=pair.card_id,
        ingredient_id=pair.ingredient_id,
        legacy_id=pair.legacy_id,
        name=pair.name,
        already=pair.already,
        message=pair.message,
    )


# ---------------------------------------------------------------------------
# Строка ING для формы
# ---------------------------------------------------------------------------
def _number(value: PreviewValue) -> str | None:
    """Число строкой, без хвостовых нулей и экспоненты: 1250.50 → «1250.5»."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value


def _percent(value: PreviewValue) -> str | None:
    """Доля потерь из листа — процентами для формы: 0,125 → «12.5»."""
    if isinstance(value, Decimal):
        return _number(value * _HUNDRED)
    return value


def _text(value: PreviewValue) -> str:
    return "" if value is None else str(value)


def _pulled(seen: RowPreview) -> PulledOut | None:
    if seen.row is None:
        return None
    got = seen.pulled
    return PulledOut(
        category=_text(got.get("category")),
        name=_text(got.get("name")),
        full_name=_text(got.get("full_name")),
        manufacturer=_text(got.get("manufacturer")),
        composition=_text(got.get("composition")),
        protein=_number(got.get("protein")),
        fat=_number(got.get("fat")),
        carbs=_number(got.get("carbs")),
        kcal=_number(got.get("kcal")),
    )


def _current(seen: RowPreview) -> CurrentOut | None:
    if seen.row is None:
        return None
    now = seen.current
    return CurrentOut(
        short_name=_text(now.get("short_name")),
        unit=_text(now.get("unit")),
        status=_text(now.get("status")),
        price_per_kg=_number(now.get("price_per_kg")),
        price_per_pack=_number(now.get("price_per_pack")),
        weight_per_piece_g=_number(now.get("weight_per_piece_g")),
        losses_unpacking=_percent(now.get("losses_unpacking")),
        losses_cutting=_percent(now.get("losses_cutting")),
        losses_thermal=_percent(now.get("losses_thermal")),
    )


@router.get("/{card_id}/reference-row", response_model=ReferenceRowOut)
def reference_row(
    card_id: CardId, user: ReconcilerDep, session: SessionDep, filler: FillerDep
) -> ReferenceRowOut:
    """Строка ING карточки для формы переноса — свежее чтение, без записи.

    Строки нет или заполнить её нельзя — это 200 с ``reason`` и ``message``;
    отказы — карточки нет (404), лист сломан или книга не настроена (503),
    Google не ответил (502)."""
    seen = reference.preview(session, filler, card_id)
    return ReferenceRowOut(
        next_id=seen.next_id,
        row=seen.row,
        ready=seen.ready,
        reason=seen.reason,
        message=seen.message,
        ref_id=seen.ref_id,
        formulas=list(seen.formulas),
        pulled=_pulled(seen),
        current=_current(seen),
    )


# ---------------------------------------------------------------------------
# Перенос в справочник
# ---------------------------------------------------------------------------
@router.post("/{card_id}/to-reference", response_model=TransferOut)
def to_reference(
    card_id: CardId,
    user: ReconcilerDep,
    body: ReferenceFormIn,
    session: SessionDep,
    filler: FillerDep,
    books: KitchenImportDep,
) -> TransferOut:
    """«Добавить в справочник»: одна запись в строку ING, перенос книги кухни
    в базу и подтверждение пары.

    Повтор (ответ потерялся в сети) — та же строка, без второй записи: ключ
    запроса один на карточку."""
    done = reference.to_reference(
        session, filler, books, card_id, body.model_dump(), actor_id=user.id
    )
    return TransferOut(
        row=done.row,
        ref_id=done.ref_id,
        already=done.already,
        imported=done.imported,
        linked=done.linked,
        ingredient_id=done.ingredient_id,
        message=done.message,
        shifted=None
        if done.shifted is None
        else ShiftedOut(row=done.shifted.row, journal_id=done.shifted.id),
        notes=list(done.notes),
    )
