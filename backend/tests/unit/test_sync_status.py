"""Свежесть данных для сайта: правила без базы и без часов."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from kitchen.config import Settings
from kitchen.web.app import create_app
from kitchen.web.sync_status import BookRow, build_sync_status

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
STALE_AFTER = timedelta(minutes=15)


def _book(
    book: str,
    checked_ago: timedelta | None,
    *,
    changed_ago: timedelta | None = None,
    problem: str | None = None,
) -> BookRow:
    return BookRow(
        book=book,
        checked_at=None if checked_ago is None else NOW - checked_ago,
        changed_at=None if changed_ago is None else NOW - changed_ago,
        problem=problem,
        problem_since=None if problem is None else NOW - timedelta(minutes=30),
    )


def test_fresh_books() -> None:
    status = build_sync_status(
        [
            _book("kitchen", timedelta(minutes=2), changed_ago=timedelta(hours=1)),
            _book("ingredient_cards", timedelta(minutes=1), changed_ago=timedelta(hours=3)),
        ],
        NOW,
        STALE_AFTER,
    )

    assert status.stale is False
    assert status.data_as_of == NOW - timedelta(minutes=2), (
        "самая ранняя проверка — не приукрашиваем"
    )
    assert status.changed_at == NOW - timedelta(hours=1), (
        "самое позднее изменение — повод перезапросить"
    )
    assert [b.title for b in status.books] == ["таблица кухни", "карточки ингредиентов"]


def test_stale_means_older_than_threshold() -> None:
    """«Старше 15 минут»: ровно 15 — ещё свежо, на секунду больше — уже нет."""
    fresh = _book("ingredient_cards", timedelta(0))
    at_limit = build_sync_status([_book("kitchen", STALE_AFTER), fresh], NOW, STALE_AFTER)
    past = build_sync_status(
        [_book("kitchen", STALE_AFTER + timedelta(seconds=1)), fresh], NOW, STALE_AFTER
    )

    assert at_limit.stale is False
    assert past.stale is True


def test_one_book_behind_with_reason() -> None:
    status = build_sync_status(
        [
            _book("kitchen", timedelta(minutes=2)),
            _book(
                "ingredient_cards",
                timedelta(minutes=55),
                problem="доступ платформы к таблице закрыт",
            ),
        ],
        NOW,
        STALE_AFTER,
    )
    books = {b.book: b for b in status.books}

    assert status.stale is True
    assert books["ingredient_cards"].stale is True
    assert books["ingredient_cards"].problem == "доступ платформы к таблице закрыт"
    assert books["kitchen"].stale is False, "свежая книга в полосу не попадает"


def test_never_synced() -> None:
    status = build_sync_status([], NOW, STALE_AFTER)

    assert status.data_as_of is None
    assert status.stale is True
    assert all(b.stale and b.checked_at is None for b in status.books)


def test_book_never_checked_leaves_no_date() -> None:
    """Первая выкладка: кухня перенесена, карточки не открылись ни разу.

    Дата кухни в строке пообещала бы свежесть и карточкам — а их данных нет.
    """
    status = build_sync_status(
        [
            _book("kitchen", timedelta(minutes=2)),
            _book("ingredient_cards", None, problem="доступ платформы к таблице закрыт"),
        ],
        NOW,
        STALE_AFTER,
    )

    assert status.data_as_of is None
    assert status.stale is True


def test_sync_requires_login() -> None:
    reply = TestClient(create_app(Settings(app_env="test"))).get("/api/sync")

    assert reply.status_code == 401
