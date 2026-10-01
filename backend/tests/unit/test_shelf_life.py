"""Срок годности для карточки: считает код, а не модель.

Бот просил модель саму вычесть одну дату из другой и просклонять
результат — и модель ошибалась молча: строка выглядела правдоподобно.
Теперь модель только читает даты с этикетки как написаны, а период,
склонение и вид строки задаёт код. Здесь — то, как он обязан это делать.
"""

from __future__ import annotations

from datetime import date

import pytest

from kitchen.domain.shelf_life import (
    describe_shelf_life,
    full_months_between,
    parse_label_date,
    plural_ru,
)


# ---------------------------------------------------------------------------
# Две даты: период считает код
# ---------------------------------------------------------------------------
def test_exact_year_is_twelve_months() -> None:
    """Решение Александра 30.09.2026: ровно год — «12 месяцев», не «1 год»."""
    assert describe_shelf_life(None, "15.06.2025", "15.06.2026", None) == (
        "12 месяцев (с 15.06.2025 до 15.06.2026)",
        (),
    )


def test_short_period_is_in_days_with_conditions() -> None:
    assert describe_shelf_life(None, "01.10.2025", "31.10.2025", "при t +2..+6°C") == (
        "30 дней (с 01.10.2025 до 31.10.2025) при t +2..+6°C",
        (),
    )


def test_bare_temperature_gets_pri() -> None:
    """Условия без «при» читаются как продолжение фразы — «при» дописываем."""
    text, _ = describe_shelf_life(None, "01.10.2025", "31.10.2025", "t +2..+6°C")
    assert text == "30 дней (с 01.10.2025 до 31.10.2025) при t +2..+6°C"


def test_other_conditions_follow_a_comma() -> None:
    text, _ = describe_shelf_life(None, "01.10.2025", "31.10.2025", "в сухом прохладном месте")
    assert text == "30 дней (с 01.10.2025 до 31.10.2025), в сухом прохладном месте"


def test_sixty_days_are_still_days() -> None:
    """Граница: ровно 60 дней — днями, месяцами только то, что длиннее."""
    assert describe_shelf_life(None, "01.01.2025", "02.03.2025", None) == (
        "60 дней (с 01.01.2025 до 02.03.2025)",
        (),
    )


def test_sixty_one_days_are_months() -> None:
    assert describe_shelf_life(None, "01.01.2025", "03.03.2025", None) == (
        "2 месяца (с 01.01.2025 до 03.03.2025)",
        (),
    )


def test_months_are_rounded_down() -> None:
    """61 день — это ещё не два полных месяца, если месяцы длинные."""
    text, _ = describe_shelf_life(None, "01.07.2025", "31.08.2025", None)
    assert text == "1 месяц (с 01.07.2025 до 31.08.2025)"


def test_year_minus_a_day_is_eleven_months() -> None:
    """Полные месяцы, округление вниз: до годовщины не хватило дня."""
    text, _ = describe_shelf_life(None, "15.06.2025", "14.06.2026", None)
    assert text == "11 месяцев (с 15.06.2025 до 14.06.2026)"


@pytest.mark.parametrize(
    ("manufactured", "best_before", "expected"),
    [
        ("01.01.2025", "02.01.2025", "1 день"),
        ("01.01.2025", "03.01.2025", "2 дня"),
        ("01.01.2025", "06.01.2025", "5 дней"),
        ("01.01.2025", "12.01.2025", "11 дней"),
        ("01.01.2025", "22.01.2025", "21 день"),
        ("01.01.2025", "23.01.2025", "22 дня"),
        ("01.01.2024", "01.10.2025", "21 месяц"),
        ("01.01.2024", "01.11.2025", "22 месяца"),
        ("01.01.2024", "01.06.2024", "5 месяцев"),
    ],
)
def test_period_is_declined(manufactured: str, best_before: str, expected: str) -> None:
    text, _ = describe_shelf_life(None, manufactured, best_before, None)
    assert text.startswith(f"{expected} (с ")


def test_two_digit_year_in_period() -> None:
    text, _ = describe_shelf_life(None, "15.06.25", "15.06.26", None)
    assert text == "12 месяцев (с 15.06.2025 до 15.06.2026)"


def test_reversed_dates_give_a_warning_and_no_period() -> None:
    """Перепутанные даты — не отрицательный срок, а вопрос к повару."""
    text, warnings = describe_shelf_life(None, "15.06.2026", "15.06.2025", None)
    assert text == ""
    assert len(warnings) == 1
    assert "15.06.2026" in warnings[0]
    assert "15.06.2025" in warnings[0]


def test_same_dates_give_a_warning() -> None:
    text, warnings = describe_shelf_life(None, "15.06.2025", "15.06.2025", None)
    assert text == ""
    assert warnings


# ---------------------------------------------------------------------------
# Только «годен до»
# ---------------------------------------------------------------------------
def test_only_best_before() -> None:
    assert describe_shelf_life(None, None, "15.06.2026", None) == ("Годен до 15.06.2026", ())


def test_only_best_before_with_conditions_and_short_year() -> None:
    text, warnings = describe_shelf_life(None, "", "15.06.26", "при t -18°C")
    assert text == "Годен до 15.06.2026 при t -18°C"
    assert warnings == ()


def test_unreadable_manufactured_falls_back_to_best_before() -> None:
    text, warnings = describe_shelf_life(None, "изг. вчера", "15.06.2026", None)
    assert text == "Годен до 15.06.2026"
    assert len(warnings) == 1
    assert "изг. вчера" in warnings[0]


def test_unreadable_best_before_is_kept_as_written() -> None:
    """«06.2026» бывает на заморозке: число не выдумываем, пишем как есть."""
    text, warnings = describe_shelf_life(None, None, "06.2026", None)
    assert text == "Годен до 06.2026"
    assert len(warnings) == 1
    assert "06.2026" in warnings[0]


def test_two_dates_in_best_before_are_not_guessed() -> None:
    """«15.06.2025-15.06.2026» в поле «годен до» — не «Годен до 15.06.2025»."""
    text, warnings = describe_shelf_life(None, None, "15.06.2025-15.06.2026", None)
    assert text == "Годен до 15.06.2025-15.06.2026"
    assert len(warnings) == 1
    assert "15.06.2025-15.06.2026" in warnings[0]


def test_only_manufactured_is_a_warning() -> None:
    text, warnings = describe_shelf_life(None, "15.06.2025", None, "при t -18°C")
    assert text == ""
    assert len(warnings) == 1
    assert "15.06.2025" in warnings[0]


def test_nothing_is_empty() -> None:
    assert describe_shelf_life(None, None, None, None) == ("", ())
    assert describe_shelf_life("  ", "", "", "при t -18°C") == ("", ())


# ---------------------------------------------------------------------------
# Период написан на этикетке
# ---------------------------------------------------------------------------
def test_period_is_kept_as_written() -> None:
    """«180 суток» не превращается в «6 месяцев» и не пересчитывается по датам."""
    assert describe_shelf_life("180 суток", "01.01.2025", "01.03.2025", None) == (
        "180 суток",
        (),
    )


def test_period_gets_conditions_once() -> None:
    text, _ = describe_shelf_life("180 суток", None, None, "при t -18°C")
    assert text == "180 суток при t -18°C"


def test_conditions_are_not_duplicated() -> None:
    assert describe_shelf_life("180 суток при t -18°C", None, None, "при t -18°C") == (
        "180 суток при t -18°C",
        (),
    )
    # Условия записаны по-другому, но в периоде они уже есть.
    text, _ = describe_shelf_life("12 месяцев при -18 °C", None, None, "t -18°C")
    assert text == "12 месяцев при -18 °C"


def test_temperature_is_not_lost_behind_other_pri() -> None:
    """«при условии герметичности» — не температура: -18 °C должно остаться."""
    text, _ = describe_shelf_life("6 месяцев при условии герметичности", None, None, "при t -18°C")
    assert text == "6 месяцев при условии герметичности при t -18°C"


def test_other_conditions_are_kept_next_to_temperature() -> None:
    text, _ = describe_shelf_life("12 месяцев при -18 °C", None, None, "в сухом месте")
    assert text == "12 месяцев при -18 °C, в сухом месте"


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        ("при -18°C, в сухом месте", "12 месяцев при -18 °C, в сухом месте"),
        ("t -18°C; в сухом месте", "12 месяцев при -18 °C, в сухом месте"),
        (
            "в сухом месте, при -18°C, вдали от солнца",
            "12 месяцев при -18 °C, в сухом месте, вдали от солнца",
        ),
        ("при -18°C", "12 месяцев при -18 °C"),
    ],
)
def test_temperature_in_both_keeps_the_rest_of_conditions(conditions: str, expected: str) -> None:
    """Температура и в периоде, и в условиях: её второй раз не дописываем, а
    нетемпературная часть условий («в сухом месте») не теряется."""
    text, warnings = describe_shelf_life("12 месяцев при -18 °C", None, None, conditions)

    assert text == expected
    assert text.count("18") == 1, "температура не дублируется"
    assert warnings == ()


def test_decimal_comma_does_not_split_conditions() -> None:
    """«+2,5 °C» — одно число с запятой, а не две части условий."""
    text, _ = describe_shelf_life("30 суток", None, None, "при +2,5 °C, в темноте")

    assert text == "30 суток при +2,5 °C, в темноте"


def test_period_whitespace_is_squashed() -> None:
    text, _ = describe_shelf_life("  12   месяцев ", None, None, None)
    assert text == "12 месяцев"


# ---------------------------------------------------------------------------
# Разбор дат
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15.06.2025", date(2025, 6, 15)),
        ("15.06.25", date(2025, 6, 15)),
        ("5.6.25", date(2025, 6, 5)),
        ("15/06/2025", date(2025, 6, 15)),
        ("15-06-2025", date(2025, 6, 15)),
        ("15.06.2025г.", date(2025, 6, 15)),
        ("15.06.2025 10:30", date(2025, 6, 15)),
        ("2025-06-15", date(2025, 6, 15)),
        ("15 июня 2025 г.", date(2025, 6, 15)),
        ("15 июня 2025", date(2025, 6, 15)),
        ("1 января 26", date(2026, 1, 1)),
        ("3 мая 2025", date(2025, 5, 3)),
        ("3 Марта 2025", date(2025, 3, 3)),
        ("15 июн. 2025", date(2025, 6, 15)),
        ("20 сентября 2025 года", date(2025, 9, 20)),
    ],
)
def test_parse_label_date(raw: str, expected: date) -> None:
    assert parse_label_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "вчера",
        "06.2026",
        "32.01.2025",
        "29.02.2025",
        "15 чего-то 2025",
        "15 числа 2025",
        # Больше одной даты — какую из них брать, код не угадывает.
        "15.06.2025-15.06.2026",
        "15.06.2025 / 2025-06-20",
        "1 июня 2025 – 1 июня 2026",
    ],
)
def test_parse_label_date_refuses(raw: str | None) -> None:
    assert parse_label_date(raw) is None


# ---------------------------------------------------------------------------
# Полные месяцы
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("start", "end", "months"),
    [
        (date(2025, 6, 15), date(2026, 6, 15), 12),
        (date(2025, 6, 15), date(2026, 6, 14), 11),
        (date(2025, 7, 1), date(2025, 8, 31), 1),
        # Конец месяца: три месяца от 31 января — 30 апреля.
        (date(2025, 1, 31), date(2025, 4, 30), 3),
        (date(2024, 2, 29), date(2025, 2, 28), 12),
        (date(2025, 1, 30), date(2025, 2, 1), 0),
        (date(2025, 1, 1), date(2025, 1, 1), 0),
    ],
)
def test_full_months_between(start: date, end: date, months: int) -> None:
    assert full_months_between(start, end) == months


def test_full_months_refuse_reversed_dates() -> None:
    with pytest.raises(ValueError, match="раньше"):
        full_months_between(date(2025, 2, 1), date(2025, 1, 1))


# ---------------------------------------------------------------------------
# Склонения
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "месяцев"),
        (1, "месяц"),
        (2, "месяца"),
        (4, "месяца"),
        (5, "месяцев"),
        (11, "месяцев"),
        (12, "месяцев"),
        (14, "месяцев"),
        (21, "месяц"),
        (22, "месяца"),
        (25, "месяцев"),
        (101, "месяц"),
        (111, "месяцев"),
        (112, "месяцев"),
    ],
)
def test_plural_ru(count: int, expected: str) -> None:
    assert plural_ru(count, "месяц", "месяца", "месяцев") == expected
