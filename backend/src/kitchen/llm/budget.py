"""Дневной бюджет на модель, лимит распознаваний на повара, журнал вызовов.

Сутки — с 00:00 по Москве: повар в 01:30 работает уже в новом дне, хотя по
UTC это ещё вчера. Всё считается по журналу ``llm_calls``:

* **бюджет** — за все вызовы за сутки, на всех вместе: ``cost_rub``
  (неизвестная цена, ``NULL``, — :data:`UNKNOWN_COST_RUB`) плюс
  :data:`UNKNOWN_COST_RUB` за каждую из ``unpriced_attempts`` — неудачных
  попыток перед последней, цена которых неизвестна. Таймаут не бесплатен:
  модель могла поработать, и серия таймаутов иначе тратила бы деньги мимо
  бюджета. Оценка в журнал не вписывается — по нему видно, что списано, а
  что оценено;
* **лимит** — сколько раз повар звал модель за сутки по этому делу, вместе
  с неудачными попытками.

Проверка идёт до вызова, запись — после, и между ними — вся длительность
вызова, до трёх минут. Все вызовы, начатые в это окно, видят бюджет без
друг друга: перерасход — на столько вызовов, сколько шло одновременно.
Блокировки нет осознанно: частоту распознаваний держит nginx, перерасход —
рубли, а блокировка на время вызова модели держала бы очередь поваров.
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
"""Во сколько бюджет считает попытку, цена которой неизвестна."""

_COST_CEILING = Decimal("100000000")
"""С этой цены — не цена: колонка ``Numeric(12,4)`` её не вместит."""
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

    known_or_estimated = (
        func.coalesce(LlmCall.cost_rub, UNKNOWN_COST_RUB)
        + LlmCall.unpriced_attempts * UNKNOWN_COST_RUB
    )
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
    unpriced_attempts: int,
    error: str = "",
    cost_rub: Decimal | None = None,
    tokens: int | None = None,
    duration_ms: int | None = None,
) -> LlmCall:
    """Записать вызов в журнал — и удачный, и нет.

    ``cost_rub`` и ``unpriced_attempts`` — из ``LlmReply`` или ``LlmError``, как
    есть: что списал polza.ai за последнюю попытку и сколько неудачных попыток
    перед ней стоили неизвестно сколько. Таймаут, потом успех за 0,0123 ₽ —
    ``cost_rub=0.0123``, ``unpriced_attempts=1``; бюджет посчитает 5,0123 ₽.
    ``unpriced_attempts`` обязателен: забытый, он молча занизил бы бюджет.
    Нелепая цена (отрицательная, бесконечная, от 10⁸ ₽) пишется как
    неизвестная — вставка в ``Numeric(12,4)`` не падает, вызов не выпадает из
    бюджета.

    Строка добавляется в сессию вызывающего; фиксирует транзакцию он. Если
    остальная работа может откатиться, вызов стоит записать отдельной
    транзакцией: деньги списаны в любом случае.
    """
    if unpriced_attempts < 0:
        raise ValueError(f"unpriced_attempts не может быть меньше нуля: {unpriced_attempts}")
    if cost_rub is not None and not (
        cost_rub.is_finite() and Decimal("0") <= cost_rub < _COST_CEILING
    ):
        cost_rub = None
    call = LlmCall(
        purpose=purpose,
        model=model,
        prompt_version=prompt_version,
        profile_id=profile_id,
        ok=ok,
        error=error[:_ERROR_LIMIT],
        cost_rub=cost_rub,
        unpriced_attempts=unpriced_attempts,
        tokens=tokens,
        duration_ms=duration_ms,
    )
    session.add(call)
    session.flush()
    return call
