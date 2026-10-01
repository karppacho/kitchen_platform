"""Карточка ингредиента: поля, чистка текста, КБЖУ и строка для таблицы.

Карточку повар заводит по фото этикетки, а платформа одной записью
добавляет её строкой в «Лист1» книги карточек. Здесь — всё, что в этом
пути решается без ввода-вывода:

* какие поля у карточки и какой ширины строка: ровно 20 значений, A–P и
  S–V. Q (декларация) и R (сертификат халяль) заполняют люди, и запись
  туда стёрла бы их работу;
* как чистится текст, пришедший от повара или прочитанный моделью;
* как строка КБЖУ с этикетки становится ``Decimal`` и когда числа выглядят
  неправдоподобно;
* чего не хватает, чтобы карточку можно было отправить;
* как зовутся файлы фото и ссылки на них.

Модель здесь только читает. Числа и сроки разбирает и считает код: КБЖУ —
этот модуль, срок годности — :mod:`kitchen.domain.shelf_life`. Бот поручал
это модели, и она ошибалась молча.

Имена полей совпадают с раскладкой листа в ``kitchen.sync.specs``. Домен
его не импортирует — слои не позволяют, — поэтому совпадение проверяет тест.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from kitchen.domain.money import parse_decimal
from kitchen.domain.shelf_life import describe_shelf_life

# ---------------------------------------------------------------------------
# Поля
# ---------------------------------------------------------------------------
APPROVED = "Да"
REJECTED = "Отбракован"
APPROVALS = (APPROVED, REJECTED)
"""Что пишется в V «Согласован» — ровно как писал бот."""

DEFAULT_CATEGORIES: tuple[str, ...] = (
    "Мясо",
    "Птица",
    "Рыба и морепродукты",
    "Молочные продукты",
    "Сыры",
    "Овощи",
    "Фрукты",
    "Зелень",
    "Бакалея",
    "Соусы",
    "Специи",
    "Тесто и мука",
    "Напитки",
    "Прочее",
    "Десерты",
)
"""Категории бота — затравка списка; к ним добавляются категории из карточек."""

CARD_FIELDS: tuple[str, ...] = (
    "category",  # A
    "name",  # B
    "label_name",  # C
    "description",  # D
    "manufacturer",  # E
    "supplier",  # F
    "composition",  # G
    "protein",  # H
    "fat",  # I
    "carbs",  # J
    "kcal",  # K
    "shelf_life_sealed",  # L
    "shelf_life_defrost",  # M
    "shelf_life_after",  # N
    "defrost_conditions",  # O
    "label_url",  # P
    # Q «Декларация» и R «Сертификат халяль» — колонки людей, их здесь нет.
    "package_url",  # S
    "before_url",  # T
    "after_url",  # U
    "approval_status",  # V
)
"""Поля строки карточки в порядке колонок листа: A–P, затем S–V."""

NUTRIENT_FIELDS: tuple[str, ...] = ("protein", "fat", "carbs", "kcal")

LABEL_FIELDS: tuple[str, ...] = (
    "label_name",
    "manufacturer",
    "composition",
    *NUTRIENT_FIELDS,
    "shelf_life_sealed",
    "shelf_life_defrost",
    "shelf_life_after",
    "defrost_conditions",
)
"""Одиннадцать полей, которые заполняются по фото этикетки."""

FIELD_TITLES: dict[str, str] = {
    "supplier": "Поставщик",
    "category": "Категория",
    "name": "Название",
    "label_name": "Название по этикетке",
    "manufacturer": "Изготовитель",
    "composition": "Состав",
    "protein": "Белки",
    "fat": "Жиры",
    "carbs": "Углеводы",
    "kcal": "Ккал",
    "shelf_life_sealed": "Срок в закрытой упаковке",
    "shelf_life_defrost": "Срок после дефростации",
    "shelf_life_after": "Срок после нарезки / фасовки",
    "defrost_conditions": "Условия дефростации",
    "description": "Описание",
}
"""Как поле называется для повара — в замечаниях и сообщениях об ошибке."""


# ---------------------------------------------------------------------------
# Чистка текста
# ---------------------------------------------------------------------------
TEXT_LIMITS: dict[str, int] = {
    "supplier": 200,
    "category": 100,
    "name": 200,
    "label_name": 500,
    "manufacturer": 500,
    "composition": 4000,
    "shelf_life_sealed": 300,
    "shelf_life_defrost": 300,
    "shelf_life_after": 300,
    "defrost_conditions": 500,
    "description": 2000,
}
"""Сколько символов поля доходит до листа.

Состав на этикетке бывает длинным, название — нет. Лимит защищает лист и
базу от мусора: ответ модели и текст повара — данные, которым не верим.
"""

_MULTILINE_FIELDS = frozenset({"description"})
"""Где повар может переносить строки. Остальное — в одну строку, как у бота."""


def clean_text(field: str, raw: object) -> str:
    """Текст поля в том виде, в каком он уйдёт в лист.

    Любой пробельный символ (табуляция, перевод страницы, NEL…) становится
    пробелом — слова не склеиваются. Убирает управляющие и невидимые
    символы (нулевой ширины, смену направления письма — ими подделывают
    видимый текст) и то, что не является текстом вовсе. Схлопывает
    пробелы, обрезает по лимиту поля. Переносы строк остаются только в
    описании. Неизвестное поле — KeyError: опечатка в имени не должна
    тихо пропускать текст без лимита.
    """
    limit = TEXT_LIMITS[field]
    if raw is None:
        return ""
    text = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    text = unicodedata.normalize("NFC", "".join(_clean_char(char) for char in text))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    separator = "\n" if field in _MULTILINE_FIELDS else " "
    text = separator.join(line for line in lines if line)
    return text[:limit].strip()


_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
"""Что выбрасывается из текста.

Cc — управляющие (U+0000, U+0007…), Cf — невидимые форматирующие (U+200B
нулевой ширины, U+202E смена направления, U+FEFF метка порядка байтов),
Cs — одиночные суррогаты (их не принимает Postgres: запись упала бы
ошибкой 500), Co — символы для личного использования, Cn — неназначенные.
"""


def _clean_char(char: str) -> str:
    if char == "\n":
        return char
    if char.isspace():
        return " "
    return "" if unicodedata.category(char) in _DROPPED_CATEGORIES else char


# ---------------------------------------------------------------------------
# КБЖУ
# ---------------------------------------------------------------------------
class NutrientUnclearError(ValueError):
    """На этикетке не одно точное число, а что-то, что код не угадывает.

    Текст — для повара, без названия поля: его добавляет тот, кто знает,
    какое поле разбиралось.
    """


_NO_DATA = frozenset({"н/д", "нд", "н.д.", "н.д", "нет", "нет данных", "не указано", "-", "—", "–"})
_KCAL_NUMBER = re.compile(r"(\d[\d\s.,]*?)\s*(?:ккал|kcal)")
_KCAL_WORD = re.compile(r"ккал|kcal")
_KILOJOULES = re.compile(r"кдж|kj")
_BOUND = re.compile(r"[<>≤≥]|\b(?:менее|более|меньше|больше|до|от|около|примерно|max|min)\b")
_RANGE = re.compile(r"\d\s*(?:[-–—]|\.\.\.?|…)\s*\d")
# «30/125 ккал/кДж»: число прямо перед «ккал» здесь — кДж. Какое число из
# пары к какой единице, зависит от порядка единиц, и его код не угадывает.
_NUMBER_PAIR = re.compile(r"\d\s*/\s*\d")
_GRAMS_SUFFIX = re.compile(r"\s*(?:г|гр|g|грамм\w*)\.?$")
# Без разрядных пробелов: у КБЖУ на 100 г их не бывает, а «5 2» или
# «12 5 г» — это две цифры, прочитанные отдельно, а не 52 и 125.
_PLAIN_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

_NUTRIENT_CEILING = Decimal("10000")
"""С этого числа значение на 100 г не бывает ни в каких единицах.

Самое большое правдоподобное — кДж чистого жира, около 3770. Оно ещё
проходит разбор, чтобы проверка правдоподобия назвала его кДж. Всё, что
от 10 000, — склеенные цифры или чужое число (штрихкод, масса нетто);
к тому же колонка базы Numeric(12,3) на огромных числах переполнилась бы
ошибкой 500.
"""
_NUTRIENT_STEP = Decimal("0.001")
"""База хранит три знака после запятой; четвёртый округлился бы молча."""


def parse_nutrient(raw: object) -> Decimal | None:
    """Белки, жиры, углеводы или ккал на 100 г — из строки с этикетки.

    «12,5 г» → 12.5, «250 ккал / 1046 кДж» → 250. Пусто и «н/д» — None:
    нет данных, а не ноль. То, что не одно точное число («<0,5», «10-12»,
    «5 %», только кДж, пара «30/125 ккал/кДж», «5 2»), — и то, что база
    сохранила бы не так, как написано (больше трёх знаков после запятой,
    от 10 000), — :class:`NutrientUnclearError`: подставить границу,
    середину диапазона или одно число из пары значило бы выдумать число.
    """
    if raw is None:
        return None
    # Decimal печатаем без экспоненты: «1E+2» иначе не прошёл бы разбор.
    as_text = format(raw, "f") if isinstance(raw, Decimal) else str(raw)
    written = " ".join(as_text.split())
    text = unicodedata.normalize("NFKC", written).lower()
    if not text or text in _NO_DATA:
        return None

    # Сначала — по всей строке: «до 250 ккал» не должно стать 250 оттого,
    # что число вынули из-за слова «ккал».
    if "%" in text:
        raise NutrientUnclearError(f"«{written}» — это процент, а нужны граммы на 100 г")
    if _BOUND.search(text):
        raise NutrientUnclearError(f"«{written}» — не точное число, впишите значение сами")
    if _RANGE.search(text):
        raise NutrientUnclearError(f"«{written}» — диапазон, а нужно одно число")
    if _NUMBER_PAIR.search(text):
        raise NutrientUnclearError(
            f"«{written}» — два числа через «/»: не понять, какое из них нужно; "
            "впишите значение сами"
        )

    if _KCAL_WORD.search(text):
        match = _KCAL_NUMBER.search(text)
        text = match.group(1).strip() if match else _KCAL_WORD.sub("", text).strip()
    elif _KILOJOULES.search(text):
        raise NutrientUnclearError(f"«{written}» — энергия только в кДж, а нужны ккал")

    number = _GRAMS_SUFFIX.sub("", text)
    value = parse_decimal(number) if _PLAIN_NUMBER.fullmatch(number) else None
    if value is None:
        raise NutrientUnclearError(f"«{written}» — не число")
    if value >= _NUTRIENT_CEILING:
        raise NutrientUnclearError(
            f"«{written}» — слишком большое число для значения на 100 г, проверьте его"
        )
    if value.quantize(_NUTRIENT_STEP) != value:
        raise NutrientUnclearError(
            f"«{written}» — больше трёх знаков после запятой, так на этикетке не пишут; "
            "проверьте число"
        )
    return value


_GRAMS_LIMIT = Decimal("100")
_KCAL_LIMIT = Decimal("900")
"""Чистый жир — около 900 ккал на 100 г. Больше — почти наверняка кДж."""

_KCAL_PER_GRAM = {"protein": Decimal("4"), "fat": Decimal("9"), "carbs": Decimal("4")}
_ENERGY_TOLERANCE_KCAL = Decimal("20")
_ENERGY_TOLERANCE_SHARE = Decimal("0.2")


def check_nutrients(
    protein: Decimal | None,
    fat: Decimal | None,
    carbs: Decimal | None,
    kcal: Decimal | None,
) -> tuple[str, ...]:
    """Замечания к КБЖУ на 100 г. Пустой кортеж — всё правдоподобно.

    Числа не исправляются: код не знает, где ошибка — в этикетке, в чтении
    или в запятой. Он только не молчит.

    * Каждое из Б, Ж, У больше 100 г на 100 г — так не бывает.
    * Ккал больше 900 — больше, чем у чистого жира; похоже на кДж.
    * Б + Ж + У больше 100 г.
    * 4·Б + 9·Ж + 4·У расходится с ккал больше чем на max(20 ккал, 20 %
      указанного). Допуск широкий: клетчатка, спирты и округление на
      этикетке дают свою разницу.

    Одна ошибка — одно замечание. Сумма проверяется, только если ни одно из
    Б, Ж, У не вышло за 100 г само; сверка с ккал — только если ничего
    другого не нашлось: белки 120 вместо 12,0 или кДж в колонке ккал
    разошлись бы и с расчётом, и повар прочёл бы об одной ошибке дважды.
    """
    warnings: list[str] = []
    grams = {"protein": protein, "fat": fat, "carbs": carbs}

    for field, value in grams.items():
        if value is not None and value > _GRAMS_LIMIT:
            warnings.append(
                f"{FIELD_TITLES[field]} — {_human(value)} г на 100 г, больше 100 г: "
                "так не бывает, проверьте число."
            )
    single_over = bool(warnings)
    if kcal is not None and kcal > _KCAL_LIMIT:
        warnings.append(
            f"Ккал — {_human(kcal)} на 100 г, больше 900: так не бывает. "
            "Возможно, это кДж — проверьте число."
        )

    total = sum((value for value in grams.values() if value is not None), Decimal("0"))
    if total > _GRAMS_LIMIT and not single_over:
        warnings.append(
            f"Белки, жиры и углеводы вместе — {_human(total)} г на 100 г, "
            "больше 100 г: проверьте числа."
        )

    if (
        not warnings
        and protein is not None
        and fat is not None
        and carbs is not None
        and kcal is not None
    ):
        estimated = (
            _KCAL_PER_GRAM["protein"] * protein
            + _KCAL_PER_GRAM["fat"] * fat
            + _KCAL_PER_GRAM["carbs"] * carbs
        )
        tolerance = max(_ENERGY_TOLERANCE_KCAL, _ENERGY_TOLERANCE_SHARE * kcal)
        if abs(estimated - kcal) > tolerance:
            rounded = estimated.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            warnings.append(
                f"По белкам, жирам и углеводам выходит около {_human(rounded)} ккал, "
                f"а указано {_human(kcal)} — проверьте числа."
            )

    return tuple(warnings)


def _human(value: Decimal) -> str:
    """Число для повара: без хвостовых нулей и экспоненты, с запятой."""
    normalized = value.normalize()
    exponent = normalized.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0:
        normalized = normalized.quantize(Decimal("1"))
    return str(normalized).replace(".", ",")


# ---------------------------------------------------------------------------
# Черновик и строка для таблицы
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CardDraftData:
    """Содержимое черновика карточки — то, из чего собирается строка листа.

    Фото хранятся идентификаторами файлов Drive; ссылки для листа строит
    :func:`card_row`.
    """

    supplier: str = ""
    category: str = ""
    name: str = ""
    label_name: str = ""
    manufacturer: str = ""
    composition: str = ""
    protein: Decimal | None = None
    fat: Decimal | None = None
    carbs: Decimal | None = None
    kcal: Decimal | None = None
    shelf_life_sealed: str = ""
    shelf_life_defrost: str = ""
    shelf_life_after: str = ""
    defrost_conditions: str = ""
    description: str = ""
    approval: str = ""
    """«Да» или «Отбракован»; пусто — повар ещё не ответил."""
    label_file_id: str = ""
    package_file_id: str = ""
    before_file_id: str = ""
    after_file_id: str = ""


def missing_for_submit(draft: CardDraftData) -> tuple[str, ...]:
    """Чего не хватает для отправки — названиями для повара.

    Обязательны поставщик, категория, название, фото этикетки и ответ
    «согласован?». Остальное — как в боте: распознанные поля повар мог
    стереть, три фото продукта и описание пропускаются, а «Отбракован»
    ведёт сразу к итогу, без них.
    """
    required = (
        ("supplier", draft.supplier),
        ("category", draft.category),
        ("name", draft.name),
    )
    missing = [FIELD_TITLES[field] for field, value in required if not clean_text(field, value)]
    if not draft.label_file_id.strip():
        missing.append("Фото этикетки")
    if draft.approval not in APPROVALS:
        missing.append("Согласован ли продукт")
    return tuple(missing)


def card_row(draft: CardDraftData) -> dict[str, str | Decimal]:
    """Строка листа: ровно 20 значений A–P и S–V по именам полей.

    Q и R сюда не входят никогда. Неизвестное число — пустая ячейка, а не
    ноль: «0 г белка» — это утверждение. Проверку полноты делает
    :func:`missing_for_submit` до вызова, но V «Согласован» проверяется и
    здесь: строку в общем листе почти не отменить, и в V не должно попасть
    ничего, кроме «Да» или «Отбракован» (иначе — ValueError).
    """
    if draft.approval not in APPROVALS:
        raise ValueError(
            f"«Согласован» — только «{APPROVED}» или «{REJECTED}», "
            f"а в черновике «{draft.approval}»: строка для листа не собрана"
        )
    return {
        "category": clean_text("category", draft.category),
        "name": clean_text("name", draft.name),
        "label_name": clean_text("label_name", draft.label_name),
        "description": clean_text("description", draft.description),
        "manufacturer": clean_text("manufacturer", draft.manufacturer),
        "supplier": clean_text("supplier", draft.supplier),
        "composition": clean_text("composition", draft.composition),
        "protein": _number_cell(draft.protein),
        "fat": _number_cell(draft.fat),
        "carbs": _number_cell(draft.carbs),
        "kcal": _number_cell(draft.kcal),
        "shelf_life_sealed": clean_text("shelf_life_sealed", draft.shelf_life_sealed),
        "shelf_life_defrost": clean_text("shelf_life_defrost", draft.shelf_life_defrost),
        "shelf_life_after": clean_text("shelf_life_after", draft.shelf_life_after),
        "defrost_conditions": clean_text("defrost_conditions", draft.defrost_conditions),
        "label_url": _url_cell(draft.label_file_id),
        "package_url": _url_cell(draft.package_file_id),
        "before_url": _url_cell(draft.before_file_id),
        "after_url": _url_cell(draft.after_file_id),
        "approval_status": draft.approval,
    }


def _number_cell(value: Decimal | None) -> str | Decimal:
    return "" if value is None else value


def _url_cell(file_id: str) -> str:
    return drive_view_url(file_id) if file_id else ""


# ---------------------------------------------------------------------------
# Фото
# ---------------------------------------------------------------------------
PHOTO_KINDS: dict[str, str] = {
    "label": "этикетка",
    "package": "упаковка",
    "before": "до",
    "after": "после",
}
"""Слот фото в черновике → слово в имени файла, как у бота."""

MOSCOW = timezone(timedelta(hours=3), "MSK")
"""Москва без перехода на летнее время с 2014 года.

Постоянное смещение вместо базы часовых поясов: на Windows и в тонких
образах её может не оказаться, а время в имени файла от неё не зависит.
"""

DRIVE_FILE_ID = re.compile(r"[A-Za-z0-9_-]+")
"""Как выглядит id файла или папки Drive — проверять ``fullmatch``.

Одно правило на всех: из id собираются и ссылка в листе, и адрес запроса к
Drive, и в настройках хранится id папки. «../», «/», «?» внутри дали бы
ссылку или запрос не туда.
"""

_NOT_NAME_CHARS = re.compile(r"[^\w\s-]")
_FILE_NAME_PART_LIMIT = 80


def drive_view_url(file_id: str) -> str:
    """Ссылка просмотра файла Drive — её бот писал в лист.

    Идентификатор проверяется: из него собирается адрес, и «../» или «?»
    внутри дали бы ссылку не на тот файл.
    """
    if not DRIVE_FILE_ID.fullmatch(file_id):
        raise ValueError(f"Непохоже на идентификатор файла Drive: «{file_id}»")
    return f"https://drive.google.com/file/d/{file_id}/view"


def photo_file_name(supplier: str, ingredient: str, kind: str, moment: datetime) -> str:
    """Имя файла фото, как у бота.

    ``{поставщик}_{ингредиент}_{этикетка|упаковка|до|после}_{ГГГГ-ММ-ДД_ЧЧ-ММ-СС}.jpg``,
    время — московское. По имени шеф находит фото в папке глазами.
    """
    word = PHOTO_KINDS.get(kind)
    if word is None:
        raise ValueError(f"Неизвестный вид фото «{kind}»")
    if moment.tzinfo is None:
        raise ValueError("Время фото без часового пояса — московское время не определить")
    stamp = moment.astimezone(MOSCOW).strftime("%Y-%m-%d_%H-%M-%S")
    return f"{_file_name_part(supplier)}_{_file_name_part(ingredient)}_{word}_{stamp}.jpg"


def _file_name_part(text: str) -> str:
    """Буквы, цифры, дефисы; пробелы → «_»; не длиннее 80 — как в боте."""
    cleaned = _NOT_NAME_CHARS.sub("", unicodedata.normalize("NFC", text))
    cleaned = "_".join(cleaned.split())
    return cleaned[:_FILE_NAME_PART_LIMIT] or "noname"


# ---------------------------------------------------------------------------
# Разбор того, что модель прочитала с этикетки
# ---------------------------------------------------------------------------
LABEL_EXTRACTION_KEYS: tuple[str, ...] = (
    "label_name",
    "manufacturer",
    "composition",
    "proteins",
    "fats",
    "carbohydrates",
    "kcal",
    "nutrition_basis",
    "shelf_life_period",
    "manufactured_on",
    "best_before",
    "storage_conditions",
    "shelf_life_defrost",
    "shelf_life_after",
    "defrost_conditions",
)
"""Ключи ответа модели, которые читает :func:`label_fields_from_extraction`.

Имена КБЖУ — как в промпте бота. ``shelf_life_sealed`` бота сюда не входит
намеренно: в нём бот просил модель считать срок, а теперь срок строит
:func:`kitchen.domain.shelf_life.describe_shelf_life` из периода и дат.
"""

_EXTRACTION_NUTRIENTS = {
    "proteins": "protein",
    "fats": "fat",
    "carbohydrates": "carbs",
    "kcal": "kcal",
}
_PER_HUNDRED_GRAMS = re.compile(r"(?<!\d)100\s*(?:г|гр|грамм\w*|g)\.?(?!\w)")
_PER_HUNDRED_MILLILITRES = re.compile(r"(?<!\d)100\s*(?:мл|ml|миллилитр\w*)\.?(?!\w)")


def label_fields_from_extraction(
    extraction: Mapping[str, object],
) -> tuple[dict[str, str | Decimal | None], tuple[str, ...]]:
    """Одиннадцать полей карточки из прочитанного моделью и замечания повару.

    Текст чистится, КБЖУ разбирается :func:`parse_nutrient` и проверяется
    :func:`check_nutrients`, срок в закрытой упаковке строится из периода и
    дат. Неясное число — пустое поле и замечание, а не догадка.
    """
    warnings: list[str] = []

    nutrients: dict[str, Decimal | None] = {}
    for source, field in _EXTRACTION_NUTRIENTS.items():
        try:
            nutrients[field] = parse_nutrient(extraction.get(source))
        except NutrientUnclearError as error:
            nutrients[field] = None
            warnings.append(f"{FIELD_TITLES[field]}: {error}")

    basis = " ".join(_text_or_empty(extraction.get("nutrition_basis")).split())
    per_grams = not basis or bool(_PER_HUNDRED_GRAMS.search(basis.lower()))
    per_millilitres = not per_grams and bool(_PER_HUNDRED_MILLILITRES.search(basis.lower()))
    has_numbers = any(value is not None for value in nutrients.values())
    if not per_grams and not per_millilitres:
        if has_numbers:
            warnings.append(
                f"Пищевая ценность на этикетке указана «{basis}», а в карточку нужна "
                "на 100 г — впишите белки, жиры, углеводы и ккал сами."
            )
        nutrients = dict.fromkeys(NUTRIENT_FIELDS)
    else:
        # На 100 мл числа переносятся как есть: без плотности на 100 г их не
        # пересчитать, а для соусов и молока разница невелика. Но молчать
        # нельзя — колонка в таблице подписана «на 100 г».
        if per_millilitres and has_numbers:
            warnings.append(
                "Пищевая ценность на этикетке — на 100 мл, а колонка в таблице — "
                "на 100 г; числа перенесены как есть, проверьте."
            )
        # Основа не прочитана — чаще всего это 100 г, и числа остаются. Но
        # это догадка: числа на порцию так тихо ушли бы в колонку «на 100 г».
        if not basis and has_numbers:
            warnings.append(
                "На этикетке не указано, на что даны белки, жиры, углеводы и ккал, — "
                "проверьте: на 100 г или на порцию."
            )
        warnings.extend(
            check_nutrients(
                nutrients["protein"], nutrients["fat"], nutrients["carbs"], nutrients["kcal"]
            )
        )

    sealed, shelf_warnings = describe_shelf_life(
        _text_or_none(extraction.get("shelf_life_period")),
        _text_or_none(extraction.get("manufactured_on")),
        _text_or_none(extraction.get("best_before")),
        _text_or_none(extraction.get("storage_conditions")),
    )
    warnings.extend(shelf_warnings)

    values: dict[str, str | Decimal | None] = {
        "label_name": clean_text("label_name", extraction.get("label_name")),
        "manufacturer": clean_text("manufacturer", extraction.get("manufacturer")),
        "composition": clean_text("composition", extraction.get("composition")),
        **nutrients,
        "shelf_life_sealed": clean_text("shelf_life_sealed", sealed),
        "shelf_life_defrost": clean_text(
            "shelf_life_defrost", extraction.get("shelf_life_defrost")
        ),
        "shelf_life_after": clean_text("shelf_life_after", extraction.get("shelf_life_after")),
        "defrost_conditions": clean_text(
            "defrost_conditions", extraction.get("defrost_conditions")
        ),
    }
    return values, tuple(warnings)


def _text_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def _text_or_empty(value: object) -> str:
    return "" if value is None else str(value)
