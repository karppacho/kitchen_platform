"""Пары «карточка — ингредиент справочника»: кандидаты и подтверждение.

Пару предлагает импорт (:mod:`kitchen.sync.importer`) — по имени, через
:class:`~kitchen.domain.matching.NameIndex`. Подтверждает человек: «Это он»
на экране «Сверка» или перенос карточки в справочник. Подтверждённую пару
(``link_confirmed_at``) импорт больше не пересчитывает.

Справочник для подбора — один на оба пути (:class:`LinkReference`): по нему
импорт решает, спорная ли пара (``ambiguous``, ``candidate``), по нему же
«Сверка» показывает кандидатов, и «Это он» принимает только кандидата из
этого списка. Разойдись они — человек выбирал бы из одного списка, а
проверка шла бы по другому.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from kitchen.db import models
from kitchen.domain.matching import Entry, NameIndex

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from sqlalchemy.orm import Session, sessionmaker

LINKED, AMBIGUOUS, CANDIDATE, ORPHAN = models.IngredientCard.LINK_STATUSES


@dataclass(frozen=True, slots=True)
class LinkCandidate:
    """Ингредиент, который человек может выбрать парой карточке."""

    ingredient_id: int
    legacy_id: str
    """id в листе ING — как его видит шеф."""
    name: str
    score: float | None
    """Похожесть названий, от 0 до 1, — у похожих. У тёзок ``None``: название
    совпало точно."""


class LinkReference:
    """Справочник для подбора пар: ингредиенты, что есть в листе ING.

    Удалённое из листа (``removed_at``) в подбор не идёт: шеф уже сказал, что
    этой позиции больше нет. Архивное индекс сам не предлагает
    (:data:`~kitchen.domain.matching.ARCHIVED`).
    """

    def __init__(self, session: Session) -> None:
        rows = session.execute(
            select(
                models.Ingredient.id,
                models.Ingredient.legacy_id,
                models.Ingredient.name,
                models.Ingredient.status,
            )
            .where(models.Ingredient.removed_at.is_(None))
            .order_by(models.Ingredient.id)
        ).all()
        self._legacy = {str(key): legacy_id for key, legacy_id, _, _ in rows}
        self.index = NameIndex(
            Entry(key=str(key), name=name, status=status) for key, _, name, status in rows
        )

    def candidates(self, card: models.IngredientCard) -> tuple[LinkCandidate, ...]:
        """Кандидаты пары для карточки — из чего выбирать «Это он».

        * ``ambiguous`` — активные тёзки: точных совпадений несколько;
        * ``candidate`` — похожие названия (не больше
          :data:`~kitchen.domain.matching.MAX_CANDIDATES`), самые похожие
          первыми;
        * у прочих выбирать не из чего: ``linked`` уже связана, у ``orphan``
          пары в справочнике нет — её переносят в справочник.

        Подбор — по справочнику сейчас, а не по тому, что видел импорт: тёзку,
        которого с тех пор удалили из листа, выбрать нельзя.
        """
        match = self.index.match(card.name)
        found: list[tuple[Entry, float | None]]
        if card.link_status == AMBIGUOUS:
            found = [(entry, None) for entry in match.exact]
        elif card.link_status == CANDIDATE:
            found = [(similar.entry, similar.score) for similar in match.similar]
        else:
            return ()
        return tuple(
            LinkCandidate(
                ingredient_id=int(entry.key),
                legacy_id=self._legacy[entry.key],
                name=entry.name,
                score=score,
            )
            for entry, score in found
        )


def card_candidates(
    session: Session, cards: Iterable[models.IngredientCard]
) -> dict[int, tuple[LinkCandidate, ...]]:
    """Кандидаты пары для каждой карточки — по id карточки. Справочник
    читается один раз на всех: экран «Сверка» показывает список целиком."""
    reference = LinkReference(session)
    return {card.id: reference.candidates(card) for card in cards}


def live_card(session: Session, card_id: int) -> models.IngredientCard | None:
    """Карточка, что есть в книге карточек. Убранная из листа — ``None``:
    её не видно на сайте, и пару ей не подтверждают."""
    return session.scalar(
        select(models.IngredientCard).where(
            models.IngredientCard.id == card_id,
            models.IngredientCard.removed_at.is_(None),
        )
    )


def known_reference_ids(sessions: sessionmaker[Session]) -> list[str]:
    """id справочника, которые помнит база, — и удалённых из листа тоже.

    Источник для писателя строки ING (:mod:`kitchen.sync.reference_writer`):
    новый id не должен совпасть ни с одним из них. Шеф удалил строку с
    наибольшим id — в листе его больше нет, а в базе ингредиент только скрыт
    (``removed_at``), с ТТК и парами. Выдай перенос тот же id — импорт нашёл
    бы старый ингредиент по нему и «воскресил» под новым названием и ценами, а
    ТТК со старым id молча считались бы по чужим числам.

    Своя короткая транзакция: писатель зовёт это под очередью писателей,
    между запросами к Google.
    """
    with sessions() as session:
        return list(session.scalars(select(models.Ingredient.legacy_id)))


def ingredient_by_legacy_id(session: Session, legacy_id: str) -> models.Ingredient | None:
    """Ингредиент по id из листа ING — и удалённый из листа тоже: по нему
    видно, чьим был этот id при прошлом переносе."""
    return session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == legacy_id))


def confirm_candidate(
    session: Session, card: models.IngredientCard, ingredient_id: int, now: datetime
) -> LinkCandidate | None:
    """«Это он»: подтвердить пару, только если ингредиент — кандидат карточки
    (:meth:`LinkReference.candidates`). Не кандидат — ``None``, карточка не
    меняется."""
    chosen = next(
        (c for c in LinkReference(session).candidates(card) if c.ingredient_id == ingredient_id),
        None,
    )
    if chosen is not None:
        confirm_link(card, ingredient_id, now)
    return chosen


def confirm_link(card: models.IngredientCard, ingredient_id: int, now: datetime) -> None:
    """Пара подтверждена человеком: импорт её больше не пересчитывает."""
    card.ingredient_id = ingredient_id
    card.link_status = LINKED
    card.link_confirmed_at = now
