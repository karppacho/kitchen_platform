"""Свежесть данных: насколько свежи книги на сайте.

Чистая функция отдельно от чтения базы: правила «когда данные устарели» и
«что показывать» проверяются тестом без базы и без часов. Считает сервер, а не
браузер: часы телефона могут врать.

Общее для сайта (``GET /api/sync``) и MCP (``svezhest``), поэтому лежит в слое
приложения, а не в ``web``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel
from sqlalchemy import select

from kitchen.db import models
from kitchen.sync.cycle import BOOK_TITLES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class BookRow:
    """Строка `sync_state` в том объёме, что нужен ответу."""

    book: str
    checked_at: datetime | None
    changed_at: datetime | None
    problem: str | None
    problem_since: datetime | None


# Any здесь не наш: его вносит конструктор, который pydantic объявляет любой
# модели (``**data: Any``). Поля моделей — конкретные типы.
class SyncBook(BaseModel):  # type: ignore[explicit-any]
    book: str
    title: str
    checked_at: datetime | None
    changed_at: datetime | None
    stale: bool
    problem: str | None
    problem_since: datetime | None


class SyncStatus(BaseModel):  # type: ignore[explicit-any]
    data_as_of: datetime | None
    """Самая ранняя проверка из книг: строка на сайте не обещает свежести,
    которой нет."""

    changed_at: datetime | None
    """Самое позднее изменение: по нему экраны понимают, что пора
    перезапросить данные."""

    stale: bool
    books: list[SyncBook]


def build_sync_status(rows: Sequence[BookRow], now: datetime, stale_after: timedelta) -> SyncStatus:
    by_book = {row.book: row for row in rows}
    books: list[SyncBook] = []
    for book, title in BOOK_TITLES.items():
        row = by_book.get(book)
        checked = row.checked_at if row else None
        books.append(
            SyncBook(
                book=book,
                title=title,
                checked_at=checked,
                changed_at=row.changed_at if row else None,
                stale=checked is None or now - checked > stale_after,
                problem=row.problem if row else None,
                problem_since=row.problem_since if row else None,
            )
        )

    checked_all = [b.checked_at for b in books]
    changed = [b.changed_at for b in books if b.changed_at is not None]
    return SyncStatus(
        data_as_of=None if None in checked_all else min(c for c in checked_all if c is not None),
        changed_at=max(changed) if changed else None,
        stale=any(b.stale for b in books),
        books=books,
    )


def read_sync_status(session: Session, stale_after_seconds: int) -> SyncStatus:
    """Свежесть данных по ``sync_state`` — одним чтением, часы берутся здесь."""
    rows = [
        BookRow(
            book=state.book,
            checked_at=state.checked_at,
            changed_at=state.changed_at,
            problem=state.problem,
            problem_since=state.problem_since,
        )
        for state in session.scalars(select(models.SyncState)).all()
    ]
    return build_sync_status(rows, datetime.now(UTC), timedelta(seconds=stale_after_seconds))
