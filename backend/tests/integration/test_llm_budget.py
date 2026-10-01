"""Журнал вызовов модели и дневной бюджет — на настоящем Postgres.

Сутки бюджета начинаются в 00:00 по Москве: повар в 01:30 уже в новом дне,
хотя по UTC это ещё вчера. Лимит — на каждого повара отдельно, бюджет — на
всех вместе. Вызов с неизвестной ценой (таймаут: модель могла поработать)
считается по оценке, а не бесплатным.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect, select

from kitchen.db.models import LlmCall, Profile
from kitchen.domain.cards import MOSCOW
from kitchen.llm.budget import (
    UNKNOWN_COST_RUB,
    LlmLimitError,
    LlmLimits,
    ensure_allowed,
    moscow_day_start,
    record_call,
)
from kitchen.llm.label import LABEL_PROMPT_VERSION, LABEL_PURPOSE

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

LIMITS = LlmLimits(daily_budget_rub=Decimal("300"), calls_per_user_daily=40)


def _msk(day: int, hour: int, minute: int = 0) -> datetime:
    """Момент по Москве, 1–2 октября 2026 (30 — сентября)."""
    month = 9 if day == 30 else 10
    return datetime(2026, month, day, hour, minute, tzinfo=MOSCOW)


def _profile(session: Session, name: str) -> uuid.UUID:
    profile = Profile(id=uuid.uuid4(), email=f"{name}@example.com", display_name=name)
    session.add(profile)
    session.flush()
    return profile.id


def _call(
    session: Session,
    at: datetime,
    *,
    cost: str | None,
    profile_id: uuid.UUID | None = None,
    purpose: str = LABEL_PURPOSE,
    ok: bool = True,
) -> None:
    session.add(
        LlmCall(
            created_at=at,
            purpose=purpose,
            model="qwen/qwen3.6-plus",
            prompt_version=LABEL_PROMPT_VERSION,
            profile_id=profile_id,
            ok=ok,
            error="" if ok else "unavailable: не ответил",
            cost_rub=None if cost is None else Decimal(cost),
            tokens=1200,
            duration_ms=4000,
        )
    )
    session.flush()


def _allowed(
    session: Session, now: datetime, profile_id: uuid.UUID | None = None, limits: LlmLimits = LIMITS
) -> bool:
    try:
        ensure_allowed(session, limits, purpose=LABEL_PURPOSE, profile_id=profile_id, now=now)
    except LlmLimitError:
        return False
    return True


# ---------------------------------------------------------------------------
# Сутки по Москве
# ---------------------------------------------------------------------------
def test_moscow_day_start() -> None:
    """00:30 по Москве 1 октября — это ещё 30 сентября по UTC, но сутки уже новые."""
    assert moscow_day_start(_msk(1, 0, 30)) == _msk(1, 0)
    assert moscow_day_start(datetime(2026, 9, 30, 21, 30, tzinfo=UTC)) == _msk(1, 0)
    assert moscow_day_start(datetime(2026, 9, 30, 20, 59, tzinfo=UTC)) == _msk(30, 0)
    with pytest.raises(ValueError, match="часового пояса"):
        moscow_day_start(datetime(2026, 10, 1, 12, 0))  # noqa: DTZ001 — проверяем отказ


@pytest.mark.integration
def test_yesterday_evening_in_moscow_does_not_count(sessions: sessionmaker[Session]) -> None:
    """В 02:00 по Москве вчерашний вечер не считается, хотя по UTC это те же сутки."""
    with sessions.begin() as session:
        _call(session, _msk(30, 22), cost="300")

        assert _allowed(session, _msk(1, 2))


@pytest.mark.integration
def test_after_moscow_midnight_counts(sessions: sessionmaker[Session]) -> None:
    """01:00 по Москве — уже сегодня, хотя по UTC это ещё вчера."""
    with sessions.begin() as session:
        _call(session, _msk(1, 1), cost="300")

        assert not _allowed(session, _msk(1, 10))


# ---------------------------------------------------------------------------
# Бюджет
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_budget_is_shared_by_everyone(sessions: sessionmaker[Session]) -> None:
    """Бюджет — на всех: два повара вместе потратили 300 ₽ — третьему отказ."""
    with sessions.begin() as session:
        first, second, third = (_profile(session, name) for name in ("a", "b", "c"))
        _call(session, _msk(1, 9), cost="200.5", profile_id=first)
        _call(session, _msk(1, 10), cost="99.4999", profile_id=second)

        assert _allowed(session, _msk(1, 11), third)

        _call(session, _msk(1, 11), cost="0.0001", profile_id=second)

        with pytest.raises(LlmLimitError) as caught:
            ensure_allowed(
                session, LIMITS, purpose=LABEL_PURPOSE, profile_id=third, now=_msk(1, 12)
            )
    assert caught.value.kind == "budget"
    assert "вручную" in str(caught.value)


@pytest.mark.integration
def test_unknown_cost_counts_as_five_rubles(sessions: sessionmaker[Session]) -> None:
    """Цена неизвестна — считается 5 ₽: таймаут не бесплатен, модель могла поработать."""
    assert Decimal("5") == UNKNOWN_COST_RUB
    limits = LlmLimits(daily_budget_rub=Decimal("10"), calls_per_user_daily=40)
    with sessions.begin() as session:
        _call(session, _msk(1, 9), cost=None, ok=False)

        assert _allowed(session, _msk(1, 10), limits=limits)

        _call(session, _msk(1, 9, 30), cost=None, ok=False)

        assert not _allowed(session, _msk(1, 10), limits=limits)


@pytest.mark.integration
def test_refused_calls_cost_nothing(sessions: sessionmaker[Session]) -> None:
    """polza.ai отказал сразу (ключ, деньги) — цена 0, бюджет цел."""
    limits = LlmLimits(daily_budget_rub=Decimal("10"), calls_per_user_daily=40)
    with sessions.begin() as session:
        for minute in range(5):
            _call(session, _msk(1, 9, minute), cost="0", ok=False)

        assert _allowed(session, _msk(1, 10), limits=limits)


# ---------------------------------------------------------------------------
# Лимит на повара
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_limit_is_per_cook(sessions: sessionmaker[Session]) -> None:
    """Лимит — на каждого повара: неудачные попытки тоже считаются, чужие — нет,
    как и вызовы модели для других дел."""
    limits = LlmLimits(daily_budget_rub=Decimal("300"), calls_per_user_daily=3)
    with sessions.begin() as session:
        cook, other = _profile(session, "cook"), _profile(session, "other")
        _call(session, _msk(1, 9), cost="1", profile_id=cook)
        _call(session, _msk(1, 9, 5), cost=None, profile_id=cook, ok=False)
        _call(session, _msk(1, 9, 10), cost="1", profile_id=cook, purpose="chat")
        _call(session, _msk(30, 23), cost="1", profile_id=cook)

        assert _allowed(session, _msk(1, 10), cook, limits)

        _call(session, _msk(1, 9, 20), cost="1", profile_id=cook)

        with pytest.raises(LlmLimitError) as caught:
            ensure_allowed(session, limits, purpose=LABEL_PURPOSE, profile_id=cook, now=_msk(1, 10))
        assert _allowed(session, _msk(1, 10), other, limits)
    assert caught.value.kind == "user_limit"
    assert "3 этикетки" in str(caught.value)


def test_limits_from_settings() -> None:
    from kitchen.config import Settings

    settings = Settings(_env_file=None, llm_daily_budget_rub=250, llm_label_calls_per_user_daily=7)

    assert LlmLimits.from_settings(settings) == LlmLimits(
        daily_budget_rub=Decimal("250"), calls_per_user_daily=7
    )


# ---------------------------------------------------------------------------
# Журнал
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_record_call_keeps_exact_cost(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        cook = _profile(session, "cook")
        record_call(
            session,
            purpose=LABEL_PURPOSE,
            model="qwen/qwen3.6-plus",
            prompt_version=LABEL_PROMPT_VERSION,
            profile_id=cook,
            ok=True,
            cost_rub=Decimal("0.0123"),
            tokens=1200,
            duration_ms=4321,
        )
        record_call(
            session,
            purpose=LABEL_PURPOSE,
            model="qwen/qwen3.6-plus",
            prompt_version=LABEL_PROMPT_VERSION,
            profile_id=None,
            ok=False,
            error="unavailable: polza.ai не отвечает",
        )

    with sessions() as session:
        good, bad = session.scalars(select(LlmCall).order_by(LlmCall.id)).all()
    assert good.cost_rub == Decimal("0.0123")
    assert isinstance(good.cost_rub, Decimal)
    assert (good.ok, good.error, good.tokens, good.duration_ms) == (True, "", 1200, 4321)
    assert good.profile_id == cook
    assert good.created_at is not None
    assert abs(good.created_at - datetime.now(UTC)) < timedelta(minutes=5)
    assert (bad.ok, bad.cost_rub, bad.tokens, bad.profile_id) == (False, None, None, None)
    assert bad.error == "unavailable: polza.ai не отвечает"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("cost", "unpriced", "stored"),
    [
        ("0.0123", 1, "5.0123"),
        (None, 2, "10"),
        ("0", 1, "5"),
        ("0", 0, "0"),
        (None, 0, None),
    ],
)
def test_record_call_adds_estimate_for_unpriced_attempts(
    sessions: sessionmaker[Session], cost: str | None, unpriced: int, stored: str | None
) -> None:
    """Таймаут, потом успех: вторая попытка стоила 0,0123 ₽, первая — неизвестно
    сколько, считается в 5 ₽. Журнал видит одну строку на распознавание, и цена
    первой попытки в ней не теряется."""
    with sessions.begin() as session:
        record_call(
            session,
            purpose=LABEL_PURPOSE,
            model="qwen/qwen3.6-plus",
            prompt_version=LABEL_PROMPT_VERSION,
            profile_id=None,
            ok=True,
            cost_rub=None if cost is None else Decimal(cost),
            unpriced_attempts=unpriced,
        )
    with sessions() as session:
        [call] = session.scalars(select(LlmCall)).all()
    assert call.cost_rub == (None if stored is None else Decimal(stored))


@pytest.mark.integration
def test_deleting_a_profile_keeps_the_journal(sessions: sessionmaker[Session]) -> None:
    """Повара удалили — траты остались: деньги потрачены, бюджет их помнит."""
    with sessions.begin() as session:
        cook = _profile(session, "cook")
        _call(session, _msk(1, 9), cost="1.5", profile_id=cook)
    with sessions.begin() as session:
        session.delete(session.get(Profile, cook))
    with sessions() as session:
        [call] = session.scalars(select(LlmCall)).all()
    assert call.profile_id is None
    assert call.cost_rub == Decimal("1.5")


@pytest.mark.integration
def test_llm_calls_schema(sessions: sessionmaker[Session]) -> None:
    """Внешний ключ на профиль — SET NULL и с индексом; стоимость — Numeric(12,4)."""
    with sessions() as session:
        inspector = inspect(session.get_bind())
        [foreign] = inspector.get_foreign_keys("llm_calls")
        indexed = {tuple(index["column_names"]) for index in inspector.get_indexes("llm_calls")}
        cost = {c["name"]: c for c in inspector.get_columns("llm_calls")}["cost_rub"]
    assert foreign["referred_table"] == "profiles"
    assert foreign["constrained_columns"] == ["profile_id"]
    assert foreign["options"].get("ondelete") == "SET NULL"
    assert ("profile_id",) in indexed
    assert ("created_at",) in indexed
    assert (cost["type"].precision, cost["type"].scale) == (12, 4)
