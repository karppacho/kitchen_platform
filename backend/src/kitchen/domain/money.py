"""Разбор чисел, приехавших из Google Sheets.

Через эти функции проходит каждое значение из таблицы, поэтому здесь
собраны все форматы, которые реально встречались у шефа:

    "р.443,00"     → 443.00      префикс рубля из выгрузки Excel
    "р.1 030,00"   → 1030.00     неразрывный пробел в разрядах
    "1 030,00 ₽"   → 1030.00     узкий неразрывный пробел, символ рубля
    "12,5"         → 12.5        запятая как десятичный разделитель
    ""             → None        пусто — это отсутствие значения
    "0"            → 0           ноль остаётся нулём: для потерь это
                                 корректное значение, а не «не заполнено»

Почему две функции, а не одна с флагом
--------------------------------------
В kitchen_bot ``_to_decimal`` существует в двух копиях с **разной**
семантикой процентов: в ``src/data/sheets.py`` строка "8%" превращается в
0.08, а в ``src/pricing/exporter.py`` знак процента просто отбрасывается и
получается 8, после чего вызывающий код угадывает по величине, доля это или
проценты. Угадывание по величине ломается ровно там, где маржа равна 1%.

Поэтому здесь разбор явный: :func:`parse_decimal` читает число как
написано, :func:`parse_percent` всегда возвращает долю. Вызывающий код
обязан знать, что он читает, — и это правильно.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# Пробелы, которые Excel и Google Sheets вставляют в разряды. Обычный пробел
# здесь тоже есть: "1 030,00" встречается при копировании руками.
_SPACES = (
    "\u00a0"  # неразрывный
    "\u202f"  # узкий неразрывный
    "\u2009"  # тонкий
    "\u2007"  # цифровой
    " "
)

_CURRENCY = ("р.", "руб.", "руб", "₽")

_HUNDRED = Decimal("100")


def _clean(raw: object) -> str | None:
    """Строка, из которой убраны валюта и разрядные пробелы. None — если пусто."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    lowered = text.lower()
    for symbol in _CURRENCY:
        lowered = lowered.replace(symbol, "")

    for space in _SPACES:
        lowered = lowered.replace(space, "")

    lowered = lowered.replace(",", ".").strip()
    return lowered or None


def parse_decimal(raw: object) -> Decimal | None:
    """Число из ячейки как есть.

    Знак процента отбрасывается без пересчёта: "8%" → 8. Если нужна доля,
    берите :func:`parse_percent` — молча делить здесь означало бы вернуть
    ту самую двусмысленность, из-за которой функция и переписана.

    Возвращает None, если ячейка пуста или значение не разбирается.
    """
    text = _clean(raw)
    if text is None:
        return None
    text = text.replace("%", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_percent(raw: object) -> Decimal | None:
    """Доля из ячейки процента.

    Со знаком процента значение делится на сто: "8%" → 0.08, "0,00%" → 0.
    Без знака считается, что в ячейке уже доля: "0.08" → 0.08. Именно так
    Google Sheets и хранит колонки с процентным форматом.

    Возвращает None, если ячейка пуста или значение не разбирается.
    """
    text = _clean(raw)
    if text is None:
        return None

    explicit = "%" in text
    text = text.replace("%", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value / _HUNDRED if explicit else value
