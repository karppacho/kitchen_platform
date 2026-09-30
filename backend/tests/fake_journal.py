"""Журнал записей в памяти — дублёр ``kitchen.db.journal.DbJournal``.

Офлайн-тестам писателя нужна не база, а то, на что писатель опирается:
очередь писателей с пределом ожидания и проверкой, что она ещё держится;
поиск открытой записи по ключу запроса и одна открытая запись на ключ — как
частичный уникальный индекс в базе; завершение записи только из ``pending``.
Настоящий журнал на Postgres проверяют интеграционные тесты.
"""

from __future__ import annotations

import itertools
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kitchen.db.journal import (
    FAILED,
    LAYOUT_UNCONFIRMED,
    OPEN_STATUSES,
    PENDING,
    ROLLED_BACK,
    VERIFIED,
    OpenWrite,
    SentWrite,
    WritersBusyError,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterator, Mapping
    from datetime import timedelta

    from kitchen.db.journal import NewWrite


@dataclass
class Record:
    """Строка журнала, как её видел бы разработчик в `sheet_writes`."""

    id: int
    book: str
    sheet: str
    row: int
    request_key: str
    actor_id: uuid.UUID | None
    before: Mapping[str, object]
    values: Mapping[str, object]
    status: str
    after: Mapping[str, object] | None = None
    content_hash: str | None = None
    error: str | None = None
    note: str | None = None
    finished: bool = False


class FakeLock:
    """Очередь писателей, которую держит писатель. ``alive`` тест может
    выключить — так выглядит сессия замка, убитая базой посреди записи."""

    def __init__(self, journal: FakeJournal) -> None:
        self._journal = journal

    def alive(self) -> bool:
        self._journal.alive_checks += 1
        return self._journal.lock_alive


class FakeJournal:
    """Журнал в памяти. Блокировка писателей — настоящая, с пределом ожидания:
    тест может занять её сам и проверить «таблица занята»."""

    def __init__(self) -> None:
        self.records: list[Record] = []
        self.lock = threading.Lock()
        self.lock_keys: list[int] = []
        self.holds: list[timedelta] = []
        self.lock_alive = True
        self.alive_checks = 0
        self._ids = itertools.count(1)

    @contextmanager
    def writers_lock(self, key: int, wait: timedelta, hold: timedelta) -> Iterator[FakeLock]:
        self.lock_keys.append(key)
        self.holds.append(hold)
        if not self.lock.acquire(timeout=wait.total_seconds()):
            raise WritersBusyError("блокировку писателей держит другой писатель")
        try:
            yield FakeLock(self)
        finally:
            self.lock.release()

    def find_open(self, request_key: str) -> OpenWrite | None:
        for record in self.records:
            if record.request_key == request_key and record.status in OPEN_STATUSES:
                return OpenWrite(
                    id=record.id,
                    row=record.row,
                    status=record.status,
                    before=dict(record.before),
                    values=dict(record.values),
                    after=dict(record.after) if record.after is not None else None,
                )
        return None

    def unconfirmed_attempts(self, request_key: str) -> tuple[SentWrite, ...]:
        return tuple(
            SentWrite(id=record.id, row=record.row, values=dict(record.values))
            for record in reversed(self.records)
            if record.request_key == request_key
            and record.status == FAILED
            and record.note == LAYOUT_UNCONFIRMED
        )

    def start(self, write: NewWrite) -> int:
        return self._add(write, PENDING).id

    def found(
        self,
        write: NewWrite,
        *,
        content_hash: str,
        note: str,
        after: Mapping[str, object] | None = None,
    ) -> int:
        record = self._add(write, VERIFIED)
        record.content_hash = content_hash
        record.note = note
        record.after = after
        record.finished = True
        return record.id

    def finish(
        self,
        write_id: int,
        *,
        status: str,
        content_hash: str | None = None,
        error: str | None = None,
        note: str | None = None,
        before: Mapping[str, object] | None = None,
        after: Mapping[str, object] | None = None,
    ) -> None:
        record = self.get(write_id)
        if record.status != PENDING:
            raise RuntimeError(f"запись журнала №{write_id} уже завершена: {record.status}")
        assert status in (VERIFIED, FAILED, ROLLED_BACK), status
        record.status = status
        record.content_hash = content_hash
        record.error = error
        record.note = note
        if before is not None:
            record.before = before
        record.after = after
        record.finished = True

    def annotate(self, write_id: int, *, error: str) -> None:
        record = self.get(write_id)
        if record.status != PENDING:
            raise RuntimeError(f"запись журнала №{write_id} уже завершена: {record.status}")
        record.error = error

    # --- для тестов ---------------------------------------------------------
    def get(self, write_id: int) -> Record:
        [record] = [record for record in self.records if record.id == write_id]
        return record

    def only(self) -> Record:
        """Единственная запись журнала — тест утверждает, что она одна."""
        [record] = self.records
        return record

    def _add(self, write: NewWrite, status: str) -> Record:
        # Как частичный уникальный индекс в базе: открытая запись на ключ одна.
        assert self.find_open(write.request_key) is None, (
            f"вторая открытая запись по ключу «{write.request_key}»"
        )
        record = Record(
            id=next(self._ids),
            book=write.book,
            sheet=write.sheet,
            row=write.row,
            request_key=write.request_key,
            actor_id=write.actor_id,
            before=write.before,
            values=write.values,
            status=status,
        )
        self.records.append(record)
        return record
