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
  повара ни новое фото, ни отмену черновика: сирота остаётся в папке фото
  без ссылок из листа и базы и пишется в лог. А фото черновика, по которому
  уже была попытка записи в лист, в корзину не идут вовсе: строка могла
  лечь, и ссылки на них у шефа должны открываться.
* **Пока карточка отправляется, черновик не меняется.** Строка уже собрана,
  а ссылки на фото летят в лист: правка, замена и удаление фото и «Начать
  заново» ждут конца отправки (409) — иначе правка молча не попала бы в
  лист, а фото, на которое сошлётся строка шефа, ушло бы в корзину.
* **Текст повара не обрезается молча** — длиннее предела поля отказ с
  пределом; КБЖУ разбирает домен, неясное число — отказ с названием поля.
* **Проверка КБЖУ не хранится.** В черновике — только замечания самой
  этикетки; проверку чисел черновик считает при каждой выдаче по тем числам,
  что в нём сейчас: правка повара или новая формулировка проверки не
  оставляют устаревших замечаний.
* **Повару — что делать, а не что ответил Google.** Подробности сбоя Drive
  (код, причина, имя переменной окружения) — только в лог.
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
    from datetime import datetime, timedelta
    from decimal import Decimal

    from sqlalchemy.orm import Session

    from kitchen.db.models import CardDraft
    from kitchen.sync.drive import DriveClient

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

    def extra(self) -> dict[str, object]:
        """Что ещё положить в тело отказа рядом с текстом (поле, строку…)."""
        return {}


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

    def extra(self) -> dict[str, object]:
        return {"field": self.field}


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
    """Google Drive не сделал, что просили; текст — :func:`storage_text`."""


class DraftConflictError(CardsError):
    """С черновиком так сейчас нельзя: идёт отправка в лист, распознавание
    уже идёт, нет фото этикетки, этикетку только что заменили."""


WAIT_FOR_SUBMIT = "Карточка отправляется — подождите"


def storage_text(error: DriveError, *, upload: bool = False) -> str:
    """Сбой Drive — словами для повара: что делать, без подробностей Google.

    «Попробуйте позже» — когда это лечится временем; «сообщите
    администратору» — когда чинить настройку (место, доступ, ключ). При
    скачивании файла нет или его подменили — фото для черновика больше нет,
    нужно новое; при загрузке «не найдено» — это папка, и чинит её
    администратор. Что именно ответил Google — в лог, рядом с id черновика.
    """
    if error.kind in ("unavailable", "bad_reply"):
        return "Хранилище фото недоступно — попробуйте позже"
    if not upload and error.kind in ("not_found", "too_large"):
        return "Фото пропало из хранилища — сфотографируйте ещё раз"
    return "Хранилище фото недоступно — сообщите администратору"


def _storage_trouble(
    error: DriveError, draft_id: uuid.UUID, *, file_id: str | None
) -> StorageError:
    """В лог — что ответил Google; повару — :func:`storage_text`.

    ``file_id`` — какое фото скачивали; ``None`` — загружали новое."""
    log.warning(
        "фото %s черновика %s %s: %s (код %s, причина %s): %s",
        file_id or "—",
        draft_id,
        "не загружено в Drive" if file_id is None else "не скачано из Drive",
        error.kind,
        error.status,
        error.reason or "—",
        error,
    )
    return StorageError(storage_text(error, upload=file_id is None))


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


def shown_warnings(notes: Sequence[str], numbers: Sequence[Decimal | None]) -> list[str]:
    """Замечания повару: замечания этикетки и проверка КБЖУ по ``numbers``.

    Два рода текста. Что заметило распознавание на самой этикетке (число не
    прочитано, пищевая ценность на 100 мл, срок неясен), правкой чисел не
    исправляется и хранится в черновике до следующего распознавания или
    замены этикетки. Проверка правдоподобия (:func:`check_nutrients`)
    говорит о числах, что стоят в черновике сейчас, — поэтому она не
    хранится, а считается здесь, при каждой выдаче. ``numbers`` — четвёрка
    (белки, жиры, углеводы, ккал). Одинаковые замечания не повторяются.
    """
    protein, fat, carbs, kcal = numbers
    kept = list(notes)
    return kept + [
        check for check in check_nutrients(protein, fat, carbs, kcal) if check not in kept
    ]


def draft_warnings(draft: CardDraft) -> list[str]:
    """Замечания черновика, какими их видит повар (:func:`shown_warnings`)."""
    return shown_warnings(draft.recognition_warnings, _nutrients(draft))


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
    session: Session,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    changes: Mapping[str, str | None],
    *,
    submit_window: timedelta,
) -> CardDraft:
    """Правка черновика: только переданные поля, целиком или никак.

    Идёт отправка — :class:`DraftConflictError`: строка уже собрана из
    черновика, и правка в лист не попала бы, а повар считал бы, что попала.
    Проверка КБЖУ не хранится — новые числа она увидит при выдаче сама
    (:func:`draft_warnings`)."""
    draft = require_active(session, owner_id, draft_id, lock=True)
    _not_submitting(session, draft.id, submit_window)
    values = validate_changes(changes)
    for name, value in values.items():
        setattr(draft, name, value)
    session.commit()
    return draft


def cancel_draft(
    session: Session,
    drive: DriveClient,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    *,
    submit_window: timedelta,
) -> None:
    """«Начать заново»: черновик отменён, его фото — в корзину Drive.

    Сначала база: не удалась корзина — черновик всё равно отменён, а фото
    остаются сиротами в папке фото, без ссылок из листа и базы (лог). Была
    попытка записи в лист — фото не трогаются вовсе. Идёт отправка (отметка
    моложе ``submit_window``) — :class:`DraftConflictError`: черновик может
    вот-вот лечь в лист.
    """
    draft = require_active(session, owner_id, draft_id, lock=True)
    _not_submitting(session, draft.id, submit_window)
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


def require_active(
    session: Session, owner_id: uuid.UUID, draft_id: uuid.UUID, *, lock: bool = False
) -> CardDraft:
    """Активный черновик повара; иначе — :class:`DraftNotFoundError`."""
    draft = store.active_draft(session, owner_id, draft_id, lock=lock)
    if draft is None:
        raise DraftNotFoundError
    return draft


def forget_recognition(draft: CardDraft) -> None:
    """Сбросить распознавание: статус (и «идёт»), прочитанное и замечания
    этикетки — они о прежнем фото. Поля черновика остаются: повар их видел и
    мог править; проверка КБЖУ пересчитается по ним сама."""
    draft.recognition_status = None
    draft.recognition_started_at = None
    draft.recognition = None
    draft.recognition_warnings = []


def _not_submitting(session: Session, draft_id: uuid.UUID, window: timedelta) -> None:
    if store.submitting(session, draft_id, window):
        raise DraftConflictError(WAIT_FOR_SUBMIT)


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
    submit_window: timedelta,
) -> CardDraft:
    """Положить фото в слот черновика: сначала Drive, потом база.

    Прежнее фото слота после этого уходит в корзину — кроме случая, когда
    по черновику уже была попытка записи в лист. Идёт отправка (отметка
    моложе ``submit_window``) — :class:`DraftConflictError`, и до загрузки, и
    после неё: отправка могла начаться, пока фото летело в Drive.

    Новая этикетка сбрасывает распознавание — статус, прочитанное и
    замечания этикетки: они о прежнем фото. Поля черновика остаются (повар
    их видел и мог править); проверка КБЖУ пересчитается по ним сама.
    """
    slot = _slot(kind)
    draft = require_active(session, owner_id, draft_id)
    if not drive.folder_id:
        raise StorageNotConfiguredError
    check_jpeg(content)
    if not draft.supplier or not draft.name:
        raise PhotoNeedsNamesError
    _not_submitting(session, draft.id, submit_window)
    name = photo_file_name(draft.supplier, draft.name, kind, now)
    properties = {"cardDraft": str(draft.id), "photoKind": kind}

    # Запрос к Google идёт до минуты; транзакцию базы на это время закрываем.
    session.rollback()
    try:
        file_id = drive.upload_jpeg(content, name=name, app_properties=properties)
    except DriveError as error:
        raise _storage_trouble(error, draft_id, file_id=None) from error

    # Черновик могли отменить, пока шла загрузка: тогда файл — сирота.
    locked = store.active_draft(session, owner_id, draft_id, lock=True)
    if locked is None:
        _discard(drive, [file_id], draft_id, reason="черновик закрыт во время загрузки", keep=False)
        raise DraftNotFoundError
    # А могли и начать отправку: ссылка на прежнее фото уже летит в лист —
    # слот не меняем, новое фото — сирота.
    if store.submitting(session, locked.id, submit_window):
        session.rollback()
        _discard(
            drive, [file_id], draft_id, reason="во время загрузки началась отправка", keep=False
        )
        raise DraftConflictError(WAIT_FOR_SUBMIT)
    previous: str | None = getattr(locked, slot)
    setattr(locked, slot, file_id)
    if kind == "label":
        forget_recognition(locked)
    keep = bool(previous) and store.sheet_write_exists(session, submit_request_key(locked.id))
    session.commit()
    if previous:
        _discard(drive, [previous], locked.id, reason="заменено новым фото", keep=keep)
    return locked


def remove_photo(
    session: Session,
    drive: DriveClient,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    kind: str,
    *,
    submit_window: timedelta,
) -> CardDraft:
    """Убрать фото из слота; файл — в корзину по тем же правилам, что при замене.

    Убрали этикетку — распознавание сбрасывается, как при замене: оно о
    фото, которого больше нет."""
    slot = _slot(kind)
    draft = require_active(session, owner_id, draft_id, lock=True)
    _not_submitting(session, draft.id, submit_window)
    previous: str | None = getattr(draft, slot)
    setattr(draft, slot, None)
    if kind == "label":
        forget_recognition(draft)
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
    draft = require_active(session, owner_id, draft_id)
    file_id: str | None = getattr(draft, slot)
    if not file_id:
        raise PhotoMissingError
    session.rollback()
    return fetch_photo(drive, file_id, draft_id)


def fetch_photo(drive: DriveClient, file_id: str, draft_id: uuid.UUID) -> bytes:
    """Скачать фото черновика из Drive — не больше :data:`PROXY_LIMIT`.

    Транзакцию базы вызывающий закрывает сам: запрос к Google идёт до минуты.
    Сбой — :class:`StorageError` с текстом для повара, подробности — в лог.
    """
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
            raise StorageError(storage_text(error)) from error
        raise _storage_trouble(error, draft_id, file_id=file_id) from error


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
    лог: сирота лежит в папке фото, ссылки на неё никто не получал, вреда от
    неё нет, а след нужен.
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
