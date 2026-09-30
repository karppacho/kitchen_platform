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

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from kitchen.db import models

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterator, Mapping

    from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

PENDING = "pending"
"""Запись в лист начата: снимок «до» уже в журнале, исход ещё не известен."""
VERIFIED = "verified"
"""Строка перечитана и совпала с отправленным — запись состоялась."""
ROLLED_BACK = "rolled_back"
"""Строку правили одновременно с нами: наши ячейки очищены, чужие целы."""
FAILED = "failed"
"""Запись не легла или раскладку листа не подтвердили — разбор по журналу."""
OPEN_STATUSES = (PENDING, VERIFIED)

LAYOUT_UNCONFIRMED = "раскладка не подтверждена"
"""Пометка (`note`) у ``failed``: лист сдвигали в окне записи, чужая карточка
могла пострадать. По ней повтор той же отправки находит такую попытку и не
даёт ей потеряться за удачным ответом."""

_BUSY = {"55P03", "57014"}
"""SQLSTATE, с которыми обрывается ожидание очереди: `lock_timeout`
(lock_not_available) и `statement_timeout` (query_canceled)."""

_STATEMENT_MARGIN = timedelta(seconds=5)
"""На сколько `statement_timeout` в транзакции очереди длиннее ожидания:
ожидание обрывает `lock_timeout`, а предел запроса — страховка от чужого,
более короткого `statement_timeout` роли."""


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


@dataclass(frozen=True, slots=True)
class UnconfirmedWrite:
    """Попытка, раскладку которой не подтвердили: строка и номер журнала —
    то, что повар покажет шефу."""

    id: int
    row: int


class HeldLock:
    """Очередь писателей, которую держит писатель, — транзакция в базе."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def alive(self) -> bool:
        """Держится ли очередь: транзакция жива, соединение не оборвано.

        Очередь держится, пока открыта транзакция, взявшая блокировку. Её
        может оборвать база (`idle_in_transaction_session_timeout`, перезапуск
        пулера) — и второй писатель тогда уже вправе выбрать ту же строку.
        Писатель спрашивает об этом прямо перед записью в лист.
        """
        try:
            self._session.execute(text("select 1"))
        except SQLAlchemyError as error:
            log.error("очередь писателей потеряна: %s", _first_line(error))
            return False
        return True


class DbJournal:
    """Журнал записей и очередь писателей на Postgres."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @contextmanager
    def writers_lock(self, key: int, wait: timedelta, hold: timedelta) -> Iterator[HeldLock]:
        """Встать в очередь писателей и держать её, пока идёт запись.

        Блокировка транзакционная (`xact`): приложение ходит через пулер
        Supavisor в transaction-режиме, и он держит за нами одно соединение
        от BEGIN до конца транзакции — сессионная блокировка повисла бы на
        чужом соединении. Поэтому очередь — это живая транзакция, в которой
        больше ничего не делается; записи журнала коммитятся рядом.

        Три предела — только `SET LOCAL`, на эту транзакцию (сессионный SET
        через пулер остался бы на чужом соединении):

        * `lock_timeout` = ``wait`` — дольше ждать другого писателя незачем:
          «Таблица занята»;
        * `statement_timeout` — чуть дольше ожидания: у роли он может быть
          короче, и тогда ожидание обрывал бы он (57014 — тоже «занята»);
        * `idle_in_transaction_session_timeout` = ``hold`` — пока писатель
          ходит в Google, транзакция стоит без запросов. Короткий предел роли
          оборвал бы её и молча отпустил очередь; ``hold`` — выше худшей
          записи, и зависший процесс он всё равно ограничивает.

        Отпускается очередь откатом транзакции, чем бы ни кончилась запись.
        Ошибка отката — например, база уже оборвала соединение — уходит в
        лог и не подменяет исход записи: он уже известен и сверен.
        """
        session = self._sessions()
        try:
            session.begin()
            limits = (
                ("idle_in_transaction_session_timeout", hold),
                ("lock_timeout", wait),
                ("statement_timeout", wait + _STATEMENT_MARGIN),
            )
            for name, limit in limits:
                milliseconds = max(1, int(limit / timedelta(milliseconds=1)))
                session.execute(text(f"set local {name} = '{milliseconds}ms'"))
            try:
                session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": key})
            except OperationalError as error:
                if getattr(error.orig, "sqlstate", None) in _BUSY:
                    raise WritersBusyError(
                        f"очередь писателей занята дольше {wait.total_seconds():g} с"
                    ) from error
                raise
            yield HeldLock(session)
        finally:
            try:
                session.rollback()
                session.close()
            except SQLAlchemyError as error:
                log.warning(
                    "очередь писателей: отпустить блокировку не удалось — %s", _first_line(error)
                )

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

    def find_unconfirmed(self, request_key: str) -> UnconfirmedWrite | None:
        """Последняя попытка этой отправки, раскладку которой не подтвердили."""
        query = (
            select(models.SheetWrite.id, models.SheetWrite.row)
            .where(
                models.SheetWrite.request_key == request_key,
                models.SheetWrite.status == FAILED,
                models.SheetWrite.note == LAYOUT_UNCONFIRMED,
            )
            .order_by(models.SheetWrite.id.desc())
            .limit(1)
        )
        with self._sessions() as session:
            found = session.execute(query).first()
        return None if found is None else UnconfirmedWrite(id=found.id, row=found.row)

    def start(self, write: NewWrite) -> int:
        """Завести запись ``pending`` — своим коммитом, до записи в лист."""
        return self._add(write, PENDING)

    def found(
        self,
        write: NewWrite,
        *,
        content_hash: str,
        note: str,
        after: Mapping[str, object] | None = None,
    ) -> int:
        """Записать сразу ``verified``: строка уже в листе, писать не пришлось."""
        return self._add(
            write, VERIFIED, content_hash=content_hash, note=note, after=after, finished=True
        )

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
        """Итог записи — только у ``pending``: поставленный итог поздний вызов
        не перепишет. ``before`` заменяет снимок «до», если передан: у
        состоявшейся записи полный снимок листа больше не нужен."""
        changes: dict[str, object] = {
            "status": status,
            "content_hash": content_hash,
            "error": error,
            "note": note,
            "after": dict(after) if after is not None else None,
            "finished_at": func.now(),
        }
        if before is not None:
            changes["before"] = dict(before)
        self._update_pending(write_id, changes)

    def annotate(self, write_id: int, *, error: str) -> None:
        """Пометить незавершённую запись, не завершая её: исход неизвестен, и
        решит его перечитывание при повторе."""
        self._update_pending(write_id, {"error": error})

    def _update_pending(self, write_id: int, changes: Mapping[str, object]) -> None:
        statement = (
            update(models.SheetWrite)
            .where(models.SheetWrite.id == write_id, models.SheetWrite.status == PENDING)
            .values({getattr(models.SheetWrite, name): value for name, value in changes.items()})
        )
        with self._sessions() as session, session.begin():
            changed = session.execute(statement).rowcount  # type: ignore[attr-defined]
        if changed != 1:
            raise RuntimeError(f"запись журнала №{write_id} уже завершена или её нет")

    def _add(
        self,
        write: NewWrite,
        status: str,
        *,
        content_hash: str | None = None,
        note: str | None = None,
        after: Mapping[str, object] | None = None,
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
            after=dict(after) if after is not None else None,
            content_hash=content_hash,
            note=note,
        )
        if finished:
            record.finished_at = func.now()
        with self._sessions() as session, session.begin():
            session.add(record)
            session.flush()
            return record.id


def _first_line(error: Exception) -> str:
    """Первая строка ошибки базы: дальше SQLAlchemy печатает запрос и ссылку."""
    return str(error).splitlines()[0] if str(error) else type(error).__name__
