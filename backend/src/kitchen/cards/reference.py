"""«Сверка»: подтверждение пары и перенос карточки в справочник — слой приложения.

Между ручками «Сверки» (``kitchen.web``) и писателем строки справочника
(:mod:`kitchen.sync.reference_writer`). Три действия:

* **Предпросмотр** (:func:`preview`) — строка ING карточки свежим чтением, без
  записи: найдена ли (или почему нет), следующий id, какие ручные ячейки —
  формулы, что уже подтянула ``QUERY`` и что сейчас в ручных ячейках.
* **«Это он»** (:func:`confirm_pair`) — пара «карточка — ингредиент»
  подтверждается только в базе и только с кандидатом карточки
  (:mod:`kitchen.db.links`). Таблица не меняется.
* **«Добавить в справочник»** (:func:`to_reference`) — одна запись писателем,
  перенос книги кухни в базу коротким путём (как после отправки карточки,
  :func:`kitchen.cards.submit.run_import`) и подтверждение пары с
  ингредиентом этой строки. Перенос не удался — ответ всё равно удачный,
  ``imported=false``: строка уже в листе, ингредиент перенесёт воркер.

Правила, ради которых этот слой есть:

* **Пару подтверждаем, только если id строки в базе — того же ингредиента**:
  ингредиент с этим id есть в базе и называется как карточка. Если id стоял в
  строке до нас, это сверяется ещё и до переноса. При сдвиге строк ING (шеф
  согласовал карточку в середине книги карточек — вывод ``QUERY`` съехал, а
  ручные колонки остались) рядом с нашим названием стоит чужой id. Перенос
  переименовал бы тот ингредиент в нашу карточку, сверка после него прошла
  бы, и подтверждённая пара пережила бы даже исправление листа. Поэтому id
  другого ингредиента — отказ «сдвинуты», и перенос не начинается. Защита
  действует, только пока база помнит прежнее название: следующий цикл
  воркера сам перенесёт сдвинутый лист и переименует ингредиент по id —
  после этого сдвиг отсюда не виден. Сигнал «у id сменилось название» — в
  ROADMAP.
* **Решение человека не затираем.** Пару, которую человек уже подтвердил
  («Это он» или перенос у другого человека — список на экране устарел),
  перенос не трогает: строку, ждущую переноса, писатель не заполняет (409
  «обновите страницу»), а подтверждённую с другим ингредиентом пару после
  записи не переписываем — она остаётся, в ответе оговорка. «Это он» у такой
  карточки — тот же 409.
* **Подтверждение — в очереди импорта.** Импорт пересчитывает пары
  неподтверждённых карточек: начатый до подтверждения и записавший после, он
  вернул бы карточке «спорную» пару поверх подтверждённой — и навсегда, раз
  подтверждённую он больше не трогает.
* **Пока идёт запрос к Google, транзакция базы закрыта**: соединение не висит
  «в транзакции», пока писатель читает и пишет лист.
* **Отказы — словами спеки** («Ответы человеку»); код ответа — по классу
  отказа (``kitchen.web.cards.ERROR_STATUS``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from sqlalchemy.exc import SQLAlchemyError

from kitchen.cards.drafts import CardsError
from kitchen.cards.submit import IMPORT_LOCK_WAIT, run_import
from kitchen.db import links
from kitchen.domain.matching import normalise_name
from kitchen.domain.reference_row import NotFound, ReferenceForm, ReferenceFormError
from kitchen.sync.importer import wait_for_import_lock
from kitchen.sync.ownership import ForbiddenWriteError
from kitchen.sync.reference_writer import (
    NOT_CONFIRMED,
    PAIR_CONFIRMED,
    REASON_CONFIRMED,
    RowRefusedError,
    SheetLayoutError,
    fill_request_key,
)
from kitchen.sync.sheet_write import SheetBusyError, WriteNotConfirmedError, WriteRefusedError

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping
    from datetime import timedelta

    from sqlalchemy.orm import Session

    from kitchen.cards.submit import BookImport
    from kitchen.db.journal import UnconfirmedWrite
    from kitchen.sync.reference_writer import FillResult, RowPreview

log = logging.getLogger(__name__)

KITCHEN = "kitchen"
"""Книга, которую переносят в базу сразу после записи строки ING."""

PAIR_WAIT = IMPORT_LOCK_WAIT
"""Сколько подтверждение пары ждёт очередь импорта. Человек ждёт ответа на
экране; дольше — идёт перенос воркера или завис ручной импорт."""

_WHOLE_NUMBER = re.compile(r"[0-9]+")

# ---------------------------------------------------------------------------
# Тексты для человека
# ---------------------------------------------------------------------------
CARD_NOT_FOUND = "Карточка не найдена — обновите страницу"
NOT_A_CANDIDATE = "Этого ингредиента нет среди кандидатов карточки — обновите страницу"
NOT_A_CANDIDATE_REASON = "not_candidate"
REFERENCE_BUSY = "Справочник сейчас обновляется — попробуйте ещё раз через минуту"
NOT_CONFIGURED = "Таблица кухни или карточек не настроена — сообщите администратору"
WRITING_CLOSED = "Запись в таблицу кухни сейчас закрыта — сообщите администратору"
NOT_IMPORTED = (
    "Ингредиент записан в справочник, но на сайт пока не перенесён — появится при "
    "следующем обновлении, через несколько минут"
)
PAIR_NOT_CONFIRMED = (
    "Пару карточки и ингредиента подтвердить не удалось — если карточка осталась на «Сверке», "
    "откройте её ещё раз: второй записи не будет"
)
"""Пару подтвердить не дали очередь импорта или база. Кнопка на «Сверке» у
карточки своя — «Добавить в справочник», «Это новый» или «Связать с
карточкой», — поэтому совет — открыть карточку, а не нажать кнопку по имени.
Карточки там может и не быть: импорт связал её сам по точному названию."""


def _other_name_text(row: int, ref_id: str) -> str:
    return (
        f"Пара карточки и ингредиента не подтверждена: на сайте у id {ref_id} другое название "
        f"или его нет — проверьте строку {row} листа ING"
    )


def _kept_text(row: int) -> str:
    return (
        "Пара карточки уже подтверждена с другим ингредиентом — её не меняли. Проверьте "
        f"строку {row} листа ING: ингредиент может оказаться в справочнике дважды"
    )


def _shifted_note(shifted: UnconfirmedWrite) -> str:
    return (
        f"При прежней попытке лист ING меняли — покажите шефу строку {shifted.row} листа ING "
        f"(запись журнала №{shifted.id})"
    )


# ---------------------------------------------------------------------------
# Отказы — текст для человека
# ---------------------------------------------------------------------------
class ReferenceCardNotFoundError(CardsError):
    """Карточки нет или её убрали из книги карточек (404)."""

    def __init__(self) -> None:
        super().__init__(CARD_NOT_FOUND)


class ReferenceConflictError(CardsError):
    """Лист или карточка сейчас не такие, чтобы это сделать (409). ``reason``
    — почему (``reason`` отказа писателя или :data:`NOT_A_CANDIDATE_REASON`),
    ``row`` — строка ING, если её нашли."""

    def __init__(self, message: str, *, reason: str, row: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.row = row

    def extra(self) -> dict[str, object]:
        return {"reason": self.reason, "row": self.row}


class ReferenceFormInvalidError(CardsError):
    """Форма переноса с ошибками (422): ``errors`` — поле → текст, все сразу."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        self.errors = dict(errors)
        super().__init__("; ".join(self.errors.values()))

    def extra(self) -> dict[str, object]:
        return {"errors": dict(self.errors)}


class ReferenceUnavailableError(CardsError):
    """Сделать нельзя, и дело не в карточке (503): колонки или формула листа
    изменились, таблица занята, запись не настроена или закрыта, справочник
    обновляется. В листе ничего не изменилось."""


class ReferenceFailedError(CardsError):
    """Google не ответил или запись не подтверждена (502) — «нажмите ещё
    раз»: повтор идёт через журнал. У неподтверждённой записи ``row`` и
    ``journal_id`` — что показать шефу."""

    def __init__(
        self, message: str, *, row: int | None = None, journal_id: int | None = None
    ) -> None:
        super().__init__(message)
        self.row = row
        self.journal_id = journal_id

    def extra(self) -> dict[str, object]:
        if self.journal_id is None:
            return {}
        return {"row": self.row, "journal_id": self.journal_id}


# ---------------------------------------------------------------------------
# На что опирается слой
# ---------------------------------------------------------------------------
class RowFiller(Protocol):
    """Писатель строки справочника —
    :class:`kitchen.sync.reference_writer.ReferenceRowFiller`."""

    def fill(
        self,
        card_name: str,
        form: ReferenceForm | ReferenceFormError,
        *,
        actor_id: uuid.UUID | None,
        request_key: str,
        confirmed: bool,
    ) -> FillResult: ...

    def preview(self, card_name: str, *, confirmed: bool) -> RowPreview: ...


@dataclass(frozen=True, slots=True)
class PairConfirmed:
    """Пара «карточка — ингредиент» подтверждена."""

    card_id: int
    ingredient_id: int
    legacy_id: str
    """id ингредиента в листе ING."""
    name: str
    """Название ингредиента."""
    already: bool
    """Пара была подтверждена этим же ингредиентом раньше — двойное нажатие."""

    @property
    def message(self) -> str:
        return f"Пара подтверждена: ингредиент «{self.name}», id {self.legacy_id}"


@dataclass(frozen=True, slots=True)
class Transferred:
    """Карточка в справочнике — для экрана «Записано в справочник»."""

    row: int
    """Строка листа ING."""
    ref_id: str
    """id ингредиента в листе ING."""
    already: bool
    """Ингредиент был в справочнике и до этого нажатия: id в строке уже стоял
    или это повтор состоявшегося переноса."""
    imported: bool
    """Наш ингредиент виден на сайте под этим id: ингредиент с этим id есть в
    базе, не удалён из листа и называется как карточка. Так при подтверждённой
    паре — и когда пару подтвердить не дали, но наш ингредиент на сайте.
    ``False`` — перенос не удался или под этим id на сайте не наш ингредиент;
    тогда в ``notes`` всегда оговорка: :data:`NOT_IMPORTED` или, если после
    удачного переноса под этим id на сайте другой ингредиент, — проверить
    строку листа (без обещания «появится»)."""
    linked: bool
    """Пара «карточка — ингредиент» подтверждена."""
    ingredient_id: int | None
    """Ингредиент пары в базе; ``None`` — пара не подтверждена."""
    message: str
    """«Записано в справочник: строка N, id X» или «Ингредиент уже в
    справочнике — id X»."""
    shifted: UnconfirmedWrite | None
    """Прежняя попытка этого переноса, раскладку которой не подтвердили:
    строку и запись журнала человек показывает шефу."""
    notes: tuple[str, ...]
    """Оговорки — каждая готовой фразой."""


# ---------------------------------------------------------------------------
# Предпросмотр
# ---------------------------------------------------------------------------
def preview(session: Session, filler: RowFiller | None, card_id: int) -> RowPreview:
    """Строка ING карточки для формы переноса — свежее чтение, без записи.

    ``filler`` — ``None``, если книга кухни или карточек не настроена. Строки
    нет или заполнить её нельзя — это не отказ: причина в ответе
    (``reason``, ``message``). Отказы — сломанный лист (503) и Google (502).
    """
    card = _card(session, card_id)
    if filler is None:
        raise ReferenceUnavailableError(NOT_CONFIGURED)
    try:
        return filler.preview(card.name, confirmed=card.confirmed)
    except WriteRefusedError as error:
        raise _refusal(error) from error


# ---------------------------------------------------------------------------
# «Это он»
# ---------------------------------------------------------------------------
def confirm_pair(
    session: Session, card_id: int, ingredient_id: int, *, wait: timedelta = PAIR_WAIT
) -> PairConfirmed:
    """Подтвердить пару карточки с ингредиентом — только в базе и только с
    кандидатом карточки (:func:`kitchen.db.links.card_candidates`).

    Идёт в очереди импорта, ожидая её не дольше ``wait``. Пара уже
    подтверждена этим ингредиентом — ответ «уже», без изменений; другим —
    отказ «обновите страницу» (:data:`REASON_CONFIRMED`): решение уже принял
    другой человек «Это он» или переносом, а список здесь устарел.
    """
    try:
        if not wait_for_import_lock(session, wait):
            raise ReferenceUnavailableError(REFERENCE_BUSY)
        card = links.live_card(session, card_id)
        if card is None:
            raise ReferenceCardNotFoundError
        known = card.ingredient
        if card.link_confirmed_at is not None:
            if known is not None and known.id == ingredient_id:
                return PairConfirmed(card.id, known.id, known.legacy_id, known.name, already=True)
            raise ReferenceConflictError(PAIR_CONFIRMED, reason=REASON_CONFIRMED)
        chosen = links.confirm_candidate(session, card, ingredient_id, _now())
        if chosen is None:
            raise ReferenceConflictError(NOT_A_CANDIDATE, reason=NOT_A_CANDIDATE_REASON)
        session.commit()
    finally:
        session.rollback()
    log.info(
        "карточка %s: пара подтверждена — ингредиент %s (id %s)",
        card_id,
        chosen.ingredient_id,
        chosen.legacy_id,
    )
    return PairConfirmed(
        card_id, chosen.ingredient_id, chosen.legacy_id, chosen.name, already=False
    )


# ---------------------------------------------------------------------------
# «Добавить в справочник»
# ---------------------------------------------------------------------------
def to_reference(
    session: Session,
    filler: RowFiller | None,
    books: BookImport,
    card_id: int,
    raw_form: Mapping[str, str | None],
    *,
    actor_id: uuid.UUID | None,
    wait: timedelta = PAIR_WAIT,
) -> Transferred:
    """Заполнить строку ING карточки, перенести книгу кухни в базу и
    подтвердить пару.

    ``filler`` — ``None``, если книга кухни или карточек не настроена;
    ``books`` — перенос книг в базу (:class:`~kitchen.sync.cycle.SyncCycle`,
    собранный с коротким ожиданием, как после отправки карточки);
    ``raw_form`` — поля формы, как их ввёл человек
    (:meth:`~kitchen.domain.reference_row.ReferenceForm.parse`). Ключ запроса —
    один на карточку: повторное нажатие второй записи не даёт.

    Форма нужна, только если строка ждёт переноса: у повтора состоявшегося
    переноса и у «уже в справочнике» писать нечего. Поэтому её ошибки не
    отказ сразу — писатель поднимает их сам, найдя строку, ждущую переноса
    (422 до записи). Иначе «Связать с карточкой» со значениями строки шефа
    (пустое короткое имя, единица «уп») отвечал бы 422, а введённое человеком
    всё равно не записалось бы.

    Пару карточки уже подтвердил человек — строку, ждущую переноса, писатель
    не заполняет (409 «обновите страницу»); повтор своего состоявшегося
    переноса отвечает по журналу. Подтверждённую пару после записи не
    затираем: она остаётся как есть, а в ответе — оговорка.

    Отказы — наследники :class:`~kitchen.cards.drafts.CardsError` с текстом
    для человека; при любом отказе пара не подтверждается.
    """
    card = _card(session, card_id)
    name = card.name
    if filler is None:
        raise ReferenceUnavailableError(NOT_CONFIGURED)
    form: ReferenceForm | ReferenceFormError
    try:
        form = ReferenceForm.parse(raw_form)
    except ReferenceFormError as error:
        form = error
    result = _fill(filler, card_id, name, form, actor_id, confirmed=card.confirmed)
    found_in_sheet = result.journal_id is None
    if found_in_sheet:
        _check_known_id(session, card_id, name, result)
    problem = run_import(books, KITCHEN)
    if problem is not None:
        log.warning(
            "карточка %s: ингредиент в строке %s листа ING (id %s), но перенос книги кухни в "
            "базу не удался — его перенесёт следующий цикл: %s",
            card_id,
            result.row,
            result.ref_id,
            problem,
        )
    pair = _confirm_written(session, card_id, name, result.ref_id, wait)
    return _answer(card_id, result, pair, imported=problem is None)


def _fill(
    filler: RowFiller,
    card_id: int,
    name: str,
    form: ReferenceForm | ReferenceFormError,
    actor_id: uuid.UUID | None,
    *,
    confirmed: bool,
) -> FillResult:
    """Одна запись писателем — отказы словами для человека."""
    try:
        return filler.fill(
            name,
            form,
            actor_id=actor_id,
            request_key=fill_request_key(card_id),
            confirmed=confirmed,
        )
    except ReferenceFormError as error:
        raise ReferenceFormInvalidError(error.errors) from error
    except WriteRefusedError as error:
        raise _refusal(error) from error
    except ForbiddenWriteError as error:
        log.error("карточка %s: запись в лист ING закрыта правилом владения — %s", card_id, error)
        raise ReferenceUnavailableError(WRITING_CLOSED) from error
    except SQLAlchemyError as error:
        # Журнал по ключу карточки: легла запись или нет, повтор перечитает
        # строку и второй не напишет — «нажмите ещё раз» правда в обоих случаях.
        log.error(
            "карточка %s: база не ответила, пока шла запись в лист ING, — исход решит повтор: %s",
            card_id,
            _first_line(error),
        )
        raise ReferenceFailedError(NOT_CONFIRMED) from error


def _refusal(error: WriteRefusedError) -> CardsError:
    """Отказ писателя → отказ слоя: строка листа не та (409), лист сломан или
    таблица занята (503), Google не ответил или запись не подтверждена (502).
    Текст — писателя, словами спеки."""
    if isinstance(error, RowRefusedError):
        return ReferenceConflictError(str(error), reason=error.reason, row=error.row)
    if isinstance(error, SheetLayoutError | SheetBusyError):
        return ReferenceUnavailableError(str(error))
    if isinstance(error, WriteNotConfirmedError):
        return ReferenceFailedError(str(error), row=error.row, journal_id=error.journal_id)
    return ReferenceFailedError(str(error))


def _check_known_id(session: Session, card_id: int, name: str, result: FillResult) -> None:
    """id стоял в строке до нас — его ли это ингредиент? До переноса в базу.

    Не целое число — не id справочника. Известен базе под другим названием —
    строки ING сдвинуты: рядом с нашим названием чужой id (см. описание
    модуля). Базе ещё не известен — шеф вписал его сам недавно; проверит
    сверка после переноса.
    """
    if not _WHOLE_NUMBER.fullmatch(result.ref_id):
        log.warning(
            "карточка %s «%s»: в строке %s листа ING id «%s» — не целое число, пара не "
            "подтверждена",
            card_id,
            name,
            result.row,
            result.ref_id,
        )
        raise _shifted(result.row)
    try:
        known = links.ingredient_by_legacy_id(session, result.ref_id)
        known_name = None if known is None else known.name
    finally:
        session.rollback()
    if known_name is not None and not _same_name(known_name, name):
        log.warning(
            "карточка %s «%s»: в строке %s листа ING id %s, а в базе это «%s» — строки "
            "сдвинуты, пара не подтверждена",
            card_id,
            name,
            result.row,
            result.ref_id,
            known_name,
        )
        raise _shifted(result.row)


_Outcome = Literal["linked", "missing", "other", "kept", "failed"]


@dataclass(frozen=True, slots=True)
class _Pair:
    """Чем кончилось подтверждение пары после записи."""

    outcome: _Outcome
    ingredient_id: int | None
    """Ингредиент пары — только у ``linked``."""
    on_site: bool
    """Наш ингредиент виден на сайте под id строки — ``imported`` ответа."""


def _confirm_written(
    session: Session, card_id: int, name: str, ref_id: str, wait: timedelta
) -> _Pair:
    """Подтвердить пару с ингредиентом строки — и виден ли он на сайте.

    У ``linked`` и ``kept`` наш ингредиент на сайте, у ``missing`` и
    ``other`` — нет. ``failed`` о сайте ничего не говорит: подтвердить не
    дали очередь импорта или база, — это отдельное чтение
    (:func:`_ours_on_site`).
    """
    outcome, ingredient_id = _link_written(session, card_id, ref_id, wait)
    if outcome == "failed":
        return _Pair(outcome, None, on_site=_ours_on_site(session, card_id, name, ref_id))
    return _Pair(outcome, ingredient_id, on_site=outcome in ("linked", "kept"))


def _link_written(
    session: Session, card_id: int, ref_id: str, wait: timedelta
) -> tuple[_Outcome, int | None]:
    """Подтвердить пару с ингредиентом строки — в очереди импорта.

    Исход и ингредиент пары: ``linked`` — подтверждена; ``missing`` —
    ингредиента с этим id на сайте нет (перенос не прошёл или его убрали из
    листа); ``other`` — есть, но называется не как карточка; ``kept`` — пару
    уже подтвердил человек с другим ингредиентом, пока шёл перенос (или до
    него — у «уже в справочнике»): её не трогаем, решение человека важнее;
    ``failed`` — подтвердить не дали очередь импорта или база.
    """
    try:
        if not wait_for_import_lock(session, wait):
            log.warning(
                "карточка %s: пара с id %s не подтверждена — очередь импорта занята дольше %s",
                card_id,
                ref_id,
                wait,
            )
            return "failed", None
        card = links.live_card(session, card_id)
        ingredient = links.ingredient_by_legacy_id(session, ref_id)
        if ingredient is None or ingredient.removed_at is not None:
            return "missing", None
        if card is None:
            log.warning(
                "карточка %s убрана из листа, пока шёл перенос, — пара не подтверждена", card_id
            )
            return "failed", None
        if not _same_name(ingredient.name, card.name):
            log.warning(
                "карточка %s «%s»: в базе id %s — «%s», пара не подтверждена",
                card_id,
                card.name,
                ref_id,
                ingredient.name,
            )
            return "other", None
        if card.link_confirmed_at is not None and card.ingredient_id != ingredient.id:
            log.warning(
                "карточка %s «%s»: пара уже подтверждена с ингредиентом %s — на id %s её не меняем",
                card_id,
                card.name,
                card.ingredient_id,
                ref_id,
            )
            return "kept", None
        links.confirm_link(card, ingredient.id, _now())
        session.commit()
        return "linked", ingredient.id
    except SQLAlchemyError as error:
        log.error(
            "карточка %s: пара с id %s не подтверждена — база не ответила: %s",
            card_id,
            ref_id,
            _first_line(error),
        )
        return "failed", None
    finally:
        session.rollback()


def _ours_on_site(session: Session, card_id: int, name: str, ref_id: str) -> bool:
    """Пару подтвердить не дали — виден ли на сайте под id строки наш
    ингредиент: есть, не удалён из листа и называется как карточка. Только
    чтение, без очереди импорта: подтверждать здесь нечего. База не ответила
    — «не виден»: утверждать, что он на сайте, нечем."""
    try:
        ingredient = links.ingredient_by_legacy_id(session, ref_id)
        return (
            ingredient is not None
            and ingredient.removed_at is None
            and _same_name(ingredient.name, name)
        )
    except SQLAlchemyError as error:
        log.error(
            "карточка %s: не проверить, виден ли на сайте id %s, — база не ответила: %s",
            card_id,
            ref_id,
            _first_line(error),
        )
        return False
    finally:
        session.rollback()


def _answer(card_id: int, result: FillResult, pair: _Pair, *, imported: bool) -> Transferred:
    """Ответ человеку — или отказ, если id в строке стоял до нас и не того
    ингредиента.

    Перенос прошёл (``imported``), а под id строки на сайте не наш ингредиент
    (его нет или у него другое название): id стоял до нас — строки сдвинуты
    (409); id записали мы — запись состоялась, ответ удачный, а пара не
    подтверждена: оговорка «проверьте строку листа» и ERROR в лог. Без
    обещания «появится через несколько минут»: само это не пройдёт. Иначе наш
    ингредиент на сайте не виден — оговорка :data:`NOT_IMPORTED`.
    """
    found_in_sheet = result.journal_id is None
    stranger = pair.outcome == "other" or (pair.outcome == "missing" and imported)
    if stranger and found_in_sheet:
        raise _shifted(result.row)
    notes: list[str] = []
    if result.shifted is not None:
        notes.append(_shifted_note(result.shifted))
    if not pair.on_site and not stranger:
        notes.append(NOT_IMPORTED)
    if pair.outcome == "failed":
        notes.append(PAIR_NOT_CONFIRMED)
    if pair.outcome == "kept":
        notes.append(_kept_text(result.row))
    if stranger:
        log.error(
            "карточка %s: строка %s листа ING записана (id %s, журнал №%s), а пару не "
            "подтвердить: %s",
            card_id,
            result.row,
            result.ref_id,
            result.journal_id,
            "ингредиента нет на сайте" if pair.outcome == "missing" else "на сайте другое название",
        )
        notes.append(_other_name_text(result.row, result.ref_id))
    log.info(
        "карточка %s: в справочнике — строка %s, id %s; пара %s",
        card_id,
        result.row,
        result.ref_id,
        "подтверждена" if pair.outcome == "linked" else "не подтверждена",
    )
    return Transferred(
        row=result.row,
        ref_id=result.ref_id,
        already=result.already,
        imported=pair.on_site,
        linked=pair.outcome == "linked",
        ingredient_id=pair.ingredient_id,
        message=result.message,
        shifted=result.shifted,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Мелочи
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _CardState:
    """Карточка, как её видит перенос, — до первого запроса к Google."""

    name: str
    confirmed: bool
    """Пару подтвердил человек (``link_confirmed_at``)."""


def _card(session: Session, card_id: int) -> _CardState:
    """Название карточки и подтверждена ли её пара — и сразу закрыть
    транзакцию: дальше Google."""
    try:
        card = links.live_card(session, card_id)
        state = (
            None
            if card is None
            else _CardState(card.name, confirmed=card.link_confirmed_at is not None)
        )
    finally:
        session.rollback()
    if state is None:
        raise ReferenceCardNotFoundError
    return state


def _shifted(row: int) -> ReferenceConflictError:
    return ReferenceConflictError(NotFound.SHIFTED.message, reason=NotFound.SHIFTED.value, row=row)


def _same_name(left: str, right: str) -> bool:
    """Названия сравниваются как ключи карточек при импорте."""
    return normalise_name(left) == normalise_name(right)


def _now() -> datetime:
    return datetime.now(UTC)


def _first_line(error: Exception) -> str:
    """Первая строка ошибки базы: дальше SQLAlchemy печатает запрос с данными."""
    return str(error).splitlines()[0] if str(error) else type(error).__name__
