"""Отправка черновика в таблицу: одна безопасная запись и перенос в базу.

«Отправить в таблицу» — единственный путь, которым карточка попадает в
«Лист1» книги карточек. Писатель строки (:mod:`kitchen.sync.writer`) делает
одну сверенную запись с журналом; здесь — всё вокруг неё:

1. **Отметка «отправка идёт»** — одним запросом, и из него же — поля и id
   фото, что уйдут в лист; коммит до записи. Пока отметка свежая, вторая
   отправка — 409, фото не меняются и не уходят в корзину: ссылки на них уже
   летят в лист. Отметка старше :func:`submit_window` — процесс умер посреди
   записи, отправка снова разрешена.
2. **Чего не хватает** — 422 со списком полей по-русски, до Google.
3. **Запись** — ключ запроса один на черновик (``card-draft:<id>``): повтор
   после обрыва сети идёт через журнал и второй строки не даёт.
4. **Черновик отправлен** — только после удачной записи: номер строки,
   время, запись журнала. Любой отказ — черновик активен, отметка снята.
5. **Перенос книги карточек в базу** — обычным путём импорта. Не удался
   (импортный замок занят, Google не ответил) — запись от этого не
   становится ошибкой: ответ удачный, ``imported=false``, карточку перенесёт
   следующий цикл.

Повтор отправки уже отправленного черновика (ответ потерялся в сети) —
тот же путь через журнал: строка та же, записи нет.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.exc import SQLAlchemyError

from kitchen.cards.drafts import (
    CardsError,
    DraftConflictError,
    DraftNotFoundError,
    draft_data,
    submit_request_key,
)
from kitchen.db import drafts as store
from kitchen.domain.cards import CardDraftData, card_row, clean_text, missing_for_submit
from kitchen.sync import specs
from kitchen.sync.ownership import ForbiddenWriteError
from kitchen.sync.reader import describe_error
from kitchen.sync.writer import (
    LOCK_TIMEOUT,
    DuplicateNameError,
    HeaderDriftError,
    SheetBusyError,
    WriteRefusedError,
    hold_limit,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping, Sequence
    from decimal import Decimal

    from sqlalchemy.orm import Session

    from kitchen.config import Settings
    from kitchen.db.journal import UnconfirmedWrite
    from kitchen.sync.cycle import CycleResult
    from kitchen.sync.writer import AppendResult

log = logging.getLogger(__name__)

BOOK = "ingredient_cards"
"""Книга, которую переносят в базу сразу после записи."""

_IMPORTED = ("imported", "stale")
"""Исходы переноса, после которых карточка в базе. «stale» — пока мы
читали лист, другой перенос прочитал его позже нас (а значит, после записи)
и успел раньше: карточка пришла с ним."""

_SUBMIT_MARGIN = timedelta(minutes=1)

ALREADY_SUBMITTING = "Карточка уже отправляется — подождите"
BUSY = "Таблица занята — попробуйте ещё раз через минуту"
DATABASE_DOWN = "Сервер временно не может записать — черновик сохранён"
NOT_CONFIGURED = "Таблица карточек не настроена — сообщите администратору. Черновик сохранён."
WRITING_CLOSED = "Запись в таблицу сейчас закрыта — сообщите администратору. Черновик сохранён."
SAVED = "Черновик сохранён."
NOT_IMPORTED = (
    "Карточка записана в таблицу, но на сайт пока не перенесена — появится при "
    "следующем обновлении, через несколько минут."
)


def submit_window(settings: Settings) -> timedelta:
    """Сколько отметка «отправка идёт» считается живой.

    Живая отправка ждёт очередь писателей (до :data:`LOCK_TIMEOUT`) и держит
    её не дольше :func:`~kitchen.sync.writer.hold_limit`; минута сверху — на
    базу до и после. Старше — процесс умер посреди записи.
    """
    return hold_limit(settings) + LOCK_TIMEOUT + _SUBMIT_MARGIN


# ---------------------------------------------------------------------------
# Отказы — текст для повара
# ---------------------------------------------------------------------------
class MissingFieldsError(CardsError):
    """Не хватает обязательного: ``missing`` — названия полей для повара."""

    def __init__(self, missing: Sequence[str]) -> None:
        super().__init__(f"Чтобы отправить карточку, заполните: {', '.join(missing)}")
        self.missing = tuple(missing)

    def extra(self) -> dict[str, object]:
        return {"missing": list(self.missing)}


class DuplicateCardError(CardsError):
    """Такая карточка уже есть в листе; ``row`` — её строка."""

    def __init__(self, message: str, *, row: int) -> None:
        super().__init__(message)
        self.row = row

    def extra(self) -> dict[str, object]:
        return {"row": self.row}


class SubmitUnavailableError(CardsError):
    """Записать сейчас нельзя, и дело не в карточке: колонки съехали, таблица
    занята, база не ответила, запись не настроена. Черновик цел."""


class SubmitFailedError(CardsError):
    """Google не принял запись или её не удалось подтвердить. Черновик цел."""


# ---------------------------------------------------------------------------
# На что опирается отправка
# ---------------------------------------------------------------------------
class RowWriter(Protocol):
    """Писатель строки карточки — :class:`kitchen.sync.writer.CardSheetWriter`."""

    def append(
        self, values: Mapping[str, str | Decimal], *, actor_id: uuid.UUID | None, request_key: str
    ) -> AppendResult: ...


class BookImport(Protocol):
    """Перенос книг в базу — :class:`kitchen.sync.cycle.SyncCycle`."""

    def run(self, *, force: bool = False, books: Sequence[str] | None = None) -> CycleResult: ...


@dataclass(frozen=True, slots=True)
class Submitted:
    """Карточка в листе — для экрана «Записано в строку N»."""

    row: int
    already_written: bool
    """Карточка легла раньше — повтором отправки или прерванной попыткой."""
    imported: bool
    """Карточка уже в базе (на сайте). ``False`` — перенесёт следующий цикл."""
    name: str
    not_written: tuple[str, ...]
    """Правки повара, не попавшие в лист, — названиями колонок листа."""
    shifted: UnconfirmedWrite | None
    """Прежняя попытка, раскладку которой не подтвердили: строку и запись
    журнала повар показывает шефу."""
    notes: tuple[str, ...]
    """Оговорки повару — каждая готовой фразой."""


# ---------------------------------------------------------------------------
# Отправка
# ---------------------------------------------------------------------------
def submit(
    session: Session,
    writer: RowWriter | None,
    books: BookImport,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    *,
    window: timedelta,
) -> Submitted:
    """Отправить черновик в лист и перенести карточку в базу.

    ``writer`` — ``None``, если книга карточек не настроена. Отказы —
    :class:`~kitchen.cards.drafts.CardsError` с текстом для повара; при
    любом отказе черновик остаётся активным.
    """
    claimed = store.claim_submit(session, owner_id, draft_id, window)
    if claimed is None:
        return _not_claimed(session, writer, books, owner_id, draft_id)
    data = draft_data(claimed)
    session.commit()

    done = False
    try:
        result = _append(writer, data, owner_id, draft_id)
        _mark_submitted(session, draft_id, result)
        done = True
    finally:
        if not done:
            _release(session, draft_id)
    return _answer(result, data, imported=_import(books, draft_id))


def _not_claimed(
    session: Session,
    writer: RowWriter | None,
    books: BookImport,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
) -> Submitted:
    """Отметку не поставили: отправка уже идёт, черновика нет — или он уже
    отправлен, и это повтор, ответ на который потерялся в сети."""
    draft = store.own_draft(session, owner_id, draft_id)
    status = None if draft is None else draft.status
    data = None if draft is None else draft_data(draft)
    session.rollback()
    if status == store.ACTIVE:
        raise DraftConflictError(ALREADY_SUBMITTING)
    if status != store.SUBMITTED or data is None:
        raise DraftNotFoundError
    # Повтор: журнал по ключу черновика отвечает той же строкой, без записи.
    result = _append(writer, data, owner_id, draft_id)
    return _answer(result, data, imported=_import(books, draft_id))


def _append(
    writer: RowWriter | None, data: CardDraftData, owner_id: uuid.UUID, draft_id: uuid.UUID
) -> AppendResult:
    """Одна запись писателем — отказы словами для повара.

    Ключ запроса — один на черновик: повтор после обрыва сети писатель
    узнаёт по журналу (``pending`` — перечитать строку, ``verified`` —
    ответить без записи).
    """
    missing = missing_for_submit(data)
    if missing:
        raise MissingFieldsError(missing)
    if writer is None:
        raise SubmitUnavailableError(NOT_CONFIGURED)
    values = card_row(data)
    try:
        return writer.append(values, actor_id=owner_id, request_key=submit_request_key(draft_id))
    except DuplicateNameError as error:
        refusal: CardsError = DuplicateCardError(str(error), row=error.row)
    except HeaderDriftError as error:
        refusal = SubmitUnavailableError(str(error))
    except SheetBusyError:
        refusal = SubmitUnavailableError(BUSY)
    except WriteRefusedError as error:
        refusal = SubmitFailedError(_saved(str(error)))
    except ForbiddenWriteError as error:
        log.error("черновик %s: запись в лист закрыта правилом владения — %s", draft_id, error)
        refusal = SubmitUnavailableError(WRITING_CLOSED)
    except SQLAlchemyError as error:
        log.error(
            "черновик %s: база не ответила, когда писатель вставал в очередь или вёл журнал — %s",
            draft_id,
            _first_line(error),
        )
        refusal = SubmitUnavailableError(DATABASE_DOWN)
    raise refusal


def _saved(message: str) -> str:
    """Текст отказа записи — с тем, что черновик цел."""
    return message if "Черновик сохранён" in message else f"{message} {SAVED}"


def _mark_submitted(session: Session, draft_id: uuid.UUID, result: AppendResult) -> None:
    """Строка в листе — черновик отправлен. Только после удачной записи."""
    try:
        marked = store.mark_submitted(
            session, draft_id, row=result.row, sheet_write_id=result.journal_id
        )
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        log.error(
            "черновик %s: карточка в строке %s (журнал №%s), а черновик не отмечен "
            "отправленным — база не ответила: %s",
            draft_id,
            result.row,
            result.journal_id,
            _first_line(error),
        )
        raise SubmitUnavailableError(
            f"Карточка записана в строку {result.row}, но сервер не успел это запомнить — "
            "нажмите «Отправить» ещё раз: второй строки не будет."
        ) from None
    if not marked:
        log.error(
            "черновик %s закрыли, пока шла запись, — карточка всё равно в строке %s (журнал №%s)",
            draft_id,
            result.row,
            result.journal_id,
        )


def _release(session: Session, draft_id: uuid.UUID) -> None:
    """Запись не состоялась — снять отметку «отправка идёт»: черновик снова
    можно править и отправлять. Не вышло — отметка отпустит его по пределу."""
    try:
        session.rollback()
        store.end_submit(session, draft_id)
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        log.error(
            "черновик %s: отметку «отправка идёт» снять не удалось — снимется по пределу: %s",
            draft_id,
            _first_line(error),
        )


def _import(books: BookImport, draft_id: uuid.UUID) -> bool:
    """Перенести книгу карточек в базу. Не вышло — лог, и только: строка уже
    в листе, а следующий цикл синхронизации перенесёт её сам."""
    try:
        result = books.run(force=True, books=(BOOK,))
    except Exception as error:
        log.warning(
            "черновик %s: карточка в листе, но перенос книги карточек в базу не удался — "
            "её перенесёт следующий цикл: %s",
            draft_id,
            describe_error(error),
        )
        return False
    outcome = result.outcomes.get(BOOK)
    if outcome is None or outcome.action not in _IMPORTED:
        log.warning(
            "черновик %s: карточка в листе, но перенос книги карточек в базу не удался — %s",
            draft_id,
            "нет исхода" if outcome is None else (outcome.problem or outcome.action),
        )
        return False
    return True


def _answer(result: AppendResult, data: CardDraftData, *, imported: bool) -> Submitted:
    """Ответ повару: строка и оговорки — каждая готовой фразой."""
    titles = tuple(specs.INGREDIENT_CARDS.column(field).title for field in result.not_written)
    notes: list[str] = []
    if titles:
        edits = ", ".join(f"«{title}»" for title in titles)
        notes.append(
            f"Карточка уже в строке {result.row} в первой версии; правки {edits} не попали — "
            "скажите шефу"
        )
    if result.shifted is not None:
        notes.append(
            f"При первой попытке таблицу меняли — покажите шефу строку {result.shifted.row} "
            f"(запись журнала №{result.shifted.id})"
        )
    if not imported:
        notes.append(NOT_IMPORTED)
    return Submitted(
        row=result.row,
        already_written=result.already_written,
        imported=imported,
        name=clean_text("name", data.name),
        not_written=titles,
        shifted=result.shifted,
        notes=tuple(notes),
    )


def _first_line(error: Exception) -> str:
    """Первая строка ошибки базы: дальше SQLAlchemy печатает запрос с данными."""
    return str(error).splitlines()[0] if str(error) else type(error).__name__
