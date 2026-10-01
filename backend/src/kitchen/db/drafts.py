"""Черновики карточек ингредиентов — в базе, и что мастеру нужно из карточек.

Здесь только запросы. Что можно менять, в каком порядке ходить в Drive и
когда фото отправляется в корзину, решает слой приложения
(``kitchen.cards.drafts``).

Черновик всегда ищется вместе с владельцем: чужой для запроса не
существует. Поэтому у функций нет варианта «по одному id».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Interval, and_, cast, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from kitchen.db import models

if TYPE_CHECKING:
    import uuid
    from datetime import datetime, timedelta

    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import Session

ACTIVE = "active"
SUBMITTED = "submitted"
CANCELLED = "cancelled"

RUNNING = "running"
DONE = "done"
FAILED = "failed"
"""Состояния распознавания этикетки (пусто — не запускали)."""

ACTIVE_OWNER_INDEX = "ux_card_drafts_active_owner"
"""Индекс «один активный черновик на повара» — по его имени узнаётся отказ."""


class ActiveDraftExistsError(RuntimeError):
    """У повара уже есть активный черновик."""


@dataclass(frozen=True, slots=True)
class CardName:
    """Карточка из листа в том объёме, что нужен подсказкам названия."""

    name: str
    supplier: str
    hidden: bool
    """Строку убрали из листа: на сайте её нет, но имя помнится."""


@dataclass(frozen=True, slots=True)
class ReferenceName:
    """Позиция справочника ING — только имя и статус, без цен."""

    key: str
    name: str
    status: str


def active_draft(
    session: Session, owner_id: uuid.UUID, draft_id: uuid.UUID | None = None, *, lock: bool = False
) -> models.CardDraft | None:
    """Активный черновик повара; с ``draft_id`` — только если это он.

    ``lock`` — взять строку на изменение (``FOR UPDATE``): две правки одного
    черновика разом не теряют друг друга, а замена фото видит слот таким,
    каким его оставила соседняя.
    """
    query = select(models.CardDraft).where(
        models.CardDraft.owner_id == owner_id,
        models.CardDraft.status == ACTIVE,
    )
    if draft_id is not None:
        query = query.where(models.CardDraft.id == draft_id)
    if lock:
        query = query.with_for_update()
    return session.scalar(query)


def add_draft(session: Session, owner_id: uuid.UUID) -> models.CardDraft:
    """Новый активный черновик. Уже есть — :class:`ActiveDraftExistsError`.

    Проверка «есть ли уже» — сам уникальный индекс: проверка запросом перед
    вставкой не закрыла бы гонку двух «Начать» с двух вкладок. После отказа
    транзакция откатывается — в ней всё равно ничего, кроме этой вставки.
    """
    draft = models.CardDraft(owner_id=owner_id)
    session.add(draft)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        diag = getattr(error.orig, "diag", None)
        if getattr(diag, "constraint_name", None) == ACTIVE_OWNER_INDEX:
            raise ActiveDraftExistsError from error
        raise
    return draft


def own_draft(
    session: Session, owner_id: uuid.UUID, draft_id: uuid.UUID
) -> models.CardDraft | None:
    """Черновик повара в любом состоянии — чтобы отличить «уже отправлен» от
    «нет такого». Чужой — ``None``, как несуществующий."""
    return session.scalar(
        select(models.CardDraft).where(
            models.CardDraft.id == draft_id, models.CardDraft.owner_id == owner_id
        )
    )


# ---------------------------------------------------------------------------
# Отправка в лист: отметка «идёт»
#
# Часы — базы (`now()`), а не процесса: отметку ставит и читает база, и
# расхождение часов контейнеров не делает свежую отметку зависшей.
# ---------------------------------------------------------------------------
def _not_submitting(window: timedelta) -> ColumnElement[bool]:
    """Отправка не идёт: отметки нет или она старше ``window``."""
    return or_(
        models.CardDraft.submit_started_at.is_(None),
        models.CardDraft.submit_started_at < func.now() - window,
    )


def _not_recognizing(stale_after: timedelta) -> ColumnElement[bool]:
    """Распознавание не идёт: не «идёт» или «идёт» старше ``stale_after``."""
    return or_(
        models.CardDraft.recognition_status.is_distinct_from(RUNNING),
        models.CardDraft.recognition_started_at < func.now() - stale_after,
    )


def claim_submit(
    session: Session,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    *,
    window: timedelta,
    recognition_stale: timedelta,
) -> models.CardDraft | None:
    """Поставить отметку «отправка идёт» и взять черновик, каким он ушёл в лист.

    Одним запросом: отметка ставится, только если её нет или она старше
    ``window`` (процесс умер посреди записи), — вторая отправка разом её не
    поставит, — и если не идёт распознавание этикетки (его итог лёг бы в
    черновик, уже ушедший в лист, и пропал бы). Поля и id фото берутся из
    этого же запроса: замена фото после него ждёт снятия отметки. ``None`` —
    черновика нет, он не активен, отправка или распознавание уже идут.
    Коммитит вызывающий — до записи в лист.
    """
    statement = (
        update(models.CardDraft)
        .where(
            models.CardDraft.id == draft_id,
            models.CardDraft.owner_id == owner_id,
            models.CardDraft.status == ACTIVE,
            _not_submitting(window),
            _not_recognizing(recognition_stale),
        )
        .values(submit_started_at=func.now())
        .returning(models.CardDraft)
        .execution_options(populate_existing=True)
    )
    return session.scalars(statement).one_or_none()


def submitting(session: Session, draft_id: uuid.UUID, window: timedelta) -> bool:
    """Идёт ли отправка черновика сейчас — отметка моложе ``window``."""
    query = select(models.CardDraft.submit_started_at >= func.now() - window).where(
        models.CardDraft.id == draft_id
    )
    return bool(session.scalar(query))


def submit_left(session: Session, draft_id: uuid.UUID, window: timedelta) -> timedelta | None:
    """Сколько свежей отметке отправки осталось до предела; ``None`` — отметки нет."""
    query = select(cast(models.CardDraft.submit_started_at + window - func.now(), Interval)).where(
        models.CardDraft.id == draft_id
    )
    left: timedelta | None = session.scalar(query)
    return left


def open_sheet_write(session: Session, request_key: str) -> str | None:
    """Статус открытой записи журнала по ключу — ``pending`` или ``verified``
    (она одна: частичный уникальный индекс); ``None`` — открытой нет."""
    query = select(models.SheetWrite.status).where(
        models.SheetWrite.request_key == request_key,
        models.SheetWrite.status.in_(models.SheetWrite.OPEN_STATUSES),
    )
    status: str | None = session.scalar(query)
    return status


def card_in_database(session: Session, *, label_url: str, row: int, name: str) -> bool:
    """Перенесена ли карточка в базу: по ссылке на её этикетку — она своя у
    каждой отправки, — или по строке листа с тем же названием."""
    query = select(
        exists().where(
            models.IngredientCard.removed_at.is_(None),
            or_(
                models.IngredientCard.label_url == label_url,
                and_(
                    models.IngredientCard.source_row == row,
                    models.IngredientCard.name == name,
                ),
            ),
        )
    )
    return bool(session.scalar(query))


def end_submit(session: Session, draft_id: uuid.UUID) -> None:
    """Снять отметку «отправка идёт»: запись не состоялась, черновик активен."""
    session.execute(
        update(models.CardDraft)
        .where(models.CardDraft.id == draft_id)
        .values(submit_started_at=None)
        .execution_options(synchronize_session=False)
    )


def mark_submitted(session: Session, draft_id: uuid.UUID, *, row: int, sheet_write_id: int) -> bool:
    """Черновик отправлен: строка в листе и запись журнала. ``False`` —
    черновик уже не активен (его закрыли, пока шла запись)."""
    result = session.execute(
        update(models.CardDraft)
        .where(models.CardDraft.id == draft_id, models.CardDraft.status == ACTIVE)
        .values(
            status=SUBMITTED,
            submitted_row=row,
            submitted_at=func.now(),
            sheet_write_id=sheet_write_id,
            submit_started_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Распознавание этикетки: отметка «идёт»
# ---------------------------------------------------------------------------
def recognition_running(session: Session, draft_id: uuid.UUID, stale_after: timedelta) -> bool:
    """Идёт ли распознавание сейчас — «идёт» моложе ``stale_after``."""
    query = select(
        and_(
            models.CardDraft.recognition_status == RUNNING,
            models.CardDraft.recognition_started_at >= func.now() - stale_after,
        )
    ).where(models.CardDraft.id == draft_id)
    return bool(session.scalar(query))


def start_recognition(
    session: Session,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    label_file_id: str,
    *,
    stale_after: timedelta,
    submit_window: timedelta,
) -> datetime | None:
    """Отметить «распознавание идёт» — если не идёт (или зависло дольше
    ``stale_after``), этикетка та же, что скачана для модели, и черновик не
    отправляется (итог распознавания лёг бы в уже ушедший в лист и пропал).

    Отдаёт метку начала: по ней результат узнаёт, что он всё ещё про этот
    запуск. ``None`` — не начато. Коммитит вызывающий.
    """
    statement = (
        update(models.CardDraft)
        .where(
            models.CardDraft.id == draft_id,
            models.CardDraft.owner_id == owner_id,
            models.CardDraft.status == ACTIVE,
            models.CardDraft.label_file_id == label_file_id,
            _not_recognizing(stale_after),
            _not_submitting(submit_window),
        )
        .values(recognition_status=RUNNING, recognition_started_at=func.now())
        .returning(models.CardDraft.recognition_started_at)
        .execution_options(synchronize_session=False)
    )
    started: datetime | None = session.scalar(statement)
    return started


def sheet_write_exists(session: Session, request_key: str) -> bool:
    """Была ли хоть одна попытка записи в лист с этим ключом — в любом статусе.

    Даже неудачная попытка могла оставить строку в листе (исход неясен), а в
    ней — ссылки на фото черновика.
    """
    query = select(exists().where(models.SheetWrite.request_key == request_key))
    return bool(session.scalar(query))


def card_categories(session: Session) -> list[str]:
    """Категории карточек, что есть в листе, — по одной на карточку."""
    query = select(models.IngredientCard.category).where(models.IngredientCard.removed_at.is_(None))
    return list(session.scalars(query).all())


def card_suppliers(session: Session) -> list[str]:
    """Поставщики карточек, что есть в листе, — по одному на карточку."""
    query = select(models.IngredientCard.supplier).where(models.IngredientCard.removed_at.is_(None))
    return list(session.scalars(query).all())


def card_names(session: Session) -> list[CardName]:
    """Все карточки — и убранные из листа: их имена тоже стоит знать повару."""
    query = select(
        models.IngredientCard.name,
        models.IngredientCard.supplier,
        models.IngredientCard.removed_at,
    ).order_by(models.IngredientCard.name)
    return [
        CardName(name=name, supplier=supplier, hidden=removed_at is not None)
        for name, supplier, removed_at in session.execute(query).all()
    ]


def reference_names(session: Session) -> list[ReferenceName]:
    """Позиции справочника, что есть в листе ING, — имена и статусы, без цен."""
    query = (
        select(models.Ingredient.legacy_id, models.Ingredient.name, models.Ingredient.status)
        .where(models.Ingredient.removed_at.is_(None))
        .order_by(models.Ingredient.name)
    )
    return [
        ReferenceName(key=key, name=name, status=status)
        for key, name, status in session.execute(query).all()
    ]
