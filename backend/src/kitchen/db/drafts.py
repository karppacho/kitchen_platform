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

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from kitchen.db import models

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

ACTIVE = "active"
SUBMITTED = "submitted"
CANCELLED = "cancelled"

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
