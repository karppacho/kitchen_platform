"""Журнал записей в Google-таблицы и очередь писателей — в базе.

Писатель строки (``kitchen.sync.writer``) решает, что писать в лист и как
проверить записанное; здесь — то, что должно пережить его процесс:

* **очередь писателей** — advisory-блокировка на время одной записи. Два
  повара отправили карточки в одну секунду — второй ждёт, пока первый
  запишет и сверит, и читает лист уже после него;
* **журнал** `sheet_writes` — след каждой записи. Каждое действие с ним —
  своя транзакция и свой коммит: запись ``pending`` обязана лечь в базу до
  того, как что-то уйдёт в лист, а блокировка тем временем держится в своей,
  отдельной транзакции.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from kitchen.db import models

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterator, Mapping

    from sqlalchemy.orm import Session, sessionmaker

PENDING = "pending"
"""Запись в лист начата: снимок «до» уже в журнале, исход ещё не известен."""
VERIFIED = "verified"
"""Строка перечитана и совпала с отправленным — запись состоялась."""
ROLLED_BACK = "rolled_back"
"""Строку правили одновременно с нами: наши ячейки очищены, чужие целы."""
FAILED = "failed"
"""Запись не легла или раскладку листа не подтвердили — разбор по журналу."""
OPEN_STATUSES = (PENDING, VERIFIED)

_LOCK_NOT_AVAILABLE = "55P03"
"""SQLSTATE истёкшего `lock_timeout`."""


class WritersBusyError(RuntimeError):
    """Очередь писателей занята дольше, чем мы готовы ждать."""


@dataclass(frozen=True, slots=True)
class NewWrite:
    """Что известно о записи до того, как она ушла в лист."""

    book: str
    sheet: str
    row: int
    request_key: str
    actor_id: uuid.UUID | None
    before: Mapping[str, object]
    values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class OpenWrite:
    """Открытая запись журнала — начатая или состоявшаяся."""

    id: int
    row: int
    status: str
    before: dict[str, object]
    values: dict[str, object]


class DbJournal:
    """Журнал записей и очередь писателей на Postgres."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @contextmanager
    def writers_lock(self, key: int, timeout: timedelta) -> Iterator[None]:
        """Встать в очередь писателей и держать её, пока идёт запись.

        Блокировка транзакционная (`xact`): приложение ходит через пулер в
        transaction-режиме, и сессионная повисла бы на чужом соединении.
        Держит её своя транзакция, в которой больше ничего не делается, —
        записи журнала коммитятся рядом, не отпуская очередь. Отпускается
        при закрытии сессии, чем бы ни кончилась запись.

        Ждать дольше ``timeout`` — :class:`WritersBusyError`. Только `SET
        LOCAL`: через пулер сессионный SET остался бы на чужом соединении.
        """
        milliseconds = int(timeout / timedelta(milliseconds=1))
        with self._sessions() as session:
            session.begin()
            session.execute(text(f"set local lock_timeout = '{milliseconds}ms'"))
            try:
                session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": key})
            except OperationalError as error:
                if getattr(error.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE:
                    raise WritersBusyError(
                        f"очередь писателей занята дольше {timeout.total_seconds():g} с"
                    ) from error
                raise
            yield

    def find_open(self, request_key: str) -> OpenWrite | None:
        """Открытая запись по ключу запроса — её держит частичный уникальный
        индекс, поэтому она одна или её нет."""
        query = select(models.SheetWrite).where(
            models.SheetWrite.request_key == request_key,
            models.SheetWrite.status.in_(OPEN_STATUSES),
        )
        with self._sessions() as session:
            write = session.scalar(query)
            if write is None:
                return None
            return OpenWrite(
                id=write.id,
                row=write.row,
                status=write.status,
                before=dict(write.before),
                values=dict(write.values),
            )

    def start(self, write: NewWrite) -> int:
        """Завести запись ``pending`` — своим коммитом, до записи в лист."""
        return self._add(write, PENDING)

    def found(self, write: NewWrite, *, content_hash: str, note: str) -> int:
        """Записать сразу ``verified``: строка уже в листе, писать не пришлось."""
        return self._add(write, VERIFIED, content_hash=content_hash, note=note, finished=True)

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
        """Итог записи. ``before`` заменяет снимок «до», если передан: у
        состоявшейся записи полный снимок листа больше не нужен."""
        with self._sessions() as session, session.begin():
            write = session.get_one(models.SheetWrite, write_id, with_for_update=True)
            write.status = status
            write.content_hash = content_hash
            write.error = error
            write.note = note
            if before is not None:
                write.before = dict(before)
            write.after = dict(after) if after is not None else None
            write.finished_at = func.now()

    def annotate(self, write_id: int, *, error: str) -> None:
        """Пометить незавершённую запись, не завершая её: исход неизвестен, и
        решит его перечитывание при повторе."""
        with self._sessions() as session, session.begin():
            write = session.get_one(models.SheetWrite, write_id, with_for_update=True)
            write.error = error

    def _add(
        self,
        write: NewWrite,
        status: str,
        *,
        content_hash: str | None = None,
        note: str | None = None,
        finished: bool = False,
    ) -> int:
        record = models.SheetWrite(
            book=write.book,
            sheet=write.sheet,
            row=write.row,
            action="append",
            status=status,
            request_key=write.request_key,
            actor_id=write.actor_id,
            before=dict(write.before),
            values=dict(write.values),
            content_hash=content_hash,
            note=note,
        )
        if finished:
            record.finished_at = func.now()
        with self._sessions() as session, session.begin():
            session.add(record)
            session.flush()
            return record.id
