"""Строка справочника ING из согласованной карточки: форма и поиск строки.

Перенос карточки в справочник — не новая строка, а заполнение ручных ячеек
той строки ING, которую формула ``QUERY`` уже вывела для карточки «Да».
Здесь — всё, что в этом пути решается без ввода-вывода:

* разбор формы переноса: числа — ``Decimal``, единица — кг, л или шт, вес
  штуки — только у «шт», потери вводятся в процентах и хранятся долей;
* следующий id справочника;
* где в листе якорь ``QUERY`` и какая строка — карточки;
* какие ячейки строки — формулы, которые писать нельзя.

Строка ищется двумя способами сразу — по названию и по месту среди «Да», —
и годится, только если оба указывают на неё. Ручные колонки ING стоят рядом
с выводом ``QUERY`` по порядку строк: переключи шеф «Да» в середине книги
карточек, и названия съедут относительно id и цен. Запись в съехавшую
строку отдала бы цену и id чужому ингредиенту — и себестоимость считалась
бы по чужим числам правдоподобно. Поэтому всё неоднозначное — отказ, а не
догадка.

Буквы колонок ING совпадают с раскладкой в ``kitchen.sync.specs`` и с
воротами записи ``WRITE_OPEN``. Домен их не импортирует — слои не
позволяют, — поэтому совпадение проверяет тест. Поля формы названы так же,
как поля ING в раскладке: букву для записи писатель берёт оттуда.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kitchen.domain.cards import clean_text
from kitchen.domain.matching import normalise_name
from kitchen.domain.money import parse_decimal
from kitchen.domain.recipe import UNIT_PIECE, UNIT_WEIGHT

# ---------------------------------------------------------------------------
# Колонки ING
# ---------------------------------------------------------------------------
ID_LETTER = "A"
"""id ингредиента — его выдаёт платформа."""

ANCHOR_LETTER = "B"
"""Где стоит формула ``QUERY`` — якорь зоны. Номер строки не зашит: шеф
может добавить старых строк выше."""

NAME_LETTER = "C"
"""Название — вывод ``QUERY`` из колонки B карточки."""

LOSSES_TOTAL_LETTER = "P"
"""Общие потери — формула шефа ``SUM(Q+R+S)`` во всех строках."""

MANUAL_LETTERS: tuple[str, ...] = ("A", "E", "L", "M", "N", "O", "Q", "R", "S", "T")
"""Ручные колонки, которые дописывают шеф и коммерция, — ровно то, что
открыто в воротах записи. B–D и F–K выводит ``QUERY``, P — формула."""

_FORMULA_CHECKED: tuple[str, ...] = tuple(sorted((*MANUAL_LETTERS, LOSSES_TOTAL_LETTER)))


def _cell(row: Sequence[object], letter: str) -> object:
    """Ячейка строки по букве. Рваная строка — хвостовых пустых ячеек
    пакетное чтение не возвращает — даёт пустое."""
    index = ord(letter) - ord("A")
    return row[index] if index < len(row) else ""


def _text(value: object) -> str:
    """Ячейка как текст. В FORMULA-чтении числа приезжают числами."""
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else str(value).strip()


def _is_formula(value: object) -> bool:
    return isinstance(value, str) and value.lstrip().startswith("=")


def _row(rows: Sequence[Sequence[object]], number: int) -> Sequence[object]:
    """Строка листа по номеру с единицы; за концом чтения — пустая."""
    return rows[number - 1] if 0 < number <= len(rows) else ()


# ---------------------------------------------------------------------------
# Форма переноса
# ---------------------------------------------------------------------------
ACTIVE_STATUS = "активный"
"""Что пишется в T «Статус». Так статус ингредиента ставил и бот."""

REFERENCE_UNITS: tuple[str, ...] = (*UNIT_WEIGHT, UNIT_PIECE)
"""Единицы, которые выбирает человек: кг, л, шт. Ветку расчёта выбирает
единица, а не заполненный вес штуки."""

SHORT_NAME_LIMIT = 100
"""Предел короткого имени для iiko.

Чистка — та же, что у названия карточки, и она обрезает по пределу
названия; этот предел короче, поэтому длинное имя получает отказ, а не
молча обрезается."""

LOSS_FIELDS: tuple[str, ...] = ("losses_unpacking", "losses_cutting", "losses_thermal")
"""Потери Q, R, S. Тепловые (S) в расчёте не применяются — осознанный пробел,
но в лист пишутся, как их вписал бы шеф."""

FORM_TITLES: Mapping[str, str] = {
    "short_name": "Короткое имя для iiko",
    "price_per_kg": "Цена за 1 кг (или 1 шт / 1 л)",
    "price_per_pack": "Цена за упаковку",
    "unit": "Единица измерения",
    "weight_per_piece_g": "Вес 1 шт, г",
    "losses_unpacking": "Потери при перетарке и дефросте",
    "losses_cutting": "Потери при нарезке",
    "losses_thermal": "Потери при тепловой обработке",
}
"""Как поле называется для человека — в сообщениях об ошибке."""

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_SHOWN_LIMIT = 40


class ReferenceFormError(ValueError):
    """Форма заполнена с ошибками.

    ``errors`` — поле → текст для человека; все ошибки сразу, а не по одной
    за попытку.
    """

    def __init__(self, errors: Mapping[str, str]) -> None:
        self.errors: dict[str, str] = dict(errors)
        super().__init__("; ".join(self.errors.values()))


@dataclass(frozen=True, slots=True)
class ReferenceForm:
    """Ручные ячейки строки ING, кроме id, — разобранные и проверенные.

    Поля названы как поля ING в раскладке и идут в порядке колонок: E, L,
    M, N, O, Q, R, S, T. ``None`` — «ячейку не трогать»: в заготовке шефа
    там уже стоит своё, а ноль был бы утверждением.
    """

    short_name: str
    price_per_kg: Decimal | None
    """L. Если в строке L — формула от M, писатель её пропускает."""
    price_per_pack: Decimal | None
    unit: str
    weight_per_piece_g: Decimal | None
    losses_unpacking: Decimal
    """Доля, а не проценты: «5» в форме — здесь 0,05."""
    losses_cutting: Decimal
    losses_thermal: Decimal
    status: str = ACTIVE_STATUS

    @classmethod
    def parse(cls, raw: Mapping[str, str | None]) -> ReferenceForm:
        """Форма из того, что ввёл человек.

        Числа — как их пишут люди («12,5», «1 030»). Потери вводятся в
        процентах, по умолчанию 0, каждая от 0 и меньше 100. Ошибки
        собираются по всем полям — :class:`ReferenceFormError`.
        """
        errors: dict[str, str] = {}
        short_name = _short_name(raw.get("short_name"), errors)
        unit = _unit(raw.get("unit"), errors)
        price_per_kg = _price("price_per_kg", raw.get("price_per_kg"), errors)
        price_per_pack = _price("price_per_pack", raw.get("price_per_pack"), errors)
        weight = _weight(raw.get("weight_per_piece_g"), unit, errors)
        losses = {field: _loss(field, raw.get(field), errors) for field in LOSS_FIELDS}
        if errors:
            raise ReferenceFormError(errors)
        return cls(
            short_name=short_name,
            price_per_kg=price_per_kg,
            price_per_pack=price_per_pack,
            unit=unit,
            weight_per_piece_g=weight,
            losses_unpacking=losses["losses_unpacking"],
            losses_cutting=losses["losses_cutting"],
            losses_thermal=losses["losses_thermal"],
        )

    def row_values(self) -> dict[str, str | Decimal | None]:
        """Значения по полям ING в порядке колонок — для писателя строки."""
        return {
            "short_name": self.short_name,
            "price_per_kg": self.price_per_kg,
            "price_per_pack": self.price_per_pack,
            "unit": self.unit,
            "weight_per_piece_g": self.weight_per_piece_g,
            "losses_unpacking": self.losses_unpacking,
            "losses_cutting": self.losses_cutting,
            "losses_thermal": self.losses_thermal,
            "status": self.status,
        }


def _short_name(raw: str | None, errors: dict[str, str]) -> str:
    # Одна строка, без невидимых символов и лишних пробелов — как название
    # карточки: короткое имя уходит в общий лист и дальше в iiko.
    text = clean_text("name", raw)
    title = FORM_TITLES["short_name"]
    if not text:
        errors["short_name"] = f"{title} — обязательно"
    elif len(text) > SHORT_NAME_LIMIT:
        errors["short_name"] = f"{title} — не длиннее {SHORT_NAME_LIMIT} символов"
    return text


def _unit(raw: str | None, errors: dict[str, str]) -> str:
    unit = (raw or "").strip().lower()
    if unit not in REFERENCE_UNITS:
        errors["unit"] = f"{FORM_TITLES['unit']} — кг, л или шт"
    return unit


def _number(
    field: str, raw: str | None, errors: dict[str, str], *, percent: bool
) -> Decimal | None:
    """Число поля или ``None``, если поле пусто. Не число — ошибка у поля.

    Знак процента допустим только у потерь: «12%» в цене — не цена, а
    молча отброшенный процент.
    """
    text = (raw or "").strip()
    if not text:
        return None
    value = None if "%" in text and not percent else parse_decimal(text)
    if value is None or not value.is_finite():
        shown = text if len(text) <= _SHOWN_LIMIT else f"{text[:_SHOWN_LIMIT]}…"
        errors[field] = f"{FORM_TITLES[field]}: «{shown}» — не число"
        return None
    return value


def _price(field: str, raw: str | None, errors: dict[str, str]) -> Decimal | None:
    value = _number(field, raw, errors, percent=False)
    if value is not None and value < _ZERO:
        errors[field] = f"{FORM_TITLES[field]} не может быть меньше нуля"
    return value


def _weight(raw: str | None, unit: str, errors: dict[str, str]) -> Decimal | None:
    field = "weight_per_piece_g"
    value = _number(field, raw, errors, percent=False)
    if field in errors or unit not in REFERENCE_UNITS:
        # Единица не выбрана — какой должен быть вес, не решить.
        return value
    title = FORM_TITLES[field]
    if unit == UNIT_PIECE:
        if value is None:
            errors[field] = f"{title} обязателен для единицы «шт»"
        elif value <= _ZERO:
            errors[field] = f"{title} должен быть больше нуля"
    elif value is not None:
        errors[field] = f"{title} заполняется только для единицы «шт» — для кг и л оставьте пустым"
    return value


def _loss(field: str, raw: str | None, errors: dict[str, str]) -> Decimal:
    """Потеря долей. Человек пишет «5» — это 5 %, в лист уходит 0,05."""
    percent = _number(field, raw, errors, percent=True)
    if percent is None:
        return _ZERO
    # 100 % — от продукта ничего не остаётся: расчёт делит на (1 − потери),
    # и стоимость молча стала бы нулём, а формулы листа — «#ДЕЛ/0!».
    if not _ZERO <= percent < _HUNDRED:
        errors[field] = f"{FORM_TITLES[field]} — от 0 до 100 %, меньше 100"
        return _ZERO
    return percent / _HUNDRED


# ---------------------------------------------------------------------------
# id
# ---------------------------------------------------------------------------
def next_reference_id(ids: Iterable[object]) -> str:
    """Следующий id справочника: наибольший числовой плюс один.

    Сравнение — по числу: по тексту «99» больше «130». Не целые числа и
    текст id не считаются. ``ids`` — значения колонки A из FORMULA- или
    UNFORMATTED-чтения, где id приезжают числами. FORMATTED-чтение не годится:
    id, показанный оформлением (дата, «1,030»), разобрался бы не тем числом
    или не разобрался вовсе, максимум молча занизился бы — и вышел бы
    повторный id. Пустой справочник — «1». Вызывать под очередью писателей:
    иначе два переноса получат один id.
    """
    top = 0
    for raw in ids:
        value = parse_decimal(raw)
        if value is None or not value.is_finite() or value != value.to_integral_value():
            continue
        top = max(top, int(value))
    return str(top + 1)


# ---------------------------------------------------------------------------
# Якорь QUERY
# ---------------------------------------------------------------------------
ANCHOR_MISSING = (
    "В листе ING формула подтягивания карточек не найдена — запись не сделана, "
    "проверьте формулу QUERY в колонке B"
)


class ReferenceLayoutError(ValueError):
    """Лист ING устроен не так, как ждали, — писать в него нельзя."""


def find_query_anchor(formula_rows: Sequence[Sequence[object]]) -> int:
    """Номер строки (с единицы) с формулой ``QUERY`` в колонке B.

    ``formula_rows`` — лист ING в FORMULA-чтении с первой строки: формулу
    видно только в нём. С этой строки ``QUERY`` выводит карточки «Да» по
    порядку. Нет такой формулы — :class:`ReferenceLayoutError`.
    """
    for number, row in enumerate(formula_rows, start=1):
        cell = _cell(row, ANCHOR_LETTER)
        if _is_formula(cell) and "QUERY(" in str(cell).upper():
            return number
    raise ReferenceLayoutError(ANCHOR_MISSING)


# ---------------------------------------------------------------------------
# Поиск строки
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Located:
    """Строка найдена и ждёт переноса: A пуст."""

    row: int
    """Номер строки листа, с единицы."""


@dataclass(frozen=True, slots=True)
class AlreadyFilled:
    """Строка найдена, но id в ней уже стоит: ингредиент уже в справочнике."""

    row: int
    ref_id: str
    """id из колонки A, как его видит человек."""


class NotFound(StrEnum):
    """Почему строку не найти однозначно. Записи нет ни в одном случае."""

    NOT_APPROVED = "not_approved"
    """Карточки нет среди «Да» — в ING её нет по устройству листа."""

    NOT_YET = "not_yet"
    """Названия нет в зоне ``QUERY``: ``IMPORTRANGE`` подтягивает с задержкой."""

    SHIFTED = "shifted"
    """Название и место указывают на разные строки — строки съехали."""

    AMBIGUOUS = "ambiguous"
    """Среди «Да» несколько карточек с этим названием: место не помогает."""

    @property
    def message(self) -> str:
        """Объяснение для человека — словами спеки."""
        return _NOT_FOUND_MESSAGES[self]


_NOT_FOUND_MESSAGES: Mapping[NotFound, str] = {
    NotFound.NOT_APPROVED: "Карточка не согласована — в справочник попадают только «Да»",
    NotFound.NOT_YET: (
        "Строка ещё не появилась в справочнике — таблица подтягивает карточки "
        "с задержкой, попробуйте через несколько минут"
    ),
    NotFound.SHIFTED: (
        "Строки справочника сдвинуты относительно карточек — запись не сделана, проверьте лист ING"
    ),
    NotFound.AMBIGUOUS: (
        "Согласованных карточек с таким названием несколько — не понять, какая строка "
        "справочника относится к этой. Запись не сделана: переименуйте одну из карточек"
    ),
}

type RowSearch = Located | AlreadyFilled | NotFound
"""Итог :func:`locate_row`."""


def locate_row(
    formatted: Sequence[Sequence[object]],
    formula: Sequence[Sequence[object]],
    anchor: int,
    approved_names: Sequence[str],
    card_name: str,
) -> RowSearch:
    """Строка ING карточки — по названию и по месту одновременно.

    ``formatted`` и ``formula`` — лист ING в FORMATTED- и FORMULA-чтении с
    первой строки; ``anchor`` — из :func:`find_query_anchor`;
    ``approved_names`` — названия карточек «Да» в порядке строк книги
    карточек, начиная с первой строки данных, включая пустые: ``QUERY``
    выводит и их, и место считается по всем.

    * По месту: якорь плюс номер карточки среди «Да».
    * По названию: строки зоны ``QUERY`` (от якоря вниз), где C — вывод
      формулы (в FORMULA-чтении пуст) и совпадает с названием карточки.

    Названия сравниваются как ключи карточек при импорте
    (:func:`~kitchen.domain.matching.normalise_name`). Строка годится, только
    если место — одна из строк по названию. В ней пуст A — :class:`Located`,
    стоит id — :class:`AlreadyFilled`. Иначе — :class:`NotFound`: карточки
    нет среди «Да»; она там не одна; названия нет в зоне; название есть, но
    не на месте.
    """
    key = normalise_name(card_name)
    if not key:
        raise ValueError("Пустое название карточки — строку справочника не найти")

    places = [
        anchor + offset for offset, name in enumerate(approved_names) if normalise_name(name) == key
    ]
    if not places:
        return NotFound.NOT_APPROVED
    if len(places) > 1:
        return NotFound.AMBIGUOUS
    (place,) = places

    by_name = [
        number
        for number in range(anchor, len(formatted) + 1)
        if _query_name(formatted, formula, number) == key
    ]
    if place not in by_name:
        return NotFound.SHIFTED if by_name else NotFound.NOT_YET

    ref_id = _text(_cell(_row(formatted, place), ID_LETTER))
    return AlreadyFilled(row=place, ref_id=ref_id) if ref_id else Located(row=place)


def _query_name(
    formatted: Sequence[Sequence[object]], formula: Sequence[Sequence[object]], number: int
) -> str | None:
    """Название в строке, если его вывела ``QUERY``.

    Вывод формулы виден в FORMATTED и пуст в FORMULA. Название, вписанное
    руками, в FORMULA-чтении есть — такая строка не из зоны ``QUERY``.
    """
    if _text(_cell(_row(formula, number), NAME_LETTER)):
        return None
    name = _text(_cell(_row(formatted, number), NAME_LETTER))
    return normalise_name(name) if name else None


# ---------------------------------------------------------------------------
# Формулы в строке
# ---------------------------------------------------------------------------
def formula_cells(row: Sequence[object]) -> tuple[str, ...]:
    """Буквы ручных ячеек строки, где стоит формула, и P — всегда.

    ``row`` — строка ING в FORMULA-чтении. P — формула шефа по устройству
    листа: её не пишут, даже если в этой строке формулу кто-то стёр. L в
    части строк — формула от M: тогда цена за единицу считается в таблице, и
    писатель её пропускает. Формула в любой другой ручной ячейке — отказ
    писателя, а не перезапись. B–D и F–K сюда не входят: их закрывают ворота
    записи.
    """
    return tuple(
        letter
        for letter in _FORMULA_CHECKED
        if letter == LOSSES_TOTAL_LETTER or _is_formula(_cell(row, letter))
    )
