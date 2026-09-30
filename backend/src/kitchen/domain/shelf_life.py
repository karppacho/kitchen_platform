"""Срок годности для карточки ингредиента: считает код, а не модель.

Бот просил модель саму вычесть дату изготовления из «годен до», округлить
и просклонять — и модель ошибалась молча: строка выглядела правдоподобно,
а число в ней было неверным. Теперь модель только читает с этикетки даты
и период **как написаны**, а период, склонение и вид строки задаёт этот
модуль.

Правила взяты из промпта бота, с одной поправкой Александра (30.09.2026):

1. На этикетке написан период («180 суток», «12 месяцев при t -18°C») —
   пишем как есть. Условия хранения дописываем, только если их в периоде
   ещё нет.
2. Периода нет, но есть дата изготовления и «годен до» — считаем разницу:
   больше 60 дней — полными месяцами (округление вниз), иначе днями.
   «12 месяцев (с 15.06.2025 до 15.06.2026) при t -18°C».
   Ровно год — «12 месяцев», а не «1 год», как просил бот: всё длиннее
   60 дней пишется месяцами, без исключений для круглых лет.
3. Есть только «годен до» — «Годен до 15.06.2026».
4. Ничего из этого нет — пусто; повар впишет сам.

Всё, что код прочитать не смог, не угадывается, а уходит повару
замечанием: перепутанные даты, дата в непонятном виде.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

MONTHS_AFTER_DAYS = 60
"""Длиннее этого срок пишется месяцами, до него включительно — днями."""

# Первые три буквы названия месяца. «Мая» и «май» различаются уже в третьей
# букве, поэтому оба варианта здесь.
_MONTHS = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "мая": 5,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}

# Дата ищется внутри строки, а не требуется целиком: модель может принести
# «15.06.2025г.» или «15.06.2025 10:30». Окружение цифрами запрещено, чтобы
# из «1506.2025» не вырезать что-нибудь похожее на дату.
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4}|\d{2})(?!\d)")
_WORDS = re.compile(r"(?<!\d)(\d{1,2})\s+([а-я]+)\.?\s*(\d{4}|\d{2})(?!\d)")

# Признаки того, что в периоде уже есть условия хранения.
_HAS_CONDITIONS = re.compile(r"°|\bпри\b|температур", re.IGNORECASE)
_STARTS_WITH_PRI = re.compile(r"при\b", re.IGNORECASE)
# «t +2..+6°C», «-18°C», «+4» — продолжение фразы «при …».
_STARTS_WITH_TEMPERATURE = re.compile(r"(?:[tт]\s*)?[+\-−–]?\s*\d", re.IGNORECASE)


def parse_label_date(raw: str | None) -> date | None:
    """Дата с этикетки или None, если её не удалось прочитать.

    Понимает «15.06.2025», «15.06.25» (год двумя цифрами — 20ГГ, как у бота),
    «15/06/2025», «2025-06-15», «15 июня 2025 г.», «15 июн. 2025».
    Несуществующая дата («29.02.2025») — None, а не ближайшая похожая.
    """
    if not raw:
        return None
    text = raw.lower().replace("ё", "е")

    match = _ISO.search(text)
    if match:
        return _date(match.group(1), match.group(2), match.group(3))

    match = _NUMERIC.search(text)
    if match:
        return _date(match.group(3), match.group(2), match.group(1))

    match = _WORDS.search(text)
    if match:
        month = _MONTHS.get(match.group(2)[:3])
        if month is None:
            return None
        return _date(match.group(3), str(month), match.group(1))

    return None


def _date(year: str, month: str, day: str) -> date | None:
    full_year = 2000 + int(year) if len(year) == 2 else int(year)
    try:
        return date(full_year, int(month), int(day))
    except ValueError:
        return None


def full_months_between(start: date, end: date) -> int:
    """Сколько полных месяцев от start до end, с округлением вниз.

    Месяц от 31 января — это 28 (29) февраля: день, которого нет в месяце,
    заменяется последним днём месяца. Так от 31.01 до 30.04 — три месяца,
    а от 15.06.2025 до 14.06.2026 — одиннадцать: до годовщины не хватило дня.
    """
    if end < start:
        raise ValueError(f"Конец срока {end:%d.%m.%Y} раньше начала {start:%d.%m.%Y}")
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if _add_months(start, months) > end:
        months -= 1
    return months


def _add_months(day: date, months: int) -> date:
    years, month_index = divmod(day.month - 1 + months, 12)
    year = day.year + years
    month = month_index + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def plural_ru(count: int, one: str, few: str, many: str) -> str:
    """Форма слова для числа: 1 месяц, 2 месяца, 5 месяцев.

    11–14 — всегда «многие»: 11 месяцев, 12 месяцев, хотя кончаются на 1 и 2.
    Без этого правила «12 месяца» — ровно та ошибка, ради которой счёт и
    забран у модели.
    """
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many


def describe_shelf_life(
    period: str | None,
    manufactured: str | None,
    best_before: str | None,
    conditions: str | None,
) -> tuple[str, tuple[str, ...]]:
    """Строка срока «в закрытой упаковке» и замечания для повара.

    Все четыре значения — то, что прочитано с этикетки, как написано.
    Возвращает пустую строку, если срок составить не из чего.
    """
    period_text = _squash(period)
    conditions_text = _squash(conditions)

    if period_text:
        if conditions_text and not _already_has_conditions(period_text, conditions_text):
            return _with_conditions(period_text, conditions_text), ()
        return period_text, ()

    made_raw = _squash(manufactured)
    until_raw = _squash(best_before)
    made = parse_label_date(made_raw)
    until = parse_label_date(until_raw)

    warnings: list[str] = []
    if made_raw and made is None:
        warnings.append(
            f"Не удалось прочитать дату изготовления «{made_raw}» — "
            "проверьте срок годности по этикетке."
        )
    if until_raw and until is None:
        warnings.append(
            f"Не удалось прочитать дату «годен до» «{until_raw}» — "
            "проверьте срок годности по этикетке."
        )

    if made is not None and until is not None:
        if until <= made:
            warnings.append(
                f"Дата изготовления {made:%d.%m.%Y} не раньше даты «годен до» "
                f"{until:%d.%m.%Y} — проверьте даты на этикетке и впишите срок сами."
            )
            return "", tuple(warnings)
        text = _period_between(made, until)
    elif until is not None:
        text = f"Годен до {until:%d.%m.%Y}"
    elif until_raw:
        # «06.2026» бывает на заморозке. Число не выдумываем — пишем как есть.
        text = f"Годен до {until_raw}"
    else:
        if made is not None:
            warnings.append(
                f"На этикетке нашлась только дата изготовления {made:%d.%m.%Y} — "
                "срок годности впишите сами."
            )
        return "", tuple(warnings)

    return _with_conditions(text, conditions_text), tuple(warnings)


def _period_between(made: date, until: date) -> str:
    days = (until - made).days
    span = f"(с {made:%d.%m.%Y} до {until:%d.%m.%Y})"
    if days > MONTHS_AFTER_DAYS:
        months = full_months_between(made, until)
        return f"{months} {plural_ru(months, 'месяц', 'месяца', 'месяцев')} {span}"
    return f"{days} {plural_ru(days, 'день', 'дня', 'дней')} {span}"


def _with_conditions(text: str, conditions: str) -> str:
    if not conditions:
        return text
    if _STARTS_WITH_PRI.match(conditions):
        return f"{text} {conditions}"
    if _STARTS_WITH_TEMPERATURE.match(conditions):
        return f"{text} при {conditions}"
    return f"{text}, {conditions}"


def _already_has_conditions(period: str, conditions: str) -> bool:
    """Есть ли в периоде условия хранения — те же или какие-то свои.

    Период с этикетки нередко уже содержит температуру: «12 месяцев при
    -18 °C». Дописать к нему «при t -18°C» — повтор, который повар будет
    стирать руками.
    """
    if _HAS_CONDITIONS.search(period):
        return True
    wanted = _key(conditions).removeprefix("при")
    return bool(wanted) and wanted in _key(period)


def _key(text: str) -> str:
    return "".join(text.casefold().split())


def _squash(value: str | None) -> str:
    """Пробелы схлопнуты, края обрезаны; None — пустая строка."""
    if value is None:
        return ""
    return " ".join(value.split())
