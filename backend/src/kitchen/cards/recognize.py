"""Распознавание этикетки черновика: модель читает фото, код разбирает прочитанное.

Повар сфотографировал этикетку — модель через polza.ai переписывает с неё
текст как напечатан, а числа, сроки и склонения считает код
(:func:`kitchen.domain.cards.label_fields_from_extraction`). Модель здесь
ничего не решает: её ответ — данные, проверенные схемой.

Порядок, и почему он такой:

1. **Черновик, ключ, этикетка.** Нет ключа polza.ai — 503 «не настроено»: ни
   вызова, ни траты бюджета. Нет фото этикетки — 409.
2. **Не идёт ли уже** — 409. «Идёт» старше :data:`STALE_AFTER` не в счёт:
   процесс умер посреди вызова, и без этого предела черновик был бы заперт
   навсегда. Черновик уходит в лист — тоже 409: итог лёг бы в черновик, уже
   отправленный, и пропал бы.
3. **Бюджет и лимит повара** — до вызова: исчерпаны — 429, модель не зовётся.
4. **Фото из Drive** — до отметки «идёт»: отметка держится только на время
   вызова модели, и её предел считается от него.
5. **Отметка «идёт»** — одним запросом и своим коммитом: двойное нажатие не
   зовёт модель дважды, а телефон, уснувший посреди ожидания, найдёт
   «идёт» и дождётся результата опросом черновика.
6. **Вызов модели** — вне транзакции базы (до трёх минут).
7. **Журнал** ``llm_calls`` — всегда, и при отказе: деньги могли быть
   списаны. Своей транзакцией, до записи результата. Модель — из настроек,
   а не из ответа шлюза: тот текст чужой и не очищен.
8. **Результат** ложится, только если черновик ещё активен, этикетка та же
   и это тот же запуск. Повар переснял или убрал этикетку, пока модель
   читала старую, — прочитанное о старой не ложится поверх новой, а «идёт»
   этого запуска не остаётся висеть. Запуск завис дольше предела, и его
   перехватил новый по той же этикетке, — итог старого тоже не ложится, со
   своим текстом: этикетку-то никто не менял.

В черновик пишутся одиннадцать полей этикетки (заменой, а не добавлением) и
замечания самой этикетки; проверку КБЖУ черновик считает при выдаче сам.
Отказ модели в лог идёт только строкой ``describe()``: в исключениях SDK —
запрос с ключом.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from kitchen.cards.drafts import (
    WAIT_FOR_SUBMIT,
    CardsError,
    DraftConflictError,
    DraftNotFoundError,
    fetch_photo,
    forget_recognition,
    require_active,
)
from kitchen.db import drafts as store
from kitchen.domain.cards import (
    LABEL_FIELDS,
    NUTRIENT_FIELDS,
    check_nutrients,
    label_fields_from_extraction,
)
from kitchen.llm.budget import LlmLimitError, ensure_allowed, record_call
from kitchen.llm.polza import TOTAL_DEADLINE_SECONDS, LlmError

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from sqlalchemy.orm import Session

    from kitchen.db.models import CardDraft
    from kitchen.llm.budget import LlmLimits
    from kitchen.llm.label import LabelReader, LabelReading
    from kitchen.sync.drive import DriveClient

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(seconds=TOTAL_DEADLINE_SECONDS + 60)
"""Сколько «идёт» считается живым. Вызов модели с повтором клиент укладывает
в общий срок (170 с); минута сверху — на разбор ответа и записи в базу.
Старше — процесс умер посреди вызова, и повтор разрешён."""

NOT_CONFIGURED = "Распознавание не настроено — заполните поля вручную"
NO_LABEL = "Нет фото этикетки — сначала сфотографируйте её"
RUNNING = "Этикетка уже распознаётся — подождите"
LABEL_REPLACED = "Фото этикетки заменили, пока шло распознавание, — распознайте новое фото"
LABEL_REMOVED = "Фото этикетки убрали, пока шло распознавание, — сфотографируйте её заново"
RESTARTED = "Эту этикетку уже распознают заново — дождитесь результата нового запуска"
INTERRUPTED = "Распознавание прервалось — попробуйте ещё раз или заполните поля вручную"
STALLED = "Распознавание прервалось — нажмите «Распознать ещё раз» или заполните поля вручную"


class RecognitionNotConfiguredError(CardsError):
    def __init__(self) -> None:
        super().__init__(NOT_CONFIGURED)


class RecognitionLimitError(CardsError):
    """Дневной бюджет или лимит повара исчерпан; текст — из ``LlmLimitError``."""


class RecognitionFailedError(CardsError):
    """Модель не дала ответа, которым можно пользоваться: повар заполнит поля сам."""


def recognize(
    session: Session,
    drive: DriveClient,
    reader: LabelReader | None,
    limits: LlmLimits,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    *,
    submit_window: timedelta,
) -> CardDraft:
    """Распознать этикетку черновика и положить прочитанное в его поля.

    ``reader`` — ``None``, если ключа polza.ai нет. Идёт отправка черновика
    (отметка моложе ``submit_window``) — 409: итог лёг бы в черновик, уже
    ушедший в лист. Отказы — :class:`~kitchen.cards.drafts.CardsError` с
    текстом для повара.
    """
    draft = require_active(session, owner_id, draft_id)
    if reader is None:
        raise RecognitionNotConfiguredError
    label = draft.label_file_id
    if not label:
        raise DraftConflictError(NO_LABEL)
    if store.recognition_running(session, draft.id, STALE_AFTER):
        raise DraftConflictError(RUNNING)
    if store.submitting(session, draft.id, submit_window):
        raise DraftConflictError(WAIT_FOR_SUBMIT)
    try:
        ensure_allowed(session, limits, purpose=reader.purpose, profile_id=owner_id)
    except LlmLimitError as error:
        raise RecognitionLimitError(str(error)) from None

    # Запрос к Drive идёт до минуты: транзакцию базы на это время закрываем.
    session.rollback()
    jpeg = fetch_photo(drive, label, draft_id)
    started = store.start_recognition(
        session, owner_id, draft_id, label, stale_after=STALE_AFTER, submit_window=submit_window
    )
    session.commit()
    if started is None:
        raise _not_started(session, owner_id, draft_id, label, submit_window)
    return _run(session, reader, owner_id, draft_id, label, started, jpeg)


def _not_started(
    session: Session,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    label: str,
    submit_window: timedelta,
) -> CardsError:
    """Отметку «идёт» не поставили: почему — повару."""
    draft = store.active_draft(session, owner_id, draft_id)
    current = None if draft is None else draft.label_file_id
    sending = draft is not None and store.submitting(session, draft_id, submit_window)
    session.rollback()
    if draft is None:
        return DraftNotFoundError()
    if current != label:
        return DraftConflictError(NO_LABEL if not current else LABEL_REPLACED)
    if sending:
        return DraftConflictError(WAIT_FOR_SUBMIT)
    return DraftConflictError(RUNNING)


def _run(
    session: Session,
    reader: LabelReader,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    label: str,
    started: datetime,
    jpeg: bytes,
) -> CardDraft:
    """Вызвать модель и положить итог. «Идёт» не остаётся висеть ни при каком
    исходе: необработанная ошибка снимает его в ``finally``."""
    settled = False
    try:
        outcome = _read(session, reader, owner_id, draft_id, jpeg)
        if isinstance(outcome, LlmError):
            message = _by_hand(str(outcome))
            _settle(session, owner_id, draft_id, label, started, failed=message)
            settled = True
            raise RecognitionFailedError(message)
        values, warnings = label_fields_from_extraction(outcome.extraction.model_dump())
        draft = _settle(
            session,
            owner_id,
            draft_id,
            label,
            started,
            values=values,
            notes=label_notes(values, warnings),
            extraction=outcome.extraction.model_dump(),
        )
        settled = True
        return draft
    finally:
        if not settled:
            _abandon(session, owner_id, draft_id, label, started)


def _read(
    session: Session,
    reader: LabelReader,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    jpeg: bytes,
) -> LabelReading | LlmError:
    """Вызов модели и строка журнала о нём — при любом исходе.

    Отказ модели возвращается, а не поднимается: решать, что сказать повару,
    — вызывающему. Ошибка, которой клиент polza.ai не знает (это поломка
    кода), — в журнал как вызов с неизвестной ценой и дальше наверх.
    """
    reading: LabelReading | None = None
    failure: LlmError | None = None
    try:
        reading = reader.read(jpeg)
    except LlmError as error:
        failure = error
    except Exception as error:
        _journal(
            session,
            reader,
            owner_id,
            ok=False,
            error=f"сбой распознавания: {type(error).__name__}",
            cost_rub=None,
            unpriced_attempts=0,
        )
        raise

    if failure is not None:
        log.warning(
            "распознавание этикетки черновика %s не удалось: %s", draft_id, failure.describe()
        )
        _journal(
            session,
            reader,
            owner_id,
            ok=False,
            error=failure.describe(),
            cost_rub=failure.cost_rub,
            unpriced_attempts=failure.unpriced_attempts,
            tokens=failure.tokens,
            duration_ms=failure.duration_ms,
        )
        return failure
    assert reading is not None
    reply = reading.reply
    _journal(
        session,
        reader,
        owner_id,
        ok=True,
        cost_rub=reply.cost_rub,
        unpriced_attempts=reply.unpriced_attempts,
        tokens=reply.tokens,
        duration_ms=reply.duration_ms,
    )
    return reading


def _journal(
    session: Session,
    reader: LabelReader,
    owner_id: uuid.UUID,
    *,
    ok: bool,
    cost_rub: Decimal | None,
    unpriced_attempts: int,
    error: str = "",
    tokens: int | None = None,
    duration_ms: int | None = None,
) -> None:
    """Строка ``llm_calls`` — своей транзакцией: деньги списаны, чем бы ни
    кончилось остальное. Модель — из настроек (``reader.model``)."""
    try:
        record_call(
            session,
            purpose=reader.purpose,
            model=reader.model,
            prompt_version=reader.prompt_version,
            profile_id=owner_id,
            ok=ok,
            unpriced_attempts=unpriced_attempts,
            error=error,
            cost_rub=cost_rub,
            tokens=tokens,
            duration_ms=duration_ms,
        )
        session.commit()
    except SQLAlchemyError as failure:
        session.rollback()
        log.error(
            "вызов модели повара %s не записан в журнал llm_calls — бюджет его не увидит: %s",
            owner_id,
            str(failure).splitlines()[0] if str(failure) else type(failure).__name__,
        )


def label_notes(values: Mapping[str, object], warnings: Sequence[str]) -> list[str]:
    """Замечания самой этикетки — без проверки КБЖУ.

    Домен отдаёт их вместе с проверкой чисел; проверку черновик считает при
    выдаче сам, по своим числам, — храниться ей незачем. Отделяется она той
    же проверкой по тем же только что прочитанным числам.
    """
    protein, fat, carbs, kcal = (_number(values.get(field)) for field in NUTRIENT_FIELDS)
    checks = set(check_nutrients(protein, fat, carbs, kcal))
    return [note for note in warnings if note not in checks]


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, Decimal):
        return value
    raise TypeError(f"КБЖУ из домена — Decimal или пусто, а пришло {value!r}")


def _by_hand(message: str) -> str:
    """Отказ модели — повару, с тем, что делать: поля можно заполнить самому."""
    if "вручную" in message:
        return message
    return f"{message} Пока заполните поля вручную."


def _settle(
    session: Session,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    label: str,
    started: datetime,
    *,
    values: Mapping[str, object] | None = None,
    notes: Sequence[str] = (),
    extraction: Mapping[str, object] | None = None,
    failed: str | None = None,
) -> CardDraft:
    """Итог распознавания — в черновик, если он всё ещё про этот запуск."""
    draft = store.active_draft(session, owner_id, draft_id, lock=True)
    if draft is None:
        session.rollback()
        log.info("черновик %s закрыт, пока шло распознавание, — итог отброшен", draft_id)
        raise DraftNotFoundError
    if not _same_run(draft, label, started):
        current = draft.label_file_id
        if current == label:
            # Этикетка та же, но запуск уже не этот: он завис дольше
            # STALE_AFTER, и новый запуск перехватил «идёт». Итог старого не
            # нужен — у нового свой; «этикетку заменили» было бы неправдой.
            session.rollback()
            log.info(
                "черновик %s: этикетку распознаёт новый запуск, пока шёл этот, — итог отброшен",
                draft_id,
            )
            raise DraftConflictError(RESTARTED)
        if draft.recognition_status == store.RUNNING and draft.recognition_started_at == started:
            # Этикетку сменили, не сбросив «идёт» (так делают замена и
            # удаление фото, но мало ли путей): этот запуск снимает его сам,
            # а не оставляет висеть до предела.
            forget_recognition(draft)
            session.commit()
        else:
            session.rollback()
        log.info(
            "черновик %s: этикетку заменили или убрали, пока шло распознавание, — итог отброшен",
            draft_id,
        )
        raise DraftConflictError(LABEL_REPLACED if current else LABEL_REMOVED)
    if failed is not None:
        draft.recognition_status = store.FAILED
        draft.recognition = {"label_file_id": label, "error": failed}
    else:
        assert values is not None
        for field in LABEL_FIELDS:
            setattr(draft, field, values[field])
        draft.recognition_status = store.DONE
        draft.recognition_warnings = list(notes)
        draft.recognition = {"label_file_id": label, "extraction": dict(extraction or {})}
    session.commit()
    return draft


def _same_run(draft: CardDraft, label: str, started: datetime) -> bool:
    return (
        draft.label_file_id == label
        and draft.recognition_status == store.RUNNING
        and draft.recognition_started_at == started
    )


def _abandon(
    session: Session, owner_id: uuid.UUID, draft_id: uuid.UUID, label: str, started: datetime
) -> None:
    """Необработанная ошибка посреди распознавания: снять «идёт», чтобы
    черновик не ждал предела. Не вышло (база недоступна) — только лог: предел
    :data:`STALE_AFTER` всё равно отпустит черновик."""
    try:
        session.rollback()
        draft = store.active_draft(session, owner_id, draft_id, lock=True)
        if draft is not None and _same_run(draft, label, started):
            draft.recognition_status = store.FAILED
            draft.recognition = {"label_file_id": label, "error": INTERRUPTED}
        session.commit()
    except SQLAlchemyError as error:
        session.rollback()
        log.error(
            "черновик %s: «распознавание идёт» не снято после сбоя — снимется через %s: %s",
            draft_id,
            STALE_AFTER,
            str(error).splitlines()[0] if str(error) else type(error).__name__,
        )


def shown_recognition(session: Session, draft: CardDraft) -> tuple[str | None, str | None]:
    """Состояние распознавания и почему не удалось — какими их видит повар.

    «Идёт» старше :data:`STALE_AFTER` — процесс умер посреди вызова (тот же
    предел разрешает повтор): выдаётся «не удалось» с подсказкой, иначе
    экран опрашивал бы черновик без конца. База не меняется — решение
    принимается при выдаче, а повтор и так разрешён. Часы — базы, как у
    отметки «идёт» и у проверки повтора.
    """
    if draft.recognition_status == store.RUNNING and not store.recognition_running(
        session, draft.id, STALE_AFTER
    ):
        return store.FAILED, STALLED
    return draft.recognition_status, recognition_error(draft)


def recognition_error(draft: CardDraft) -> str | None:
    """Почему не удалось последнее распознавание — текст для повара."""
    if draft.recognition_status != store.FAILED or not draft.recognition:
        return None
    error = draft.recognition.get("error")
    return error if isinstance(error, str) else None
