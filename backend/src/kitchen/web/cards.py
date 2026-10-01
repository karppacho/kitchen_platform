"""Ручки карточек ингредиентов: черновик, фото, подсказки, распознавание, отправка.

Тонкие: разобрать запрос, позвать слой приложения (``kitchen.cards``),
отдать ответ. Всё, что решает, — там. Внешние участники — Drive, чтец
этикеток (polza.ai), писатель строки и перенос книги в базу — приходят
зависимостями из ``app.state``: тест подменяет их фальшивками.

Ручки — для тех, кто заводит карточки: повар, шеф, разработчик. Коммерсу
раздел не нужен — 403. Черновик видит только его владелец: чужой — 404,
как несуществующий. Числа КБЖУ — строками, как во всём API: двоичная дробь
JSON их бы исказила.

Изменяющие запросы с кукой сессии стоят за общей защитой от подделки
(``kitchen.web.csrf``): без заголовка ``X-Kitchen-Csrf: 1`` до ручки они не
доходят.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from kitchen.cards import drafts, recognize, submit
from kitchen.config import Settings
from kitchen.db.models import CardDraft
from kitchen.domain.cards import TEXT_LIMITS
from kitchen.llm.budget import LlmLimits
from kitchen.llm.label import LabelReader
from kitchen.sync.client import GspreadClient
from kitchen.sync.cycle import SyncCycle
from kitchen.sync.drive import DriveClient
from kitchen.sync.writer import CardSheetWriter
from kitchen.web.auth import CurrentUser, SessionDep, get_settings, require

# Типы зависимостей импортируются в рантайме, а не под TYPE_CHECKING: с
# `from __future__ import annotations` FastAPI разрешает их по именам модуля
# (см. kitchen.web.auth).

router = APIRouter(prefix="/api/cards", tags=["карточки"])

CardsUserDep = Annotated[CurrentUser, Depends(require("cook", "chef", "developer"))]
SettingsDep = Annotated[Settings, Depends(get_settings)]

PhotoKind = Literal["label", "package", "before", "after"]


def get_drive(request: Request) -> DriveClient:
    """Клиент Drive приложения. Зависимостью — чтобы тест подменил его фальшивкой."""
    drive: DriveClient = request.app.state.drive
    return drive


def get_label_reader(request: Request) -> LabelReader | None:
    """Чтец этикеток — один на процесс; ``None`` — ключа polza.ai нет."""
    reader: LabelReader | None = request.app.state.label_reader()
    return reader


def get_google(request: Request) -> Iterator[GspreadClient]:
    """Вход в Google-таблицы на время запроса: один на писателя и перенос,
    сессия закрывается после ответа."""
    google: GspreadClient = request.app.state.google()
    try:
        yield google
    finally:
        google.close()


GoogleDep = Annotated[GspreadClient, Depends(get_google)]


def get_card_writer(request: Request, google: GoogleDep) -> CardSheetWriter | None:
    """Писатель строки карточки; ``None`` — книга карточек не настроена."""
    writer: CardSheetWriter | None = request.app.state.card_writer(google)
    return writer


def get_sync_cycle(request: Request, google: GoogleDep) -> SyncCycle:
    """Перенос книги карточек в базу после отправки — с коротким ожиданием."""
    cycle: SyncCycle = request.app.state.sync_cycle(google)
    return cycle


def get_submit_window(settings: SettingsDep) -> timedelta:
    """Сколько отметка «отправка идёт» считается живой."""
    return submit.submit_window(settings)


DriveDep = Annotated[DriveClient, Depends(get_drive)]
LabelReaderDep = Annotated[LabelReader | None, Depends(get_label_reader)]
WriterDep = Annotated[CardSheetWriter | None, Depends(get_card_writer)]
CycleDep = Annotated[SyncCycle, Depends(get_sync_cycle)]
SubmitWindowDep = Annotated[timedelta, Depends(get_submit_window)]


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------
class DraftOut(BaseModel):
    """Черновик, каким его видит мастер."""

    id: uuid.UUID
    status: str
    step: str
    supplier: str
    category: str
    name: str
    label_name: str
    manufacturer: str
    composition: str
    protein: str | None
    fat: str | None
    carbs: str | None
    kcal: str | None
    shelf_life_sealed: str
    shelf_life_defrost: str
    shelf_life_after: str
    defrost_conditions: str
    description: str
    approval: str | None
    photos: dict[str, bool]
    """Какие из четырёх фото есть. Сами фото — ``GET …/photos/{вид}``."""
    recognition_status: str | None
    """Распознавание этикетки: ``running`` — идёт (опрашивайте черновик),
    ``done``, ``failed``; ``null`` — не запускали или этикетку заменили."""
    recognition_error: str | None
    """Почему не удалось последнее распознавание — текст для повара."""
    warnings: list[str]
    """Замечания повару: что заметило распознавание на этикетке и что не так
    с КБЖУ, — по числам, что стоят в черновике сейчас."""
    missing: list[str]
    """Чего не хватает для отправки — названиями для повара."""
    created_at: datetime
    updated_at: datetime


class DraftPatch(BaseModel):
    """Правка черновика: только переданные поля. Чужие ключи — 422: id фото
    приходит только из загрузки, статус — только от сервера."""

    model_config = ConfigDict(extra="forbid")

    step: str | None = None
    supplier: str | None = None
    category: str | None = None
    name: str | None = None
    label_name: str | None = None
    manufacturer: str | None = None
    composition: str | None = None
    protein: str | None = None
    fat: str | None = None
    carbs: str | None = None
    kcal: str | None = None
    shelf_life_sealed: str | None = None
    shelf_life_defrost: str | None = None
    shelf_life_after: str | None = None
    defrost_conditions: str | None = None
    description: str | None = None
    approval: str | None = None


class OptionsOut(BaseModel):
    categories: list[str]
    suppliers: list[str]


class CardHit(BaseModel):
    name: str
    supplier: str


class CardMatchesOut(BaseModel):
    exact: list[CardHit]
    similar: list[CardHit]


class ReferenceMatchesOut(BaseModel):
    exact: list[str]
    similar: list[str]


class NameCheckOut(BaseModel):
    cards: CardMatchesOut
    """Карточки в листе: точное совпадение — дубль, такую строку не запишут."""
    hidden: CardMatchesOut
    """Карточки, убранные из листа."""
    reference: ReferenceMatchesOut
    """Имена из справочника — только имена, без цен."""


class ShiftedOut(BaseModel):
    """Прежняя попытка записи, раскладку которой не подтвердили."""

    row: int
    journal_id: int


class SubmitOut(BaseModel):
    """Карточка в листе: «Записано в строку N»."""

    row: int
    already_written: bool
    """Карточка легла раньше — повтором отправки или прерванной попыткой."""
    imported: bool
    """Карточка уже на сайте; ``false`` — появится при следующем обновлении."""
    name: str
    not_written: list[str]
    """Правки, не попавшие в лист, — названиями колонок листа."""
    shifted: ShiftedOut | None
    """Строка и запись журнала, которые повар показывает шефу."""
    notes: list[str]
    """Оговорки повару готовыми фразами — показать как есть."""


def _number(value: Decimal | None) -> str | None:
    """Число строкой, без хвостовых нулей и экспоненты: 12.500 → «12.5», 100 → «100»."""
    return None if value is None else format(value.normalize(), "f")


def _out(draft: CardDraft) -> DraftOut:
    return DraftOut(
        id=draft.id,
        status=draft.status,
        step=draft.step,
        supplier=draft.supplier,
        category=draft.category,
        name=draft.name,
        label_name=draft.label_name,
        manufacturer=draft.manufacturer,
        composition=draft.composition,
        protein=_number(draft.protein),
        fat=_number(draft.fat),
        carbs=_number(draft.carbs),
        kcal=_number(draft.kcal),
        shelf_life_sealed=draft.shelf_life_sealed,
        shelf_life_defrost=draft.shelf_life_defrost,
        shelf_life_after=draft.shelf_life_after,
        defrost_conditions=draft.defrost_conditions,
        description=draft.description,
        approval=draft.approval,
        photos=drafts.photos(draft),
        recognition_status=draft.recognition_status,
        recognition_error=recognize.recognition_error(draft),
        warnings=drafts.draft_warnings(draft),
        missing=list(drafts.missing(draft)),
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _matches(found: drafts.CardMatches) -> CardMatchesOut:
    return CardMatchesOut(
        exact=[CardHit(name=card.name, supplier=card.supplier) for card in found.exact],
        similar=[CardHit(name=card.name, supplier=card.supplier) for card in found.similar],
    )


# ---------------------------------------------------------------------------
# Подсказки
# ---------------------------------------------------------------------------
@router.get("/options", response_model=OptionsOut)
def options(session: SessionDep, user: CardsUserDep) -> OptionsOut:
    """Варианты выбора: категории (затравка бота и карточки, частые первыми)
    и поставщики карточек."""
    found = drafts.options(session)
    return OptionsOut(categories=found.categories, suppliers=found.suppliers)


@router.get("/name-check", response_model=NameCheckOut)
def name_check(
    session: SessionDep,
    user: CardsUserDep,
    name: Annotated[str, Query(max_length=TEXT_LIMITS["name"])] = "",
) -> NameCheckOut:
    """Есть ли уже такая карточка и как это называется в справочнике."""
    found = drafts.name_check(session, name)
    return NameCheckOut(
        cards=_matches(found.cards),
        hidden=_matches(found.hidden),
        reference=ReferenceMatchesOut(
            exact=list(found.reference.exact), similar=list(found.reference.similar)
        ),
    )


# ---------------------------------------------------------------------------
# Черновик
# ---------------------------------------------------------------------------
@router.get("/drafts/current", response_model=DraftOut | None)
def current_draft(session: SessionDep, user: CardsUserDep) -> DraftOut | None:
    """Незаконченная карточка повара; ``null`` — её нет."""
    draft = drafts.current_draft(session, user.id)
    return None if draft is None else _out(draft)


@router.post("/drafts", response_model=DraftOut, status_code=status.HTTP_201_CREATED)
def start_draft(session: SessionDep, user: CardsUserDep) -> DraftOut:
    return _out(drafts.start_draft(session, user.id))


@router.patch("/drafts/{draft_id}", response_model=DraftOut)
def update_draft(
    draft_id: uuid.UUID,
    body: DraftPatch,
    session: SessionDep,
    user: CardsUserDep,
    window: SubmitWindowDep,
) -> DraftOut:
    changes = body.model_dump(exclude_unset=True)
    return _out(drafts.update_draft(session, user.id, draft_id, changes, submit_window=window))


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_draft(
    draft_id: uuid.UUID,
    session: SessionDep,
    user: CardsUserDep,
    drive: DriveDep,
    window: SubmitWindowDep,
) -> Response:
    """«Начать заново»."""
    drafts.cancel_draft(session, drive, user.id, draft_id, submit_window=window)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/drafts/{draft_id}/submit", response_model=SubmitOut)
def submit_draft(
    draft_id: uuid.UUID,
    session: SessionDep,
    user: CardsUserDep,
    writer: WriterDep,
    cycle: CycleDep,
    window: SubmitWindowDep,
) -> SubmitOut:
    """«Отправить в таблицу»: одна запись в лист и перенос карточки в базу.

    Повтор (ответ потерялся в сети) — та же строка, без второй записи."""
    sent = submit.submit(session, writer, cycle, user.id, draft_id, window=window)
    return SubmitOut(
        row=sent.row,
        already_written=sent.already_written,
        imported=sent.imported,
        name=sent.name,
        not_written=list(sent.not_written),
        shifted=None
        if sent.shifted is None
        else ShiftedOut(row=sent.shifted.row, journal_id=sent.shifted.id),
        notes=list(sent.notes),
    )


# ---------------------------------------------------------------------------
# Распознавание этикетки
# ---------------------------------------------------------------------------
@router.post("/recognize/{draft_id}", response_model=DraftOut)
def recognize_label(
    draft_id: uuid.UUID,
    session: SessionDep,
    user: CardsUserDep,
    drive: DriveDep,
    reader: LabelReaderDep,
    settings: SettingsDep,
    window: SubmitWindowDep,
) -> DraftOut:
    """Распознать фото этикетки черновика: поля и замечания — в черновик.

    Идёт до трёх минут; телефон мог уснуть, а nginx — ответить 504 — результат
    ждёт в черновике (``recognition_status``): опрашивать черновик."""
    draft = recognize.recognize(
        session,
        drive,
        reader,
        LlmLimits.from_settings(settings),
        user.id,
        draft_id,
        submit_window=window,
    )
    return _out(draft)


# ---------------------------------------------------------------------------
# Фото
# ---------------------------------------------------------------------------
@router.put("/drafts/{draft_id}/photos/{kind}", response_model=DraftOut)
def put_photo(
    draft_id: uuid.UUID,
    kind: PhotoKind,
    photo: Annotated[UploadFile, File()],
    session: SessionDep,
    user: CardsUserDep,
    drive: DriveDep,
    window: SubmitWindowDep,
) -> DraftOut:
    """Фото в слот черновика (multipart, поле ``photo``, JPEG до 8 МБ).

    Новая этикетка сбрасывает распознавание прежней."""
    # На байт больше предела: этого хватает, чтобы узнать «слишком большое»,
    # не читая в память всё, что прислали.
    content = photo.file.read(drafts.PHOTO_LIMIT + 1)
    draft = drafts.put_photo(
        session,
        drive,
        user.id,
        draft_id,
        kind,
        content,
        now=datetime.now(UTC),
        submit_window=window,
    )
    return _out(draft)


@router.delete("/drafts/{draft_id}/photos/{kind}", response_model=DraftOut)
def remove_photo(
    draft_id: uuid.UUID,
    kind: PhotoKind,
    session: SessionDep,
    user: CardsUserDep,
    drive: DriveDep,
    window: SubmitWindowDep,
) -> DraftOut:
    return _out(drafts.remove_photo(session, drive, user.id, draft_id, kind, submit_window=window))


@router.get("/drafts/{draft_id}/photos/{kind}")
def photo(
    draft_id: uuid.UUID,
    kind: PhotoKind,
    session: SessionDep,
    user: CardsUserDep,
    drive: DriveDep,
) -> Response:
    """Фото черновика — через наш прокси, только владельцу.

    Папка в Drive закрыта, ссылку на файл повар открыть не сможет; да и
    отдавать её незачем. ``private`` — не класть в общие кэши, ``no-cache`` —
    после замены фото по тому же адресу не показывать старое; ``nosniff`` —
    браузер не угадывает тип, байты — только картинка.
    """
    content = drafts.photo_bytes(session, drive, user.id, draft_id, kind)
    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"},
    )


# ---------------------------------------------------------------------------
# Отказы
# ---------------------------------------------------------------------------
ERROR_STATUS: dict[type[drafts.CardsError], int] = {
    drafts.DraftNotFoundError: status.HTTP_404_NOT_FOUND,
    drafts.PhotoMissingError: status.HTTP_404_NOT_FOUND,
    drafts.DraftExistsError: status.HTTP_409_CONFLICT,
    drafts.PhotoNeedsNamesError: status.HTTP_409_CONFLICT,
    drafts.DraftConflictError: status.HTTP_409_CONFLICT,
    submit.DuplicateCardError: status.HTTP_409_CONFLICT,
    drafts.PhotoTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    drafts.NotJpegError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    drafts.DraftFieldError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    submit.MissingFieldsError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    recognize.RecognitionLimitError: status.HTTP_429_TOO_MANY_REQUESTS,
    drafts.StorageError: status.HTTP_502_BAD_GATEWAY,
    recognize.RecognitionFailedError: status.HTTP_502_BAD_GATEWAY,
    submit.SubmitFailedError: status.HTTP_502_BAD_GATEWAY,
    drafts.StorageNotConfiguredError: status.HTTP_503_SERVICE_UNAVAILABLE,
    recognize.RecognitionNotConfiguredError: status.HTTP_503_SERVICE_UNAVAILABLE,
    submit.SubmitUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
}
"""Отказ слоя приложения → код ответа. Новый отказ без кода — тест краснеет."""


def status_for(error_type: type[drafts.CardsError]) -> int | None:
    """Код ответа отказа — по цепочке наследования: наследник отказа с кодом
    отвечает тем же кодом. Сам ``CardsError`` кода не имеет."""
    for cls in error_type.__mro__:
        if cls in ERROR_STATUS:
            return ERROR_STATUS[cls]
    return None


async def cards_error(request: Request, error: Exception) -> JSONResponse:
    """Отказ — текстом для повара в ``detail``, как во всём API, и тем, что
    ещё нужно экрану: у отказа правки — ``field`` (какое поле подсветить), у
    дубля — ``row``, у неполного черновика — ``missing``."""
    if not isinstance(error, drafts.CardsError):  # pragma: no cover — регистрируется на CardsError
        raise error
    body: dict[str, object] = {"detail": str(error), **error.extra()}
    code = status_for(type(error))
    return JSONResponse(body, status_code=code or status.HTTP_500_INTERNAL_SERVER_ERROR)
