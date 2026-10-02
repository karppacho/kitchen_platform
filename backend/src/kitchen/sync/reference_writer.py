"""Писатель строки справочника: ручные ячейки строки ING согласованной карточки.

Вторая запись платформы в Google-таблицу (ADR-0003, вторая ступень) и первая
— в книгу кухни. Перенос карточки в справочник — не новая строка: формула
``QUERY`` листа ING уже вывела для карточки «Да» строку с категорией,
названием, изготовителем и КБЖУ. Писатель дописывает в неё то, что раньше
дописывали руками шеф или коммерция: id, короткое имя, цены, единицу, вес
штуки, потери и статус. По этой строке калькулятор считает себестоимость, и
ошибка здесь тихая: id или цена в чужой строке дают правдоподобные чужие
числа. Поэтому всё неясное — отказ с понятным текстом, а не догадка.

Порядок :meth:`ReferenceRowFiller.fill` — механика писателя карточек
(:mod:`kitchen.sync.writer`):

1. **Ворота и значения** — до первого запроса к Google: право записи всех
   десяти ручных полей (ворота «книга → лист → колонки» строк не знают —
   строки держит этот писатель); числа — JSON-числами без потери точности,
   пустое — null.
2. **Очередь писателей** — та же, что у писателя карточек. Ждать дольше
   30 с — «Таблица занята».
3. **Журнал по ключу запроса** ``ing-fill:<id карточки>``. Состоявшаяся
   запись — ответ без единого запроса к Google; незавершённая — перечитать
   её строку: легла — ответ ею (и ничего в ней не трогать), не легла — писать
   заново.
4. **Свежее чтение**: лист ING дважды — FORMATTED (как видит шеф) и FORMULA
   (где формулы; id — числами), — и книга карточек (порядок «Да»). Шапки —
   той же проверкой, что у импорта; якорь ``QUERY`` — один и без ошибки.
5. **Строка** — по названию и по месту среди «Да» сразу
   (:func:`~kitchen.domain.reference_row.locate_row`). A пуст и в FORMATTED,
   и в FORMULA. Ни одна ручная ячейка, кроме L, не формула; L-формула
   пропускается. Ячейки потерь оформлены процентами.
6. **id** — наибольший числовой id листа плюс один, под очередью.
7. **Журнал** ``pending`` со снимком строки и листа — своим коммитом, до
   записи.
8. **Очередь ещё наша?** Её могла оборвать база — тогда отказ до записи.
9. **Одна запись**: ``values.batchUpdate``, RAW, только ручные ячейки этой
   строки. B–D, F–K (вывод ``QUERY``) и P (формула шефа) в теле не бывают
   никогда.
10. **Перечитать и сверить** — и когда запись упала. Раскладка: якорь
    ``QUERY`` на месте, в C — название карточки, в A — наш id, ручные ячейки
    соседей сверху и снизу — как при чтении (вывод ``QUERY`` при вставке
    строки внутри зоны не сдвигается, а ручные ячейки сдвигаются); значения
    — точно, по FORMULA-чтению. Итог — в журнал: ``verified``;
    ``rolled_back`` — строку правили одновременно с нами: наши ячейки
    возвращены к тому, что в них было до записи, чужие не тронуты;
    ``failed`` — не легла или раскладку не подтвердили: ничего не трогаем,
    полный снимок листа (значения и формулы) в журнале, ERROR в лог.

Возврат, а не очистка: в строке-заготовке до нас стояли умолчания шефа (цены
0, «кг», потери 0% в процентном оформлении). Очистка стёрла бы их, а пустые
ячейки потерь заблокировали бы следующий перенос проверкой оформления.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, NoReturn

from kitchen.db.journal import (
    FAILED,
    FILL,
    LAYOUT_UNCONFIRMED,
    ROLLED_BACK,
    VERIFIED,
    NewWrite,
    UnconfirmedWrite,
    WritersBusyError,
)
from kitchen.domain.cards import APPROVED
from kitchen.domain.matching import normalise_name
from kitchen.domain.reference_row import (
    ANCHOR_BROKEN,
    FORM_TITLES,
    LOSS_FIELDS,
    AlreadyFilled,
    NotFound,
    ReferenceFormError,
    ReferenceLayoutError,
    find_query_anchor,
    formula_cells,
    is_query_formula,
    locate_row,
    next_reference_id,
)
from kitchen.sync import specs
from kitchen.sync.reader import check_header, describe_error, row_hash
from kitchen.sync.sheet_write import (
    FORMATTED,
    FORMULA,
    LOCK_TIMEOUT,
    SHEET_WRITE_LOCK_KEY,
    SentValue,
    SheetBusyError,
    SheetUnavailableError,
    WriteNotConfirmedError,
    WriteRefusedError,
    hold_limit_for,
    joined,
    quoted,
    read_ranges,
    same_cell,
    sheet_number,
    trouble,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from datetime import timedelta

    from kitchen.config import Settings
    from kitchen.db.journal import OpenWrite
    from kitchen.domain.reference_row import ReferenceForm
    from kitchen.sync.client import SheetsClient, Spreadsheet
    from kitchen.sync.ownership import Column
    from kitchen.sync.sheet_write import SheetJournal, WritersLock

log = logging.getLogger(__name__)

SPEC = specs.INGREDIENTS
"""Единственный лист, в который пишем: ING книги кухни."""
CARDS = specs.INGREDIENT_CARDS
"""Откуда порядок «Да»: «Лист1» книги карточек. Сюда этот писатель не пишет."""

ACTION = FILL
"""Действие в журнале ``sheet_writes``."""

FIELDS: tuple[str, ...] = (
    "id",
    "short_name",
    "price_per_kg",
    "price_per_pack",
    "unit",
    "weight_per_piece_g",
    "losses_unpacking",
    "losses_cutting",
    "losses_thermal",
    "status",
)
"""Ручные поля строки ING — A, E, L, M, N, O, Q, R, S, T, в порядке колонок.
id выдаёт писатель, остальное — :meth:`ReferenceForm.row_values`."""

_WRITTEN: tuple[Column, ...] = tuple(
    sorted((SPEC.column(f) for f in FIELDS), key=lambda c: c.index)
)
_TEXT_FIELDS = frozenset({"short_name", "unit", "status"})
"""Текстом: короткое имя, единица, статус. Остальное — числами JSON."""
_ID = SPEC.column("id")
_ANCHOR = SPEC.column("category")
"""B: в строке якоря — формула ``QUERY``."""
_NAME = SPEC.column("name")
_PRICE = SPEC.column("price_per_kg")
"""L: в части строк — формула от M, тогда пропускается."""
_LOSSES: tuple[Column, ...] = tuple(SPEC.column(f) for f in LOSS_FIELDS)
_WIDTH = len(SPEC.columns)
"""A–T: столько колонок сверяется и хешируется, как у импорта."""
_LAST = SPEC.columns[-1].letter
_TITLE = quoted(SPEC.title)
_CARD_NAME = CARDS.column("name")
_APPROVAL = CARDS.column("approval_status")

_WORST_REQUESTS = 14
"""Запросов к Google в худшем заполнении — с запасом: открыть две книги,
перечитать прерванную попытку (два чтения), прочитать лист дважды и книгу
карточек, записать, перечитать (два), вернуть свои ячейки, перечитать ещё
раз (два) — около тринадцати."""


def hold_limit(settings: Settings) -> timedelta:
    """Сколько одно заполнение может держать очередь писателей:
    :data:`_WORST_REQUESTS` запросов по «подключение + ответ» секунд из
    настроек и минута сверху. При (10, 60) — 17 мин 20 с."""
    return hold_limit_for(_WORST_REQUESTS, settings)


HOLD_LIMIT = hold_limit_for(_WORST_REQUESTS)
""":func:`hold_limit` для таймаутов Google по умолчанию (10 и 60 с)."""

REQUEST_KEY_PREFIX = "ing-fill:"


def fill_request_key(card_id: int) -> str:
    """Ключ запроса переноса — один на карточку: повторное нажатие, обрыв
    связи, второй человек на той же карточке второго id не дадут."""
    return f"{REQUEST_KEY_PREFIX}{card_id}"


# ---------------------------------------------------------------------------
# Тексты для человека — словами спеки («Ответы человеку»)
# ---------------------------------------------------------------------------
HEADER_DRIFT = "Колонки справочника изменились — сообщите разработчику"
CARDS_HEADER_DRIFT = "Колонки таблицы карточек изменились — сообщите разработчику"
BUSY = "Таблица занята — попробуйте ещё раз"
LOCK_LOST = (
    "Связь с базой платформы прервалась до записи — в справочнике ничего не изменилось, "
    "нажмите ещё раз"
)
NOT_CONFIRMED = "Не удалось подтвердить запись — нажмите ещё раз: второй записи не будет"
NOT_APPLIED = "Google не принял запись — в справочнике ничего не изменилось, нажмите ещё раз"
_NOTHING_CHANGED = "{reason} — в справочнике ничего не изменилось, нажмите ещё раз"

REASON_FORMULA = "formula"
"""Причина отказа: в ручной ячейке строки (кроме L) — формула."""
REASON_PERCENT = "percent"
"""Причина отказа: ячейки потерь оформлены не процентами."""
REASON_LOSSES_EMPTY = "losses_empty"
"""Причина отказа: ячейки потерь пусты — оформление не проверить."""


def _formula_text(row: int, letters: Sequence[str]) -> str:
    if len(letters) == 1:
        where, what = f"в ручной ячейке {letters[0]} — формула", "её"
    else:
        where, what = f"в ручных ячейках {', '.join(letters)} — формулы", "их"
    return (
        f"В строке {row} листа ING {where}: платформа {what} не перезаписывает. Запись не "
        "сделана, проверьте лист ING"
    )


def _percent_text(row: int) -> str:
    return (
        f"Ячейки потерь в строке {row} оформлены не процентами — запись не сделана, "
        "проверьте лист ING"
    )


def _losses_empty_text(row: int) -> str:
    return (
        f"Ячейки потерь в строке {row} пусты — оформление не проверить. Запись не сделана: "
        "впишите в них 0 % в листе ING"
    )


def _rolled_back_text(row: int) -> str:
    return (
        f"В строку {row} листа ING одновременно с нами вписали своё — наши ячейки вернули как "
        "было, чужие не тронуты. Нажмите ещё раз"
    )


def _unconfirmed_text(row: int, journal_id: int) -> str:
    return (
        f"Лист ING меняли в ту же секунду — запись не подтверждена. Покажите шефу строку {row} "
        f"листа ING (запись журнала №{journal_id})"
    )


def _resumed_unconfirmed_text(row: int, journal_id: int) -> str:
    return (
        "Лист ING меняли, пока прежняя попытка записи оставалась незавершённой, — запись не "
        f"подтверждена. Покажите шефу строку {row} листа ING (запись журнала №{journal_id})"
    )


# ---------------------------------------------------------------------------
# Ошибки и итог
# ---------------------------------------------------------------------------
class RowRefusedError(WriteRefusedError):
    """Строку карточки заполнить нельзя — запись не начиналась, в листе ничего
    не изменилось (409: лист сейчас не такой, чтобы в него писать).

    ``reason`` — почему: значение :class:`~kitchen.domain.reference_row.NotFound`
    («not_approved», «not_yet», «shifted», «ambiguous»), :data:`REASON_FORMULA`
    или :data:`REASON_PERCENT`; ``row`` — строка, если её нашли.
    """

    def __init__(self, message: str, *, reason: str, row: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.row = row


class SheetLayoutError(WriteRefusedError):
    """Лист устроен не так, как ждали: шапка ING или книги карточек, якорь
    ``QUERY``. Писать по позиции нельзя, нужен разработчик (503). ``issues``
    — расхождения шапки."""

    def __init__(self, message: str, issues: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.issues = tuple(issues)


@dataclass(frozen=True, slots=True)
class FillResult:
    """Ингредиент в справочнике: строка ING, его id и оговорки."""

    row: int
    ref_id: str
    """id, как его видит человек: «131»."""
    journal_id: int | None
    """Запись журнала. ``None`` — id в строке уже стоял: писать не пришлось."""
    already: bool
    """Ингредиент был в справочнике и до этого вызова: id в строке уже стоял,
    или это повтор состоявшегося переноса."""
    shifted: UnconfirmedWrite | None = None
    """Прежняя попытка этого переноса, раскладку которой не подтвердили: лист
    меняли в окне записи, и чужая строка могла пострадать. Строку и номер
    журнала показывают шефу и при удачном ответе."""

    @property
    def message(self) -> str:
        """Ответ человеку — словами спеки."""
        if self.already:
            return f"Ингредиент уже в справочнике — id {self.ref_id}"
        return f"Записано в справочник: строка {self.row}, id {self.ref_id}"


# ---------------------------------------------------------------------------
# Значения
# ---------------------------------------------------------------------------
def _planned(form: ReferenceForm) -> dict[str, SentValue]:
    """Поля формы для тела записи (без id) — до первого запроса к Google.

    Право записи — первым и на все десять полей: закрытые ворота отказывают
    по правилу владения, а не по виду значения. Короткое имя, единица и
    статус — текстом; цены, вес и потери (доли) — JSON-числами через
    :func:`~kitchen.sync.sheet_write.sheet_number`: строка «0.05» при RAW
    легла бы текстом, и формула P её не сложила бы. Пустое — ``None``
    (null): ячейку не трогать. Число, которое через ``float`` стало бы
    другим, — ошибка поля формы.
    """
    SPEC.check_writable(*FIELDS)
    sent: dict[str, SentValue] = {}
    errors: dict[str, str] = {}
    for field, value in form.row_values().items():
        if field in _TEXT_FIELDS:
            if not isinstance(value, str):
                raise ValueError(f"Поле {field} — текст, а пришло {value!r}")
            sent[field] = value or None
        elif value is None:
            sent[field] = None
        elif isinstance(value, Decimal):
            try:
                sent[field] = sheet_number(value)
            except WriteRefusedError:
                shown = value * 100 if field in LOSS_FIELDS else value
                unit = " %" if field in LOSS_FIELDS else ""
                errors[field] = (
                    f"{FORM_TITLES[field]}: число {format(shown, 'f').replace('.', ',')}{unit} "
                    "не записать в таблицу точно — проверьте его"
                )
        else:
            raise ValueError(f"Поле {field} — число Decimal или пусто, а пришло {value!r}")
    if errors:
        raise ReferenceFormError(errors)
    return sent


def _body(sent: Mapping[str, SentValue], row: int) -> dict[str, object]:
    """Тело ``values.batchUpdate``: RAW, по диапазону на каждую сплошную
    группу ручных колонок этой строки — A; E; L–O (M–O, если L — формула);
    Q–T. Каждый диапазон ограничен с обеих сторон: значений больше, чем
    ячеек, Google не примет. Пустое — null: ячейка не трогается."""
    runs: list[list[Column]] = []
    for column in (c for c in _WRITTEN if c.field in sent):
        if runs and runs[-1][-1].index + 1 == column.index:
            runs[-1].append(column)
        else:
            runs.append([column])
    data = [
        {
            "range": f"{_TITLE}!{run[0].letter}{row}:{run[-1].letter}{row}",
            "values": [[sent[c.field] for c in run]],
        }
        for run in runs
    ]
    return {"valueInputOption": "RAW", "data": data}


# ---------------------------------------------------------------------------
# Чтение листа
# ---------------------------------------------------------------------------
def _cell(line: Sequence[object], index: int) -> object:
    return line[index] if index < len(line) else ""


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _row(rows: Sequence[Sequence[object]], number: int) -> list[object]:
    """Строка листа по номеру с единицы шириной не меньше A–T; за концом
    чтения — пустая."""
    line = list(rows[number - 1]) if 0 < number <= len(rows) else []
    return line + [""] * (_WIDTH - len(line))


def _text_row(rows: Sequence[Sequence[object]], number: int) -> list[str]:
    return [str(cell) for cell in _row(rows, number)]


def _first(block: Sequence[Sequence[object]]) -> object:
    return _cell(block[0], 0) if block else ""


@dataclass(frozen=True, slots=True)
class _Sheets:
    """Свежее чтение под очередью: лист ING двумя чтениями и порядок «Да»."""

    formatted: list[list[str]]
    formula: list[list[object]]
    anchor: int
    approved: list[str]


def _read(kitchen: Spreadsheet, cards: Spreadsheet) -> _Sheets:
    """Лист ING (FORMATTED и FORMULA) и книга карточек — с проверкой шапок и
    якоря. Расхождение — :class:`SheetLayoutError`, до записи."""
    formatted = [
        [str(cell) for cell in line]
        for line in _google(
            SPEC.title, "прочитать лист", lambda: read_ranges(kitchen, [_TITLE], FORMATTED)[0]
        )
    ]
    issues = check_header(SPEC, formatted)
    if issues:
        log.warning("«%s»: заполнение остановлено, шапка — %s", SPEC.title, "; ".join(issues))
        raise SheetLayoutError(HEADER_DRIFT, issues)
    formula = _google(
        SPEC.title, "прочитать формулы листа", lambda: read_ranges(kitchen, [_TITLE], FORMULA)[0]
    )
    try:
        anchor = find_query_anchor(formula)
    except ReferenceLayoutError as error:
        log.warning("«%s»: заполнение остановлено — %s", SPEC.title, error)
        raise SheetLayoutError(str(error)) from error
    shown = str(_cell(_row(formatted, anchor), _ANCHOR.index)).strip()
    if shown.startswith("#"):
        # «#REF!», «#N/A»: IMPORTRANGE потерял доступ или формула сломана —
        # вывода нет, и «строка ещё не появилась» было бы враньём навсегда.
        log.warning("«%s»: формула QUERY в строке %s показывает «%s»", SPEC.title, anchor, shown)
        raise SheetLayoutError(ANCHOR_BROKEN)
    raw_cards = [
        [str(cell) for cell in line]
        for line in _google(
            CARDS.title,
            "прочитать книгу карточек",
            lambda: read_ranges(cards, [quoted(CARDS.title)], FORMATTED)[0],
        )
    ]
    issues = check_header(CARDS, raw_cards)
    if issues:
        log.warning("«%s»: заполнение ING остановлено, шапка — %s", CARDS.title, "; ".join(issues))
        raise SheetLayoutError(CARDS_HEADER_DRIFT, issues)
    # Порядок «Да» — как у QUERY: строки данных по порядку, «Да» в V точно,
    # пустые названия тоже в счёт — формула выводит и их.
    approved = [
        str(_cell(line, _CARD_NAME.index))
        for line in raw_cards[CARDS.header_rows :]
        if _cell(line, _APPROVAL.index) == APPROVED
    ]
    return _Sheets(formatted, formula, anchor, approved)


def _row_values(sheet: _Sheets, row: int, planned: Mapping[str, SentValue]) -> dict[str, SentValue]:
    """Что пишем в найденную строку — после проверок её ячеек.

    * Формула в ручной ячейке, кроме L, — отказ (в A тоже: формула, которая
      показывает пусто, — не место для id); L-формула пропускается.
    * A пуст и в FORMULA-чтении: id под оформлением «;;;» в FORMATTED
      выглядит пустым — это не строка, ждущая переноса, а сдвиг.
    * Ячейки потерь показывают «%»: доля в ячейке без процентного
      оформления — «0,05» вместо «5%». Пустая ячейка не показывает ничего,
      и оформление по ней не проверить — отказ со своим текстом.
    * id — наибольший числовой id листа (FORMULA-чтение: числа числами)
      плюс один, JSON-целым.
    """
    raw = _row(sheet.formula, row)
    shown = _text_row(sheet.formatted, row)
    formulas = formula_cells(raw)
    written = {c.letter for c in _WRITTEN}
    blocked = [letter for letter in formulas if letter in written and letter != _PRICE.letter]
    if blocked:
        raise RowRefusedError(_formula_text(row, blocked), reason=REASON_FORMULA, row=row)
    if not _blank(raw[_ID.index]):
        raise RowRefusedError(NotFound.SHIFTED.message, reason=NotFound.SHIFTED.value, row=row)
    if any(_blank(shown[c.index]) for c in _LOSSES):
        raise RowRefusedError(_losses_empty_text(row), reason=REASON_LOSSES_EMPTY, row=row)
    if any("%" not in shown[c.index] for c in _LOSSES):
        raise RowRefusedError(_percent_text(row), reason=REASON_PERCENT, row=row)
    ref_id = next_reference_id(_cell(line, _ID.index) for line in sheet.formula[SPEC.header_rows :])
    values: dict[str, SentValue] = {_ID.field: sheet_number(Decimal(ref_id))}
    for field in FIELDS[1:]:
        if field == _PRICE.field and _PRICE.letter in formulas:
            continue
        values[field] = planned[field]
    return values


# ---------------------------------------------------------------------------
# Попытка и перечитывание
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Attempt:
    """Запись в строку ``row``, заведённая в журнале."""

    row: int
    journal_id: int
    anchor: int
    name: str
    """Название карточки — им сверяется C после записи."""
    before: Mapping[str, object]
    """Снимок «до»: строка как видит шеф (``rows``); она и соседи сверху и
    снизу формулами (``formula``); якорь, название и, пока запись не
    подтверждена, весь лист значениями и формулами (``sheet``,
    ``sheet_formula``)."""
    sent: Mapping[str, SentValue]

    @classmethod
    def of(cls, prior: OpenWrite) -> _Attempt:
        """Прерванная попытка — из журнала."""
        anchor, name = prior.before.get("anchor"), prior.before.get("name")
        if isinstance(anchor, bool) or not isinstance(anchor, int) or not isinstance(name, str):
            raise ValueError(f"в журнале №{prior.id} нет якоря или названия карточки")
        return cls(prior.row, prior.id, anchor, name, prior.before, _sent_from(prior))

    @property
    def ref_id(self) -> str:
        return _ref_id(self.sent, self.journal_id)

    def line(self) -> list[str]:
        """Строка до записи, как её видел шеф, A–T."""
        return [str(cell) for cell in self._snapshot("rows", self.row)[:_WIDTH]]

    def raw_line(self, number: int | None = None) -> list[object]:
        """Строка ``number`` (по умолчанию — наша) до записи формулами, A–T:
        числа — числами. Есть наша и соседи сверху и снизу."""
        return self._snapshot("formula", self.row if number is None else number)[:_WIDTH]

    def rows_only(self) -> dict[str, object]:
        """Снимок без листа целиком — у записи, раскладка которой подтверждена."""
        return {key: value for key, value in self.before.items() if key not in _WHOLE_SHEET}

    def _snapshot(self, key: str, number: int) -> list[object]:
        rows = self.before.get(key)
        line = rows.get(str(number)) if isinstance(rows, dict) else None
        if not isinstance(line, list):
            raise ValueError(f"в журнале №{self.journal_id} нет снимка строки {number}")
        return [*line, *[""] * (_WIDTH - len(line))]


_WHOLE_SHEET = ("sheet", "sheet_formula")
"""Полный снимок листа в журнале — значениями и формулами. Нужен, пока
раскладку не подтвердили: по нему возвращают затёртое, в том числе формулы."""


@dataclass(frozen=True, slots=True)
class _Reread:
    """Что увидели в листе после записи."""

    line: list[str]
    """Строка, как её видит шеф, A–T."""
    values: list[object]
    """Строка формулами, A–T: числа — числами, формула — текстом."""
    anchor_cell: tuple[str, object]
    """Ячейка якоря: как видит шеф и формулой."""
    neighbours: dict[str, list[object]]
    """Соседи сверху и снизу формулами, A–T — по номеру строки."""
    moved: tuple[str, ...]
    """Соседи, чьи ручные ячейки не те, что при чтении, — номера строк."""
    place_holds: bool
    """Якорь ``QUERY`` на месте и без ошибки, в C — название карточки, ручные
    ячейки соседей — как при чтении."""
    layout_confirmed: bool
    """Место то же, и в A — наш id."""
    untouched: bool
    """Место то же, а строка — как при чтении листа."""

    def after(self, attempt: _Attempt) -> dict[str, object]:
        return {
            "rows": {str(attempt.row): self.line},
            "values": self.values,
            "neighbours": self.neighbours,
            "anchor": {str(attempt.anchor): list(self.anchor_cell)},
        }


def _look(book: Spreadsheet, attempt: _Attempt) -> _Reread:
    """Перечитать якорь и строки N−1, N, N+1 — FORMATTED и FORMULA, по
    запросу на чтение.

    Место строки держат три вещи:

    * якорь ``QUERY`` — на месте и без ошибки;
    * в C — название карточки, и это вывод ``QUERY`` (в FORMULA пусто);
    * ручные ячейки соседей сверху и снизу — те же, что при чтении. Вывод
      ``QUERY`` привязан к якорю и при вставке или удалении строки внутри
      зоны остаётся на своих номерах строк, а ручные ячейки сдвигаются. Тогда
      в нашей строке прежнее название, но ручные ячейки соседа — и запись
      легла поверх них. Сдвиг виден по соседям: id в листе уникальны.

    Значения сверяются по FORMULA-чтению: у ячейки без формулы это то же
    значение без оформления, что и UNFORMATTED (числа числами), а формулу,
    вписанную в нашу ячейку в окне записи, оно показывает формулой — чужой.
    """
    row = attempt.row
    ranges = [f"{_TITLE}!{_ANCHOR.letter}{attempt.anchor}", f"{_TITLE}!A{row - 1}:{_LAST}{row + 1}"]
    shown_anchor, shown_rows = read_ranges(book, ranges, FORMATTED)
    raw_anchor, raw_rows = read_ranges(book, ranges, FORMULA)
    anchor_shown, anchor_formula = str(_first(shown_anchor)), _first(raw_anchor)
    line = _text_row(shown_rows, 2)[:_WIDTH]
    values = _row(raw_rows, 2)[:_WIDTH]
    neighbours = {
        str(row - 1): _row(raw_rows, 1)[:_WIDTH],
        str(row + 1): _row(raw_rows, 3)[:_WIDTH],
    }
    moved = tuple(
        number
        for number, now in neighbours.items()
        if _manual_changed(attempt.raw_line(int(number)), now)
    )
    anchor_holds = is_query_formula(anchor_formula) and not anchor_shown.strip().startswith("#")
    # Название — вывод QUERY: видно в FORMATTED и пусто в FORMULA, как при поиске строки.
    same_name = normalise_name(line[_NAME.index]) == normalise_name(attempt.name)
    place = anchor_holds and same_name and _blank(values[_NAME.index]) and not moved
    return _Reread(
        line=line,
        values=values,
        anchor_cell=(anchor_shown, anchor_formula),
        neighbours=neighbours,
        moved=moved,
        place_holds=place,
        layout_confirmed=place and same_cell(attempt.sent[_ID.field], values[_ID.index]),
        untouched=place and line == attempt.line(),
    )


def _manual_changed(before: Sequence[object], now: Sequence[object]) -> bool:
    """Изменилась ли хоть одна ручная ячейка строки (A, E, L–O, Q–T)."""
    return any(not same_cell(_as_sent(before[c.index]), now[c.index]) for c in _WRITTEN)


# ---------------------------------------------------------------------------
# Писатель
# ---------------------------------------------------------------------------
class ReferenceRowFiller:
    """Заполняет ручные ячейки строки ING согласованной карточки. Второй путь
    записи платформы (ADR-0003, вторая ступень): та же очередь писателей, что
    у :class:`~kitchen.sync.writer.CardSheetWriter`, сверка и журнал — у
    каждой записи."""

    def __init__(
        self,
        client: SheetsClient,
        journal: SheetJournal,
        *,
        kitchen_id: str,
        cards_id: str,
        lock_timeout: timedelta = LOCK_TIMEOUT,
        hold: timedelta = HOLD_LIMIT,
    ) -> None:
        """``kitchen_id`` и ``cards_id`` — идентификаторы книги кухни и книги
        карточек; ``hold`` — :func:`hold_limit` от настроек таймаутов Google."""
        self._client = client
        self._journal = journal
        self._kitchen_id = kitchen_id
        self._cards_id = cards_id
        self._lock_timeout = lock_timeout
        self._hold = hold

    def fill(
        self,
        card_name: str,
        form: ReferenceForm,
        *,
        actor_id: uuid.UUID | None,
        request_key: str,
    ) -> FillResult:
        """Заполнить строку ING карточки ``card_name`` значениями ``form``.

        ``card_name`` — название карточки, как в колонке B книги карточек;
        сравнивается как ключ карточки при импорте. ``request_key`` —
        :func:`fill_request_key` от id карточки. Ошибки —
        :class:`~kitchen.domain.reference_row.ReferenceFormError` (поле формы)
        и наследники :class:`WriteRefusedError` с текстом для человека.
        """
        planned = _planned(form)
        if not normalise_name(card_name):
            raise ValueError("Пустое название карточки — строку справочника не найти")
        try:
            with self._journal.writers_lock(
                SHEET_WRITE_LOCK_KEY, self._lock_timeout, self._hold
            ) as lock:
                result = self._fill(
                    lock, card_name, planned, actor_id=actor_id, request_key=request_key
                )
        except WritersBusyError as error:
            raise SheetBusyError(BUSY) from error
        return _announced(result)

    # --- по шагам -----------------------------------------------------------
    def _fill(
        self,
        lock: WritersLock,
        card_name: str,
        planned: Mapping[str, SentValue],
        *,
        actor_id: uuid.UUID | None,
        request_key: str,
    ) -> FillResult:
        attempts = self._journal.unconfirmed_attempts(request_key)
        shifted = UnconfirmedWrite(attempts[0].id, attempts[0].row) if attempts else None
        prior = self._journal.find_open(request_key)
        if prior is not None and prior.status == VERIFIED:
            # Уже записано: ответ по журналу, без единого запроса к Google.
            return FillResult(prior.row, _ref_id(prior.values, prior.id), prior.id, True, shifted)

        kitchen = _google(
            SPEC.title, "открыть книгу кухни", lambda: self._client.open(self._kitchen_id)
        )
        if prior is not None:
            resumed = self._resume(kitchen, prior, shifted)
            if resumed is not None:
                return resumed

        cards = _google(
            CARDS.title, "открыть книгу карточек", lambda: self._client.open(self._cards_id)
        )
        sheet = _read(kitchen, cards)
        found = locate_row(sheet.formatted, sheet.formula, sheet.anchor, sheet.approved, card_name)
        if isinstance(found, NotFound):
            log.info("«%s»: «%s» не заполнена — %s", SPEC.title, card_name, found.value)
            raise RowRefusedError(found.message, reason=found.value)
        if isinstance(found, AlreadyFilled):
            log.info(
                "«%s»: «%s» уже в строке %s, id %s", SPEC.title, card_name, found.row, found.ref_id
            )
            return FillResult(found.row, found.ref_id, None, True, shifted)

        row = found.row
        sent = _row_values(sheet, row, planned)
        before: dict[str, object] = {
            "rows": {str(row): _text_row(sheet.formatted, row)},
            "formula": {str(n): _row(sheet.formula, n) for n in (row - 1, row, row + 1)},
            "anchor": sheet.anchor,
            "name": card_name,
            "sheet": sheet.formatted,
            "sheet_formula": sheet.formula,
        }
        journal_id = self._journal.start(
            NewWrite(
                book=SPEC.spreadsheet,
                sheet=SPEC.title,
                row=row,
                request_key=request_key,
                actor_id=actor_id,
                before=before,
                values=dict(sent),
                action=ACTION,
            )
        )
        attempt = _Attempt(row, journal_id, sheet.anchor, card_name, before, sent)
        if not lock.alive():
            self._journal.finish(
                journal_id,
                status=FAILED,
                error="очередь писателей потеряна до записи: транзакцию блокировки оборвала "
                "база — в лист ничего не ушло",
                before=attempt.rows_only(),
            )
            log.error(
                "«%s»: очередь писателей потеряна до записи в строку %s — отказ, журнал №%s",
                SPEC.title,
                row,
                journal_id,
            )
            raise SheetBusyError(LOCK_LOST)
        failure: Exception | None = None
        try:
            kitchen.values_batch_update(_body(sent, row))
        except Exception as error:
            # Исход неясен: Google мог записать и не ответить. Решает
            # перечитывание, а не повтор записи.
            failure = error
        return self._settle(kitchen, attempt, failure, shifted)

    def _reread(self, book: Spreadsheet, attempt: _Attempt, cause: str) -> _Reread:
        """Перечитать якорь и строку. Не вышло — исход неизвестен: журнал
        остаётся ``pending``, повтор с тем же ключом перечитает снова."""
        try:
            return _look(book, attempt)
        except Exception as error:
            reason = f"{cause}; перечитать строку не удалось: {describe_error(error)}"
            self._journal.annotate(attempt.journal_id, error=reason)
            log.error(
                "«%s»: исход записи в строку %s неизвестен, журнал №%s остаётся pending — %s",
                SPEC.title,
                attempt.row,
                attempt.journal_id,
                reason,
            )
            raise WriteNotConfirmedError(
                f"{trouble(error, SPEC.title)}. {NOT_CONFIRMED}",
                row=attempt.row,
                journal_id=attempt.journal_id,
                layout_confirmed=False,
            ) from error

    def _settle(
        self,
        book: Spreadsheet,
        attempt: _Attempt,
        failure: Exception | None,
        shifted: UnconfirmedWrite | None,
    ) -> FillResult:
        """Исход записи в этом же вызове — в окне в секунду после неё."""
        cause = "запись ушла" if failure is None else f"запись упала: {describe_error(failure)}"
        seen = self._reread(book, attempt, cause)
        if seen.layout_confirmed:
            strangers = _differing(attempt, seen.values)
            if strangers:
                self._roll_back(book, attempt, seen, strangers)
            note = joined(
                "" if failure is None else f"{cause}, но строка перечитана — легла",
                _shifted_note(shifted),
            )
            self._finish_verified(attempt, seen, note or None)
            log.info(
                "«%s»: строка %s заполнена, id %s — журнал №%s",
                SPEC.title,
                attempt.row,
                attempt.ref_id,
                attempt.journal_id,
            )
            return FillResult(attempt.row, attempt.ref_id, attempt.journal_id, False, shifted)
        if failure is not None and seen.untouched:
            self._journal.finish(
                attempt.journal_id,
                status=FAILED,
                error=f"запись не легла: {describe_error(failure)}",
                before=attempt.rows_only(),
                after=seen.after(attempt),
            )
            log.warning("«%s»: запись в строку %s не легла — %s", SPEC.title, attempt.row, cause)
            raise WriteNotConfirmedError(
                NOT_APPLIED,
                row=attempt.row,
                journal_id=attempt.journal_id,
                layout_confirmed=True,
            ) from failure
        raise self._unconfirmed(
            attempt,
            seen.after(attempt),
            _shift_reason(seen),
            _unconfirmed_text(attempt.row, attempt.journal_id),
        )

    def _resume(
        self, book: Spreadsheet, prior: OpenWrite, shifted: UnconfirmedWrite | None
    ) -> FillResult | None:
        """Прежняя попытка с этим ключом прервалась посреди записи.

        Её строку перечитываем. Легла — ответ ею, и **ничего в ней не
        трогаем**: прошли, может быть, часы, и шеф строкой уже пользуется —
        возврат своих ячеек годится только для окна в секунду в том же
        вызове, что и запись. Что в строке иначе, чем отправляла та попытка,
        — в журнал. Не легла и лист на месте — попытка ``failed``, пишем
        заново (``None``). Иначе — отказ.
        """
        attempt = _Attempt.of(prior)
        cause = "прежняя попытка прервалась, исход неизвестен"
        seen = self._reread(book, attempt, cause)
        if seen.layout_confirmed:
            note = joined(
                f"{cause}; строка перечитана — легла",
                _chef_note(_differing(attempt, seen.values)),
                _shifted_note(shifted),
            )
            self._finish_verified(attempt, seen, note)
            return FillResult(attempt.row, attempt.ref_id, attempt.journal_id, True, shifted)
        if seen.untouched:
            self._journal.finish(
                attempt.journal_id,
                status=FAILED,
                error="прежняя попытка не записала строку — пишем заново",
                before=attempt.rows_only(),
                after=seen.after(attempt),
            )
            return None
        raise self._unconfirmed(
            attempt,
            seen.after(attempt),
            _shift_reason(seen),
            _resumed_unconfirmed_text(attempt.row, attempt.journal_id),
        )

    def _finish_verified(self, attempt: _Attempt, seen: _Reread, note: str | None) -> None:
        """Запись состоялась: хеш — ``row_hash`` строки A–T, как её видит
        шеф, — ровно как хеширует импорт книги кухни."""
        self._journal.finish(
            attempt.journal_id,
            status=VERIFIED,
            content_hash=row_hash(seen.line),
            note=note,
            before=attempt.rows_only(),
            after=seen.after(attempt),
        )

    def _roll_back(
        self, book: Spreadsheet, attempt: _Attempt, seen: _Reread, strangers: Sequence[Column]
    ) -> NoReturn:
        """Строку правили одновременно с нами: вернуть только наши ячейки.

        Только в том же вызове, что и запись, и только при подтверждённой
        раскладке. Наша ячейка — где лежит ровно отправленное; ей
        возвращается то, что в ней было до записи (пустое — пусто, умолчание
        заготовки — умолчание), одной записью RAW. Ячейки, где отправленное
        совпадает с прежним, не трогаются. После возврата строка и соседи
        перечитываются ещё раз: сдвинь шеф строки и в этом окне — возвращали,
        возможно, не там; не лежит в возвращённой ячейке прежнее — в неё в
        этом окне вписали своё. И то и другое — разбор по журналу.
        """
        row = attempt.row
        before = attempt.raw_line()
        ours = [
            c
            for c in _WRITTEN
            if attempt.sent.get(c.field) is not None
            and same_cell(attempt.sent[c.field], seen.values[c.index])
            and not same_cell(attempt.sent[c.field], before[c.index])
        ]
        after = {**seen.after(attempt), "restored": [c.letter for c in ours]}
        restore = {
            "valueInputOption": "RAW",
            "data": [
                {
                    "range": f"{_TITLE}!{c.letter}{row}:{c.letter}{row}",
                    "values": [[_as_cell(before[c.index])]],
                }
                for c in ours
            ],
        }
        try:
            if ours:
                book.values_batch_update(restore)
            again = _look(book, attempt)
        except Exception as error:
            raise self._unconfirmed(
                attempt,
                after,
                f"возврат своих ячеек не подтверждён: {describe_error(error)}",
                f"{trouble(error, SPEC.title)}, когда в строке {row} листа ING возвращались наши "
                f"ячейки, — что в ней осталось, неизвестно. Покажите шефу строку {row} листа ING "
                f"(запись журнала №{attempt.journal_id})",
            ) from error
        after["after_restore"] = {
            "rows": {str(row): again.line},
            "values": again.values,
            "neighbours": again.neighbours,
        }
        if not again.place_holds:
            raise self._unconfirmed(
                attempt,
                after,
                "после возврата своих ячеек якорь, название или соседние строки не те — строки "
                "сдвигали",
                _unconfirmed_text(row, attempt.journal_id),
            )
        # Возврат — тоже запись: в его окне шеф мог вписать своё. «Вернули как
        # было» — только если перечитанное совпало с тем, что было до записи.
        unrestored = [
            c for c in ours if not same_cell(_as_sent(before[c.index]), again.values[c.index])
        ]
        if unrestored:
            raise self._unconfirmed(
                attempt,
                after,
                f"после возврата своих ячеек в них не то, что было до записи: "
                f"{_letters(unrestored)}",
                _unconfirmed_text(row, attempt.journal_id),
            )
        letters = ", ".join(c.letter for c in strangers)
        self._journal.finish(
            attempt.journal_id,
            status=ROLLED_BACK,
            error=f"в строке {row} чужие значения в колонках {letters}; наши ячейки возвращены "
            "к тому, что было до записи",
            before=attempt.rows_only(),
            after=after,
        )
        log.warning(
            "«%s»: строку %s правили одновременно с записью (%s) — наши ячейки возвращены, "
            "журнал №%s",
            SPEC.title,
            row,
            letters,
            attempt.journal_id,
        )
        raise WriteNotConfirmedError(
            _rolled_back_text(row), row=row, journal_id=attempt.journal_id, layout_confirmed=True
        )

    def _unconfirmed(
        self, attempt: _Attempt, after: Mapping[str, object], reason: str, message: str
    ) -> WriteNotConfirmedError:
        """Раскладку не подтвердили — ``failed``; ошибка — вызывающему.

        Возвращать нечего и нельзя: в строке может лежать чужой ингредиент.
        В журнале — полный снимок листа (по нему возвращают затёртое) и
        пометка :data:`~kitchen.db.journal.LAYOUT_UNCONFIRMED`: по ней
        следующий удачный перенос этой карточки несёт номер этой записи.
        Экрана журнала пока нет — поэтому ERROR в лог.
        """
        self._journal.finish(
            attempt.journal_id,
            status=FAILED,
            error=reason,
            note=LAYOUT_UNCONFIRMED,
            before=attempt.before,
            after=after,
        )
        log.error(
            "«%s»: запись в строку %s не подтверждена — %s; журнал №%s (полный снимок листа)",
            SPEC.title,
            attempt.row,
            reason,
            attempt.journal_id,
        )
        return WriteNotConfirmedError(
            message, row=attempt.row, journal_id=attempt.journal_id, layout_confirmed=False
        )


# ---------------------------------------------------------------------------
# Мелочи
# ---------------------------------------------------------------------------
def _differing(attempt: _Attempt, values: Sequence[object]) -> tuple[Column, ...]:
    """Колонки, где в строке не то, что должно быть после записи: в
    записанной — отправленное, в оставленной (null) — то, что было до неё."""
    before = attempt.raw_line()
    return tuple(
        c
        for c in _WRITTEN
        if c.field in attempt.sent
        and not same_cell(_expected(attempt.sent[c.field], before[c.index]), values[c.index])
    )


def _expected(sent: SentValue, before: object) -> SentValue:
    return sent if sent is not None else _as_sent(before)


def _as_sent(cell: object) -> SentValue:
    """Ячейка FORMULA-чтения как значение тела: пустая — null."""
    if cell is None or cell == "":
        return None
    if isinstance(cell, str | int | float) and not isinstance(cell, bool):
        return cell
    return str(cell)


def _as_cell(cell: object) -> str | int | float:
    """Прежнее значение ячейки для возврата RAW: пустое — пустая строка
    (Google очищает ячейку), число — числом, текст — текстом."""
    value = _as_sent(cell)
    return "" if value is None else value


def _sent_from(prior: OpenWrite) -> dict[str, SentValue]:
    """Отправленное попыткой — из журнала: сверять надо с ним."""
    sent: dict[str, SentValue] = {}
    for field in FIELDS:
        if field not in prior.values:
            continue
        value = prior.values[field]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, str | int | float)
        ):
            raise ValueError(f"в журнале №{prior.id} у поля {field} не значение ячейки: {value!r}")
        sent[field] = value
    if _ID.field not in sent:
        raise ValueError(f"в журнале №{prior.id} нет отправленного id")
    return sent


def _ref_id(values: Mapping[str, object], journal_id: int) -> str:
    value = values.get(_ID.field)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"в журнале №{journal_id} id не целое число: {value!r}")
    return str(value)


def _letters(columns: Iterable[Column]) -> str:
    return ", ".join(c.letter for c in columns)


def _chef_note(changed: Sequence[Column]) -> str:
    return f"с тех пор в листе иначе: {_letters(changed)}" if changed else ""


def _shifted_note(shifted: UnconfirmedWrite | None) -> str:
    if shifted is None:
        return ""
    return f"раскладка прежней попытки не подтверждена — журнал №{shifted.id}, строка {shifted.row}"


def _shift_reason(seen: _Reread) -> str:
    if seen.moved:
        return (
            f"ручные ячейки соседних строк {', '.join(seen.moved)} не те, что при чтении, — "
            "строки внутри зоны QUERY сдвигали; ничего не возвращено"
        )
    if seen.place_holds:
        return (
            "в A нет нашего id, а якорь и название на месте — id стёрли или вписали своё; "
            "ничего не возвращено"
        )
    return (
        "якорь QUERY или название в строке не те, что при чтении, — строки сдвигали; "
        "ничего не возвращено"
    )


def _announced(result: FillResult) -> FillResult:
    """Удачный ответ с оговоркой — ещё и в лог: экрана журнала пока нет."""
    if result.shifted is not None:
        log.error(
            "«%s»: ингредиент в строке %s, но прежняя попытка этого переноса не подтверждена — "
            "покажите шефу строку %s (журнал №%s)",
            SPEC.title,
            result.row,
            result.shifted.row,
            result.shifted.id,
        )
    return result


def _google[T](title: str, what: str, call: Callable[[], T]) -> T:
    """Запрос к Google до записи: отказ — человеку причина словами."""
    try:
        return call()
    except Exception as error:
        log.warning("«%s»: не удалось %s — %s", title, what, describe_error(error))
        raise SheetUnavailableError(
            _NOTHING_CHANGED.format(reason=trouble(error, title))
        ) from error
