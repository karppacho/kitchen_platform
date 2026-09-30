"""Дневной бюджет на модель, лимит распознаваний на повара, журнал вызовов.

Сутки — с 00:00 по Москве: повар в 01:30 работает уже в новом дне, хотя по
UTC это ещё вчера. Всё считается по журналу ``llm_calls``:

* **бюджет** — сумма ``cost_rub`` всех вызовов за сутки, на всех вместе.
  Цена неизвестна (таймаут: модель могла поработать, а ответа нет) — вызов
  считается в :data:`UNKNOWN_COST_RUB`, а не бесплатным: иначе серия
  таймаутов тратила бы деньги мимо бюджета;
* **лимит** — сколько раз повар звал модель за сутки по этому делу, вместе
  с неудачными попытками.

Проверка и запись вызова — разные шаги, и два запроса разом могут оба
пройти проверку: бюджет превысится на один вызов. Это осознанно: частоту
распознаваний держит nginx, а блокировка ради копеек не окупается.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, select

from kitchen.db.models import LlmCall
from kitchen.domain.cards import MOSCOW
from kitchen.domain.shelf_life import plural_ru

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.orm import Session

    from kitchen.config import Settings

UNKNOWN_COST_RUB = Decimal("5")
"""Во сколько бюджет считает вызов, цена которого неизвестна."""

_ERROR_LIMIT = 1000

LimitKind = Literal["budget", "user_limit"]


class LlmLimitError(RuntimeError):
    """Вызов модели сейчас не разрешён: ``budget`` — дневной бюджет потрачен,
    ``user_limit`` — повар исчерпал свой лимит. Текст — для повара."""

    def __init__(self, kind: LimitKind, message: str) -> None:
        super().__init__(message)
        self.kind: LimitKind = kind


@dataclass(frozen=True, slots=True)
class LlmLimits:
    """Пределы распознавания этикеток на сутки."""

    daily_budget_rub: Decimal
    """На всех вместе."""
    calls_per_user_daily: int
    """На одного повара."""

    @classmethod
    def from_settings(cls, settings: Settings) -> LlmLimits:
        return cls(
            daily_budget_rub=Decimal(settings.llm_daily_budget_rub),
            calls_per_user_daily=settings.llm_label_calls_per_user_daily,
        )


def moscow_day_start(now: datetime) -> datetime:
    """Начало московских суток, в которых лежит ``now``.

    Москва — постоянное смещение из домена (:data:`kitchen.domain.cards.MOSCOW`),
    одно определение на всю платформу.
    """
    if now.tzinfo is None:
        raise ValueError("Время без часового пояса — московские сутки не определить")
    return now.astimezone(MOSCOW).replace(hour=0, minute=0, second=0, microsecond=0)


def ensure_allowed(
    session: Session,
    limits: LlmLimits,
    *,
    purpose: str,
    profile_id: uuid.UUID | None,
    now: datetime | None = None,
) -> None:
    """Можно ли звать модель сейчас; нельзя — :class:`LlmLimitError`.

    Звать до вызова, а не после: отказ стоит повару секунды, а вызов сверх
    бюджета — денег. ``now`` — для тестов; по умолчанию текущее время.
    """
    since = moscow_day_start(datetime.now(UTC) if now is None else now)

    known_or_estimated = func.coalesce(LlmCall.cost_rub, UNKNOWN_COST_RUB)
    spent = session.scalar(
        select(func.coalesce(func.sum(known_or_estimated), 0)).where(LlmCall.created_at >= since)
    )
    if Decimal(spent or 0) >= limits.daily_budget_rub:
        raise LlmLimitError(
            "budget",
            "Распознавание этикеток на сегодня закончилось: дневной бюджет потрачен. "
            "Заполните поля вручную — завтра распознавание снова заработает.",
        )

    if profile_id is None:
        return
    calls = session.scalar(
        select(func.count())
        .select_from(LlmCall)
        .where(
            LlmCall.created_at >= since,
            LlmCall.profile_id == profile_id,
            LlmCall.purpose == purpose,
        )
    )
    limit = limits.calls_per_user_daily
    if (calls or 0) >= limit:
        labels = plural_ru(limit, "этикетку", "этикетки", "этикеток")
        raise LlmLimitError(
            "user_limit",
            f"Вы сегодня распознали уже {limit} {labels} — это предел на день. "
            "Заполните поля вручную.",
        )


def record_call(
    session: Session,
    *,
    purpose: str,
    model: str,
    prompt_version: str,
    profile_id: uuid.UUID | None,
    ok: bool,
    error: str = "",
    cost_rub: Decimal | None = None,
    tokens: int | None = None,
    duration_ms: int | None = None,
) -> LlmCall:
    """Записать вызов в журнал — и удачный, и нет.

    Строка добавляется в сессию вызывающего; фиксирует транзакцию он. Если
    остальная работа может откатиться, вызов стоит записать отдельной
    транзакцией: деньги списаны в любом случае.
    """
    call = LlmCall(
        purpose=purpose,
        model=model,
        prompt_version=prompt_version,
        profile_id=profile_id,
        ok=ok,
        error=error[:_ERROR_LIMIT],
        cost_rub=cost_rub,
        tokens=tokens,
        duration_ms=duration_ms,
    )
    session.add(call)
    session.flush()
    return call
