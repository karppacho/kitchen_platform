"""Ответ `GET /api/sync`: насколько свежи данные на сайте.

Чистая функция отдельно от ручки: правила «когда данные устарели» и «что
показывать» проверяются тестом без базы и без часов. Считает сервер, а не
браузер: часы телефона могут врать.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel

from kitchen.sync.cycle import BOOK_TITLES

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class BookRow:
    """Строка `sync_state` в том объёме, что нужен ответу."""

    book: str
    checked_at: datetime | None
    changed_at: datetime | None
    problem: str | None
    problem_since: datetime | None


class SyncBook(BaseModel):
    book: str
    title: str
    checked_at: datetime | None
    changed_at: datetime | None
    stale: bool
    problem: str | None
    problem_since: datetime | None


class SyncStatus(BaseModel):
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
