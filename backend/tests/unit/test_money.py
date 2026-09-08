"""Разбор чисел из Sheets.

Каждый случай здесь — формат, который реально приезжал из таблицы шефа.
Это не абстрактная проверка парсера, а список того, обо что уже спотыкались.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kitchen.domain.money import parse_decimal, parse_percent


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("443", Decimal("443")),
        ("12,5", Decimal("12.5")),
        ("12.5", Decimal("12.5")),
        ("р.443,00", Decimal("443.00")),
        ("руб. 443,00", Decimal("443.00")),
        ("443,00 ₽", Decimal("443.00")),
        # Разрядные пробелы: обычный, неразрывный, узкий неразрывный.
        ("1 030,00", Decimal("1030.00")),
        ("р.1\u00a0030,00", Decimal("1030.00")),
        ("1\u202f030,00", Decimal("1030.00")),
        ("-5,5", Decimal("-5.5")),
    ],
)
def test_parse_decimal_forms(raw: str, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


def test_zero_is_zero_not_missing() -> None:
    """Ноль — значение, а не пропуск.

    Для колонок потерь `0` означает «потерь нет», и превратить его в None
    значило бы потерять смысл. Отличать от пустой ячейки обязательно.
    """
    assert parse_decimal("0") == Decimal("0")
    assert parse_decimal(0) == Decimal("0")
    assert parse_decimal("") is None
    assert parse_decimal("   ") is None
    assert parse_decimal(None) is None


@pytest.mark.parametrize("raw", ["не число", "—", "н/д", "abc", "%"])
def test_unparseable_is_none(raw: str) -> None:
    """Мусор в ячейке не роняет расчёт, а становится None.

    Дальше по цепочке None обязан превратиться в предупреждение — молчаливый
    ноль подрывает доверие к цифрам.
    """
    assert parse_decimal(raw) is None


def test_parse_decimal_does_not_guess_about_percent() -> None:
    """Знак процента отбрасывается без пересчёта — это осознанный контракт.

    В kitchen_bot две функции с одним именем ведут себя по-разному: одна
    делит на сто, другая нет, а вызывающий код угадывает по величине. На
    марже ровно в 1% такое угадывание даёт неверный ответ.
    """
    assert parse_decimal("8%") == Decimal("8")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8%", Decimal("0.08")),
        ("0,00%", Decimal("0")),
        ("56,86%", Decimal("0.5686")),
        ("100%", Decimal("1")),
        # Без знака процента в ячейке уже лежит доля — так Sheets хранит
        # колонки с процентным форматом.
        ("0,5686", Decimal("0.5686")),
        ("0", Decimal("0")),
    ],
)
def test_parse_percent_returns_fraction(raw: str, expected: Decimal) -> None:
    assert parse_percent(raw) == expected


def test_percent_boundary_one() -> None:
    """Граница, на которой ломается угадывание «доля или проценты».

    Значение 1 без знака — это доля (100%), значение 1% — это 0.01.
    Различить их по величине невозможно, поэтому решает только знак.
    """
    assert parse_percent("1") == Decimal("1")
    assert parse_percent("1%") == Decimal("0.01")


@pytest.mark.parametrize("raw", [None, "", "   ", "%", "мусор", "н/д"])
def test_parse_percent_survives_garbage(raw: object) -> None:
    """Пустое и неразбираемое даёт None, а не исключение и не ноль.

    Ноль здесь был бы худшим исходом: «потерь 0%» и «потери не заполнены» —
    разные утверждения, и второе обязано дойти до предупреждения.
    """
    assert parse_percent(raw) is None
