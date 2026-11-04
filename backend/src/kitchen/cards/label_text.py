"""Черновик по тексту этикетки: человек (или его модель по подписке) прочитал
этикетку сам, код разбирает прочитанное.

Тот же разбор, что после распознавания polza.ai
(:func:`kitchen.domain.cards.label_fields_from_extraction`) и те же правила
записи: одиннадцать полей этикетки заменяются целиком, замечания этикетки
ложатся в черновик. Фото, шаг мастера и прочие поля не трогаются. Модель
здесь не вызывается, журнал ``llm_calls`` не пишется — платит пользователь
своей подпиской.

Черновик повара один: нет активного — создаётся, есть — заменяются поля
этикетки в нём. Идёт отправка или распознавание — отказ с тем же текстом,
что в вебе: правка пропала бы или затёрлась итогом.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kitchen.cards.drafts import WAIT_FOR_SUBMIT, DraftConflictError
from kitchen.cards.recognize import RUNNING, STALE_AFTER, label_notes
from kitchen.db import drafts as store
from kitchen.domain.cards import LABEL_FIELDS, label_fields_from_extraction

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping
    from datetime import timedelta

    from sqlalchemy.orm import Session

    from kitchen.db.models import CardDraft


def fill_from_label_text(
    session: Session,
    owner_id: uuid.UUID,
    extraction: Mapping[str, object],
    *,
    submit_window: timedelta,
) -> tuple[CardDraft, bool]:
    """Положить прочитанную этикетку в черновик повара: ``(черновик, создан ли новый)``."""
    draft = store.active_draft(session, owner_id, lock=True)
    created = draft is None
    if draft is None:
        try:
            draft = store.add_draft(session, owner_id)
        except store.ActiveDraftExistsError as race:
            # Гонка двух вызовов: соседний успел создать — пишем в его черновик.
            draft = store.active_draft(session, owner_id, lock=True)
            created = False
            if draft is None:
                # Соседний черновик успел и создаться, и уйти — повтор вызова разберётся.
                session.rollback()
                raise DraftConflictError(WAIT_FOR_SUBMIT) from race
    else:
        if store.submitting(session, draft.id, submit_window):
            session.rollback()
            raise DraftConflictError(WAIT_FOR_SUBMIT)
        if store.recognition_running(session, draft.id, STALE_AFTER):
            session.rollback()
            raise DraftConflictError(RUNNING)

    values, warnings = label_fields_from_extraction(extraction)
    for field in LABEL_FIELDS:
        setattr(draft, field, values[field])
    draft.recognition_status = store.DONE
    draft.recognition_started_at = None
    draft.recognition_warnings = label_notes(values, warnings)
    draft.recognition = {"label_file_id": draft.label_file_id or "", "extraction": dict(extraction)}
    session.commit()
    return draft, created
