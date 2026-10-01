"""Отправка черновика в таблицу: одна безопасная запись и перенос в базу.

«Отправить в таблицу» — единственный путь, которым карточка попадает в
«Лист1» книги карточек. Писатель строки (:mod:`kitchen.sync.writer`) делает
одну сверенную запись с журналом; здесь — всё вокруг неё:

1. **Отметка «отправка идёт»** — одним запросом, и из него же — поля и id
   фото, что уйдут в лист; коммит до записи. Пока отметка свежая, вторая
   отправка — 409, правка, фото и распознавание ждут: строка уже собрана, а
   ссылки на фото летят в лист. Идёт распознавание — отправка ждёт его.
   Отметка старше :func:`submit_window` — процесс умер посреди записи,
   отправка снова разрешена.
2. **Чего не хватает** — 422 со списком полей по-русски, до Google.
3. **Запись** — ключ запроса один на черновик (``card-draft:<id>``): повтор
   после обрыва сети идёт через журнал и второй строки не даёт.
4. **Черновик отправлен** — только после удачной записи: номер строки,
   время, запись журнала. Любой отказ — черновик активен, отметка снята.
5. **Перенос книги карточек в базу** — обычным путём импорта, но с коротким
   ожиданием (:data:`IMPORT_LOCK_WAIT`, :data:`IMPORT_GOOGLE_TIMEOUT`): повар
   ждёт ответа на телефоне, а запись уже состоялась. Не удался (импортный
   замок занят, Google не ответил) — ответ всё равно удачный,
   ``imported=false``; воркер перенесёт карточку следующим циклом.

Повтор отправки уже отправленного черновика (ответ потерялся в сети) идёт
через журнал и к Google не ходит вовсе: строка — из журнала, ``imported`` —
по базе. Прерванная отправка, чья запись в журнале состоялась, доводится до
конца тем же коротким путём.
"""

from __future__ import annotations

import logging
import math
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
from kitchen.cards.recognize import STALE_AFTER
from kitchen.db import drafts as store
from kitchen.db.journal import PENDING, VERIFIED
from kitchen.domain.cards import (
    CardDraftData,
    card_row,
    clean_text,
    drive_view_url,
    missing_for_submit,
)
from kitchen.domain.shelf_life import plural_ru
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

IMPORT_LOCK_WAIT = timedelta(seconds=10)
"""Сколько перенос после отправки ждёт импортный замок.

Повар ждёт ответа на телефоне (фронтенд — 60 с вместе с записью), а строка
уже в листе. Замок дольше занят — значит, идёт перенос воркера или застрял
ручной импорт: ответ — ``imported=false``, карточку перенесёт воркер (он
держит замок только на запись в базу, а Google читает до него)."""

IMPORT_GOOGLE_TIMEOUT = (5, 20)
"""Таймауты чтения книги для переноса после отправки — (соединение, ответ).
Короче обычных: не ответил Google быстро — перенесёт воркер."""

_IMPORTED = ("imported", "stale")
"""Исходы переноса, после которых карточка в базе. «stale» — пока мы
читали лист, другой перенос прочитал его позже нас (а значит, после записи)
и успел раньше: карточка пришла с ним."""

_SUBMIT_MARGIN = timedelta(minutes=1)

WAIT_FOR_RECOGNITION = "Дождитесь окончания распознавания"
BUSY = "Таблица занята — попробуйте ещё раз через минуту"
DATABASE_DOWN = "Сервер временно не может записать — черновик сохранён"
UNCONFIRMED = "Не удалось подтвердить запись — нажмите «Отправить» ещё раз: второй строки не будет"
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


def submitting_text(left: timedelta | None) -> str:
    """409 на свежей отметке: отправка идёт — или прервалась, и отметка
    отпустит черновик через ``left``."""
    minutes = max(1, math.ceil((left or timedelta(0)) / timedelta(minutes=1)))
    unit = plural_ru(minutes, "минуту", "минуты", "минут")
    return f"Карточка отправляется или отправка прервалась — попробуйте через {minutes} {unit}"


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
    """Перенос книг в базу — :class:`kitchen.sync.cycle.SyncCycle`, собранный
    с :data:`IMPORT_LOCK_WAIT` и :data:`IMPORT_GOOGLE_TIMEOUT`."""

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
    claimed = store.claim_submit(
        session, owner_id, draft_id, window=window, recognition_stale=STALE_AFTER
    )
    if claimed is None:
        return _not_claimed(session, writer, owner_id, draft_id, window)
    data = draft_data(claimed)
    session.commit()

    done = False
    try:
        result = _append(session, writer, data, owner_id, draft_id)
        _mark_submitted(session, draft_id, result)
        done = True
    finally:
        if not done:
            _release(session, draft_id)
    return _answer(result, data, imported=_import(books, draft_id))


def _not_claimed(
    session: Session,
    writer: RowWriter | None,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    window: timedelta,
) -> Submitted:
    """Отметку не поставили — почему, и что ответить.

    * Черновик уже отправлен — это повтор, ответ на который потерялся в
      сети: строка из журнала, ``imported`` — по базе, к Google — ни шага.
    * Идёт распознавание — 409, пусть закончится.
    * Отметка свежая, а запись в журнале по ключу уже состоялась — прежний
      запрос записал и не успел отметить черновик (упала база, перезапуск
      контейнера): довести коротким путём, без записи.
    * Иначе отправка идёт или прервалась — 409 с тем, сколько ждать.
    """
    draft = store.own_draft(session, owner_id, draft_id)
    status = None if draft is None else draft.status
    data = None if draft is None else draft_data(draft)
    if status == store.ACTIVE and store.recognition_running(session, draft_id, STALE_AFTER):
        session.rollback()
        raise DraftConflictError(WAIT_FOR_RECOGNITION)
    written = store.open_sheet_write(session, submit_request_key(draft_id))
    left = store.submit_left(session, draft_id, window)
    session.rollback()
    if data is None or status not in (store.ACTIVE, store.SUBMITTED):
        raise DraftNotFoundError
    if status == store.ACTIVE and written != VERIFIED:
        raise DraftConflictError(submitting_text(left))

    # Записано раньше: писатель ответит из журнала, без единого запроса к Google.
    result = _append(session, writer, data, owner_id, draft_id)
    if status == store.ACTIVE:
        # Не WARNING: так же выглядит и штатное двойное нажатие — первый
        # запрос записал и вот-вот отметит черновик сам. Отметит первым любой
        # из двух, второй найдёт черновик отправленным той же строкой.
        log.info(
            "черновик %s: строка %s уже записана (журнал №%s), черновик ещё не отмечен "
            "отправленным — отмечаю (второе нажатие во время отправки или прерванная отправка)",
            draft_id,
            result.row,
            result.journal_id,
        )
        _mark_submitted(session, draft_id, result)
    return _answer(result, data, imported=_in_database(session, result, data))


def _append(
    session: Session,
    writer: RowWriter | None,
    data: CardDraftData,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
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
        refusal = _database_trouble(session, draft_id, error)
    raise refusal


def _database_trouble(session: Session, draft_id: uuid.UUID, error: SQLAlchemyError) -> CardsError:
    """База не ответила, пока писатель работал. Что сказать повару, решает
    журнал: запись по ключу уже заведена — она могла лечь, и повтор её
    перечитает; нет — отказ на входе в очередь, в лист ничего не ушло.

    Журнал и сам не читается (база лежит) — легла ли строка, неизвестно:
    «нажмите ещё раз», как при заведённой записи. Повтор ничего не теряет —
    он сначала перечитает журнал и строку, второй не напишет; а «сервер не
    может записать» могло бы оказаться неправдой про уже легшую карточку."""
    try:
        written = store.open_sheet_write(session, submit_request_key(draft_id))
        session.rollback()
    except SQLAlchemyError as unreadable:
        session.rollback()
        log.error(
            "черновик %s: база не ответила посреди отправки, и журнал записей не прочитан — "
            "исход решит повтор: %s; журнал: %s",
            draft_id,
            _first_line(error),
            _first_line(unreadable),
        )
        return SubmitFailedError(UNCONFIRMED)
    if written in (PENDING, VERIFIED):
        log.error(
            "черновик %s: база не ответила посреди записи (журнал — %s), исход решит повтор — %s",
            draft_id,
            written,
            _first_line(error),
        )
        return SubmitFailedError(UNCONFIRMED)
    log.error(
        "черновик %s: база не ответила на входе в очередь писателей — %s",
        draft_id,
        _first_line(error),
    )
    return SubmitUnavailableError(DATABASE_DOWN)


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
    if not marked and not _marked_by_twin(session, draft_id, result):
        log.error(
            "черновик %s закрыли, пока шла запись, — карточка всё равно в строке %s (журнал №%s)",
            draft_id,
            result.row,
            result.journal_id,
        )


def _marked_by_twin(session: Session, draft_id: uuid.UUID, result: AppendResult) -> bool:
    """Черновик уже отмечен отправленным этой же строкой и записью журнала —
    его отметил второй запрос того же двойного нажатия. Это не ошибка.

    Не прочитали (база не ответила) — считаем, что не отмечен: лучше лишний
    ERROR, чем пропущенный."""
    try:
        marked = store.submitted_as(session, draft_id)
        session.rollback()
    except SQLAlchemyError:
        session.rollback()
        return False
    return marked == (result.row, result.journal_id)


def _release(session: Session, draft_id: uuid.UUID) -> None:
    """Запись не состоялась — снять отметку «отправка идёт»: черновик снова
    можно править и отправлять. Не вышло — отметка отпустит его по пределу,
    а состоявшуюся запись повтор найдёт в журнале."""
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


def _in_database(session: Session, result: AppendResult, data: CardDraftData) -> bool:
    """Есть ли карточка в базе — без переноса и без Google: повтор отвечает
    по тому, что уже перенесено."""
    try:
        found = store.card_in_database(
            session,
            label_url=drive_view_url(data.label_file_id),
            row=result.row,
            name=clean_text("name", data.name),
        )
    finally:
        session.rollback()
    return found


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
