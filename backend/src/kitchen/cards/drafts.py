"""Черновик карточки ингредиента: правки повара, фото, подсказки.

Повар проходит мастер на телефоне, и каждый шаг сохраняется здесь —
черновик живёт на сервере, один активный на повара. В лист он не пишется,
пока повар не нажмёт «Отправить».

Правила, ради которых этот слой есть:

* **Чужой черновик не существует.** Каждый запрос ищет черновик вместе с
  владельцем, отказ — тот же, что у несуществующего.
* **id файла Drive — только из нашей загрузки.** Ни одна правка его не
  принимает: с охватом ``drive`` корзина и скачивание дотянулись бы до
  любого файла, открытого сервисному аккаунту.
* **Сначала Drive, потом база.** Загрузка не удалась — черновик не изменился
  ни в чём. Пока идёт запрос к Google, транзакция базы закрыта: соединение
  не висит «в транзакции» до минуты.
* **Корзина — уборка, а не часть действия.** Отказ корзины не отнимает у
  повара ни новое фото, ни отмену черновика: сирота остаётся в закрытой
  папке и пишется в лог. А фото черновика, по которому уже была попытка
  записи в лист, в корзину не идут вовсе: строка могла лечь, и ссылки на
  них у шефа должны открываться.
* **Текст повара не обрезается молча** — длиннее предела поля отказ с
  пределом; КБЖУ разбирает домен, неясное число — отказ с названием поля.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kitchen.db import drafts as store
from kitchen.db.drafts import CardName, ReferenceName
from kitchen.domain.cards import (
    APPROVALS,
    APPROVED,
    DEFAULT_CATEGORIES,
    FIELD_TITLES,
    NUTRIENT_FIELDS,
    REJECTED,
    TEXT_LIMITS,
    CardDraftData,
    NutrientUnclearError,
    check_nutrients,
    clean_text,
    missing_for_submit,
    parse_nutrient,
    photo_file_name,
    text_too_long,
)
from kitchen.domain.matching import Entry, NameIndex, normalise_name
from kitchen.sync.drive import DriveError

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime
    from decimal import Decimal

    from sqlalchemy.orm import Session

    from kitchen.db.models import CardDraft
    from kitchen.sync.drive import DriveClient

__all__ = ["CardName", "ReferenceName"]

log = logging.getLogger(__name__)

STEPS: tuple[str, ...] = (
    "supplier",
    "category",
    "name",
    "label",
    "review",
    "approval",
    "photos",
    "description",
    "summary",
)
"""Шаги мастера по порядку — «Шаг N из 9»: поставщик, категория, название,
фото этикетки, проверка распознанного, «согласован?», три фото продукта,
описание, итог."""

PHOTO_LIMIT = 8 * 1024 * 1024
"""Самое большое фото, которое примет сервер. Браузер уменьшает снимок до
1600 px — это сотни килобайт; больше 8 МБ — не наш снимок."""

PROXY_LIMIT = PHOTO_LIMIT + 1024 * 1024
"""Самый большой файл, который прокси отдаст из Drive. Наши загрузки — не
больше :data:`PHOTO_LIMIT`; запас — чтобы снижение предела загрузки не
отрезало уже загруженные фото. Заметно больше — файл в папке подменили:
отказ, а не обрезанная картинка."""

NUTRIENT_INPUT_LIMIT = 100
"""Сколько знаков повар может вписать в белки, жиры, углеводы или ккал.

Разбор ккал (``_KCAL_NUMBER`` в домене) на длинной строке растёт
квадратично: на 10 000 знаков — около секунды, на 100 000 — часы. Сто
знаков с запасом вмещают любую запись с этикетки."""

_RAW_TEXT_SLACK = 4
"""Во сколько раз сырой текст может быть длиннее предела поля, прежде чем
его откажут, не разбирая. Чистка — проход по каждому символу, и 25 МБ
пробелов (столько пропускает nginx) она разбирала бы секундами; у живого
текста лишних пробелов вчетверо больше, чем букв, не бывает."""

PHOTO_SLOTS: dict[str, str] = {
    "label": "label_file_id",
    "package": "package_file_id",
    "before": "before_file_id",
    "after": "after_file_id",
}
"""Вид фото → колонка черновика с id файла. Виды — те же, что у имени файла
в домене (``PHOTO_KINDS``); совпадение проверяет тест."""

_JPEG_START = b"\xff\xd8\xff"
_JPEG_END = b"\xff\xd9"


def submit_request_key(draft_id: uuid.UUID) -> str:
    """Ключ отправки черновика в лист — один на черновик (журнал ``sheet_writes``)."""
    return f"card-draft:{draft_id}"


# ---------------------------------------------------------------------------
# Отказы — текст для повара
# ---------------------------------------------------------------------------
class CardsError(Exception):
    """Отказ, который увидит повар: текст исключения — для него."""


class DraftNotFoundError(CardsError):
    """Черновика нет, он не этого повара или уже не активен."""

    def __init__(self) -> None:
        super().__init__("Черновик не найден — обновите страницу")


class DraftExistsError(CardsError):
    def __init__(self) -> None:
        super().__init__("У вас уже есть незаконченная карточка — продолжите её или начните заново")


class DraftFieldError(CardsError):
    """Правка поля не принята; ``field`` — какое поле подсветить."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


class PhotoTooLargeError(CardsError):
    def __init__(self) -> None:
        super().__init__(f"Фото больше {PHOTO_LIMIT // (1024 * 1024)} МБ — сфотографируйте ещё раз")


class NotJpegError(CardsError):
    def __init__(self) -> None:
        super().__init__("Фото не в формате JPEG или не загрузилось целиком — выберите его ещё раз")


class PhotoNeedsNamesError(CardsError):
    def __init__(self) -> None:
        super().__init__("Сначала укажите поставщика и название — по ним называется файл фото")


class PhotoMissingError(CardsError):
    def __init__(self) -> None:
        super().__init__("Этого фото в черновике нет")


class StorageNotConfiguredError(CardsError):
    def __init__(self) -> None:
        super().__init__("Хранилище фото не настроено — сообщите администратору")


class StorageError(CardsError):
    """Google Drive не сделал, что просили; текст — из ``explain_drive_error``."""


# ---------------------------------------------------------------------------
# Правка повара
# ---------------------------------------------------------------------------
def validate_changes(changes: Mapping[str, str | None]) -> dict[str, str | Decimal | None]:
    """Правка черновика → значения для колонок. Неверное — :class:`DraftFieldError`.

    Проверяется всё до того, как что-то изменится: правка принимается
    целиком или не принимается вовсе. Первое неверное поле — в отказе.
    """
    return {name: _validated(name, raw) for name, raw in changes.items()}


def _validated(name: str, raw: str | None) -> str | Decimal | None:
    if name in TEXT_LIMITS:
        return _text(name, raw)
    if name in NUTRIENT_FIELDS:
        return _nutrient(name, raw)
    if name == "approval":
        if raw is None or raw in APPROVALS:
            return raw
        raise DraftFieldError(name, f"Согласован: только «{APPROVED}» или «{REJECTED}»")
    if name == "step":
        if raw in STEPS:
            return raw
        raise DraftFieldError(name, "Неизвестный шаг мастера — обновите страницу")
    # id фото, статус и прочее служебное правкой не меняются.
    raise DraftFieldError(name, f"Поле «{name[:40]}» вручную не меняется")


def _text(name: str, raw: str | None) -> str:
    limit = TEXT_LIMITS[name]
    if raw is not None and (len(raw) > _RAW_TEXT_SLACK * limit or text_too_long(name, raw)):
        raise DraftFieldError(name, f"{FIELD_TITLES[name]}: не больше {limit} знаков")
    return clean_text(name, raw)


def _nutrient(name: str, raw: str | None) -> Decimal | None:
    title = FIELD_TITLES[name]
    if raw is not None and len(raw) > NUTRIENT_INPUT_LIMIT:
        raise DraftFieldError(name, f"{title}: не больше {NUTRIENT_INPUT_LIMIT} знаков")
    try:
        return parse_nutrient(raw)
    except NutrientUnclearError as error:
        raise DraftFieldError(name, f"{title}: {error}") from error


def renew_nutrient_warnings(
    notes: Sequence[str],
    before: Sequence[Decimal | None],
    after: Sequence[Decimal | None],
) -> list[str]:
    """Замечания черновика после того, как поменялись белки, жиры, углеводы или ккал.

    В замечаниях — два рода текста. Что заметило распознавание на самой
    этикетке (число не прочитано, пищевая ценность на 100 мл, срок неясен),
    правкой чисел не исправляется и остаётся. Проверка правдоподобия чисел
    (:func:`check_nutrients`) говорит о числах, которые стоят в черновике
    сейчас: прежняя её выдача уходит, новая — приходит. ``before`` и
    ``after`` — четвёрки (белки, жиры, углеводы, ккал).

    Опирается на то, что проверка в замечаниях всегда посчитана по числам
    черновика: распознавание кладёт её вместе с самими числами, правка —
    здесь. Одинаковые замечания не повторяются.
    """
    stale = set(_check(before))
    kept = [note for note in notes if note not in stale]
    fresh = [warning for warning in _check(after) if warning not in kept]
    return kept + fresh


def _check(values: Sequence[Decimal | None]) -> tuple[str, ...]:
    protein, fat, carbs, kcal = values
    return check_nutrients(protein, fat, carbs, kcal)


def _nutrients(draft: CardDraft) -> tuple[Decimal | None, ...]:
    return (draft.protein, draft.fat, draft.carbs, draft.kcal)


# ---------------------------------------------------------------------------
# Черновик
# ---------------------------------------------------------------------------
def current_draft(session: Session, owner_id: uuid.UUID) -> CardDraft | None:
    """Незаконченная карточка повара — с неё он продолжит."""
    return store.active_draft(session, owner_id)


def start_draft(session: Session, owner_id: uuid.UUID) -> CardDraft:
    """Новый черновик. Уже есть активный — :class:`DraftExistsError`: второй
    «Начать» (вторая вкладка, двойное нажатие) не теряет первый."""
    try:
        draft = store.add_draft(session, owner_id)
    except store.ActiveDraftExistsError as error:
        raise DraftExistsError from error
    session.commit()
    return draft


def update_draft(
    session: Session, owner_id: uuid.UUID, draft_id: uuid.UUID, changes: Mapping[str, str | None]
) -> CardDraft:
    """Правка черновика: только переданные поля, целиком или никак.

    Поменялись числа КБЖУ — замечания к ним пересчитываются
    (:func:`renew_nutrient_warnings`).
    """
    draft = _active(session, owner_id, draft_id, lock=True)
    values = validate_changes(changes)
    before = _nutrients(draft)
    for name, value in values.items():
        setattr(draft, name, value)
    after = _nutrients(draft)
    if after != before:
        draft.recognition_warnings = renew_nutrient_warnings(
            draft.recognition_warnings, before, after
        )
    session.commit()
    return draft


def cancel_draft(
    session: Session, drive: DriveClient, owner_id: uuid.UUID, draft_id: uuid.UUID
) -> None:
    """«Начать заново»: черновик отменён, его фото — в корзину Drive.

    Сначала база: не удалась корзина — черновик всё равно отменён, а фото
    остаются сиротами в закрытой папке (лог). Была попытка записи в лист —
    фото не трогаются вовсе.
    """
    draft = _active(session, owner_id, draft_id, lock=True)
    draft.status = store.CANCELLED
    files = [file_id for file_id in _photo_ids(draft).values() if file_id]
    keep = bool(files) and store.sheet_write_exists(session, submit_request_key(draft.id))
    session.commit()
    _discard(drive, files, draft.id, reason="черновик отменён", keep=keep)


def draft_data(draft: CardDraft) -> CardDraftData:
    """Содержимое черновика для домена: чего не хватает, строка для листа."""
    return CardDraftData(
        supplier=draft.supplier,
        category=draft.category,
        name=draft.name,
        label_name=draft.label_name,
        manufacturer=draft.manufacturer,
        composition=draft.composition,
        protein=draft.protein,
        fat=draft.fat,
        carbs=draft.carbs,
        kcal=draft.kcal,
        shelf_life_sealed=draft.shelf_life_sealed,
        shelf_life_defrost=draft.shelf_life_defrost,
        shelf_life_after=draft.shelf_life_after,
        defrost_conditions=draft.defrost_conditions,
        description=draft.description,
        approval=draft.approval or "",
        label_file_id=draft.label_file_id or "",
        package_file_id=draft.package_file_id or "",
        before_file_id=draft.before_file_id or "",
        after_file_id=draft.after_file_id or "",
    )


def missing(draft: CardDraft) -> tuple[str, ...]:
    """Чего не хватает для отправки — названиями для повара."""
    return missing_for_submit(draft_data(draft))


def photos(draft: CardDraft) -> dict[str, bool]:
    """Какие фото в черновике есть. Сами id файлов клиенту не нужны: фото
    показываются через прокси по виду."""
    return {kind: bool(file_id) for kind, file_id in _photo_ids(draft).items()}


def _active(
    session: Session, owner_id: uuid.UUID, draft_id: uuid.UUID, *, lock: bool = False
) -> CardDraft:
    draft = store.active_draft(session, owner_id, draft_id, lock=lock)
    if draft is None:
        raise DraftNotFoundError
    return draft


def _photo_ids(draft: CardDraft) -> dict[str, str | None]:
    return {
        "label": draft.label_file_id,
        "package": draft.package_file_id,
        "before": draft.before_file_id,
        "after": draft.after_file_id,
    }


def _slot(kind: str) -> str:
    slot = PHOTO_SLOTS.get(kind)
    if slot is None:
        raise ValueError(f"Неизвестный вид фото «{kind[:40]}»")
    return slot


# ---------------------------------------------------------------------------
# Фото
# ---------------------------------------------------------------------------
def check_jpeg(content: bytes) -> None:
    """Не больше :data:`PHOTO_LIMIT` и похоже на JPEG целиком: начало
    ``FF D8 FF`` и конец ``FF D9``.

    Тип, который назвал браузер, — слова клиента; смотрим на сами байты.
    Обрыв по дороге отрезает конец — такой файл тоже не JPEG.
    """
    if len(content) > PHOTO_LIMIT:
        raise PhotoTooLargeError
    if not (content.startswith(_JPEG_START) and content.endswith(_JPEG_END)):
        raise NotJpegError


def put_photo(
    session: Session,
    drive: DriveClient,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    kind: str,
    content: bytes,
    *,
    now: datetime,
) -> CardDraft:
    """Положить фото в слот черновика: сначала Drive, потом база.

    Прежнее фото слота после этого уходит в корзину — кроме случая, когда
    по черновику уже была попытка записи в лист.
    """
    slot = _slot(kind)
    draft = _active(session, owner_id, draft_id)
    if not drive.folder_id:
        raise StorageNotConfiguredError
    check_jpeg(content)
    if not draft.supplier or not draft.name:
        raise PhotoNeedsNamesError
    name = photo_file_name(draft.supplier, draft.name, kind, now)
    properties = {"cardDraft": str(draft.id), "photoKind": kind}

    # Запрос к Google идёт до минуты; транзакцию базы на это время закрываем.
    session.rollback()
    try:
        file_id = drive.upload_jpeg(content, name=name, app_properties=properties)
    except DriveError as error:
        log.warning(
            "фото черновика %s не загружено в Drive: %s (код %s, причина %s)",
            draft_id,
            error.kind,
            error.status,
            error.reason or "—",
        )
        raise StorageError(str(error)) from error

    # Черновик могли отменить, пока шла загрузка: тогда файл — сирота.
    locked = store.active_draft(session, owner_id, draft_id, lock=True)
    if locked is None:
        _discard(drive, [file_id], draft_id, reason="черновик закрыт во время загрузки", keep=False)
        raise DraftNotFoundError
    previous: str | None = getattr(locked, slot)
    setattr(locked, slot, file_id)
    keep = bool(previous) and store.sheet_write_exists(session, submit_request_key(locked.id))
    session.commit()
    if previous:
        _discard(drive, [previous], locked.id, reason="заменено новым фото", keep=keep)
    return locked


def remove_photo(
    session: Session, drive: DriveClient, owner_id: uuid.UUID, draft_id: uuid.UUID, kind: str
) -> CardDraft:
    """Убрать фото из слота; файл — в корзину по тем же правилам, что при замене."""
    slot = _slot(kind)
    draft = _active(session, owner_id, draft_id, lock=True)
    previous: str | None = getattr(draft, slot)
    setattr(draft, slot, None)
    keep = bool(previous) and store.sheet_write_exists(session, submit_request_key(draft.id))
    session.commit()
    if previous:
        _discard(drive, [previous], draft.id, reason="фото убрано из черновика", keep=keep)
    return draft


def photo_bytes(
    session: Session, drive: DriveClient, owner_id: uuid.UUID, draft_id: uuid.UUID, kind: str
) -> bytes:
    """Содержимое фото черновика — для прокси. Файл берётся только из слота."""
    slot = _slot(kind)
    draft = _active(session, owner_id, draft_id)
    file_id: str | None = getattr(draft, slot)
    if not file_id:
        raise PhotoMissingError
    session.rollback()
    try:
        return drive.download(file_id, max_bytes=PROXY_LIMIT)
    except DriveError as error:
        if error.kind == "too_large":
            log.error(
                "фото %s черновика %s в Drive больше %s байт — файл подменили? Не отдано",
                file_id,
                draft_id,
                PROXY_LIMIT,
            )
        else:
            log.warning(
                "фото %s черновика %s не скачано из Drive: %s (код %s)",
                file_id,
                draft_id,
                error.kind,
                error.status,
            )
        raise StorageError(str(error)) from error


def _discard(
    drive: DriveClient,
    file_ids: Sequence[str],
    draft_id: uuid.UUID,
    *,
    reason: str,
    keep: bool,
) -> None:
    """Отправить фото в корзину Drive — не роняя ответ.

    ``keep`` — по черновику была попытка записи в лист: строка могла лечь со
    ссылками на эти фото, корзина сломала бы их у шефа. Тогда только лог.
    Отказ корзины («файл не из папки фото», Drive не ответил) — тоже только
    лог: сирота лежит в закрытой папке, вреда от неё нет, а след нужен.
    """
    if not file_ids:
        return
    if keep:
        log.warning(
            "фото черновика %s оставлены в Drive (%s): по черновику была попытка записи "
            "в таблицу, ссылки на них могли лечь в лист — %s",
            draft_id,
            reason,
            ", ".join(file_ids),
        )
        return
    for file_id in file_ids:
        try:
            drive.trash(file_id)
        except (DriveError, ValueError) as error:
            log.warning(
                "фото-сирота %s черновика %s (%s): в корзину не отправлено — %s",
                file_id,
                draft_id,
                reason,
                error,
            )


# ---------------------------------------------------------------------------
# Подсказки: категории, поставщики, похожие названия
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Options:
    categories: list[str]
    """Затравка бота и категории карточек — частые первыми. «Другая…» —
    действие экрана, а не категория, её здесь нет."""
    suppliers: list[str]


def options(session: Session) -> Options:
    return Options(
        categories=ranked(store.card_categories(session), DEFAULT_CATEGORIES),
        suppliers=ranked(store.card_suppliers(session)),
    )


def ranked(values: Iterable[str], seed: Sequence[str] = ()) -> list[str]:
    """Варианты выбора по частоте в карточках, без повторов.

    Регистр и лишние пробелы не делают вариант новым: «сыры» — это «Сыры».
    Показывается написание затравки, а у новых — самое частое. При равной
    частоте затравка идёт первой в своём порядке, остальное — по алфавиту.
    """
    counts: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    for raw in values:
        text = " ".join(raw.split())
        if not text:
            continue
        key = text.casefold()
        counts[key] += 1
        spellings.setdefault(key, Counter())[text] += 1

    position = {item.casefold(): index for index, item in enumerate(seed)}
    shown = {item.casefold(): item for item in seed}
    for key, variants in spellings.items():
        if key not in shown:
            shown[key] = min(variants, key=lambda variant: (-variants[variant], variant))
    order = sorted(shown, key=lambda key: (-counts[key], position.get(key, len(seed)), shown[key]))
    return [shown[key] for key in order]


@dataclass(frozen=True, slots=True)
class CardMatches:
    exact: tuple[CardName, ...] = ()
    """То же название после нормализации — для карточек в листе это дубль:
    писатель такую строку не запишет."""
    similar: tuple[CardName, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceMatches:
    exact: tuple[str, ...] = ()
    """Имя из справочника ING: взяв его, карточка склеится с позицией сама."""
    similar: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NameCheck:
    cards: CardMatches = field(default_factory=CardMatches)
    """Карточки, что есть в листе."""
    hidden: CardMatches = field(default_factory=CardMatches)
    """Карточки, убранные из листа. Имя свободно, но новая строка с ним
    вернёт старую карточку на сайт — со старыми связями."""
    reference: ReferenceMatches = field(default_factory=ReferenceMatches)


def name_check(session: Session, name: str) -> NameCheck:
    return match_names(name, store.card_names(session), store.reference_names(session))


def match_names(
    name: str, cards: Sequence[CardName], reference: Sequence[ReferenceName]
) -> NameCheck:
    """Те же и похожие названия среди карточек и в справочнике.

    Правила — как у сверки (:mod:`kitchen.domain.matching`): нормализация
    имени, порог похожести, архивные позиции справочника не предлагаются.
    Похожие ищутся, только когда точных нет.
    """
    if not normalise_name(name):
        return NameCheck()
    index = NameIndex(Entry(key=row.key, name=row.name, status=row.status) for row in reference)
    match = index.match(name)
    return NameCheck(
        cards=_card_matches(name, [card for card in cards if not card.hidden]),
        hidden=_card_matches(name, [card for card in cards if card.hidden]),
        reference=ReferenceMatches(
            exact=tuple(dict.fromkeys(entry.name for entry in match.exact)),
            similar=tuple(dict.fromkeys(found.entry.name for found in match.similar)),
        ),
    )


def _card_matches(name: str, cards: Sequence[CardName]) -> CardMatches:
    index = NameIndex(Entry(key=str(number), name=card.name) for number, card in enumerate(cards))
    match = index.match(name)
    return CardMatches(
        exact=tuple(cards[int(entry.key)] for entry in match.exact),
        similar=tuple(cards[int(found.entry.key)] for found in match.similar),
    )
