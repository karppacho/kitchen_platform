"""Сдвиг строк ING: название одного id пришло к другому.

Ручные колонки ING (id, цены) связаны с выводом ``QUERY`` (названия, КБЖУ)
только номером строки. Ушла строка из вывода — всё ниже съехало, и по id в
базу приехали бы чужие названия. Главный риск здесь — ложная тревога на
обычной правке шефа: она остановила бы перенос всей таблицы кухни. Поэтому
тесты в первую очередь проверяют, чего признак сдвигом не считает.
"""

from __future__ import annotations

import pytest

from kitchen.domain.ing_shift import (
    SHIFT_MIN_MOVED,
    MovedName,
    SheetName,
    looks_shifted,
    moved_names,
)

KNOWN = {
    "1": "Томаты",
    "2": "Сахар",
    "3": "Соль",
    "4": "Сырная котлета",
    "5": "Фритюрное масло",
}
"""Названия в базе: id → название."""


def _sheet(*lines: tuple[str, str]) -> tuple[SheetName, ...]:
    """Лист ING по порядку: шапка — в первой строке, id и названия — со второй."""
    return tuple(
        SheetName(ref_id=ref_id, name=name, row=number)
        for number, (ref_id, name) in enumerate(lines, start=2)
    )


def _known_sheet(**renamed: str) -> tuple[SheetName, ...]:
    """Лист, совпадающий с базой, кроме названий этих id (`id_2="..."`)."""
    names = {f"id_{ref_id}": name for ref_id, name in KNOWN.items()} | renamed
    return _sheet(*((ref_id, names[f"id_{ref_id}"]) for ref_id in KNOWN))


# ---------------------------------------------------------------------------
# Не сдвиг
# ---------------------------------------------------------------------------


def test_unchanged_sheet_moves_nothing() -> None:
    assert moved_names(KNOWN, _known_sheet()) == ()


def test_single_rename_is_not_a_move() -> None:
    """Шеф переименовал позицию в название, которого в базе ни у кого нет."""
    assert moved_names(KNOWN, _known_sheet(id_1="Помидоры черри")) == ()


def test_rename_into_name_the_neighbour_kept_is_not_a_move() -> None:
    """Шеф назвал id 3 «Сахар», а у id 2 «Сахар» остался: это дубль, а не
    сдвиг — название ни у кого не ушло."""
    assert moved_names(KNOWN, _known_sheet(id_3="Сахар")) == ()


def test_new_id_is_not_a_move() -> None:
    """Новый id — сравнивать не с чем, даже если его название носил id,
    который из листа ушёл."""
    sheet = _sheet(
        ("1", "Томаты"),
        ("2", "Сахар"),
        ("3", "Соль"),
        ("4", "Сырная котлета"),
        ("6", "Фритюрное масло"),
    )

    assert moved_names(KNOWN, sheet) == ()


def test_id_gone_from_sheet_is_not_a_move() -> None:
    sheet = _sheet(
        ("1", "Томаты"), ("2", "Сахар"), ("4", "Сырная котлета"), ("5", "Фритюрное масло")
    )

    assert moved_names(KNOWN, sheet) == ()


def test_empty_name_is_not_a_move() -> None:
    """Пустое название не в счёт — даже когда пустым в базе было название
    другого id, а в листе тот получил своё: так бывает с последней строкой
    после прошлого сдвига, которую импорт уже перенёс без названия."""
    known = {**KNOWN, "6": ""}
    sheet = _sheet(
        ("1", "Томаты"),
        ("2", ""),
        ("3", "Соль"),
        ("4", "Сырная котлета"),
        ("5", "Фритюрное масло"),
        ("6", "Перец"),
    )

    assert moved_names(known, sheet) == ()


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Томаты", "томаты"),
        ("Мёд", "Мед"),
        ("Соус сырный, острый", "Соус сырный острый"),
    ],
    ids=["регистр", "ё", "пунктуация"],
)
def test_spelling_is_not_a_move(first: str, second: str) -> None:
    """Регистр, «ё» и пунктуация продукты не различают: два тёзки, записанные
    по-разному, поменялись написаниями — названия у людей прежние."""
    known = {"1": first, "2": second, "3": "Соль"}

    sheet = _sheet(("1", second), ("2", first), ("3", "Соль"))

    assert moved_names(known, sheet) == ()


# ---------------------------------------------------------------------------
# Сдвиг
# ---------------------------------------------------------------------------


def test_block_shift_moves_every_id_below_the_gap() -> None:
    """Как в живом листе: из вывода ``QUERY`` ушла строка id 2 — названия ниже
    съехали на строку вверх, а у последнего id вывода не осталось."""
    sheet = _sheet(
        ("1", "Томаты"),
        ("2", "Соль"),
        ("3", "Сырная котлета"),
        ("4", "Фритюрное масло"),
        ("5", ""),
    )

    assert moved_names(KNOWN, sheet) == (
        MovedName(ref_id="2", row=3, was="Сахар", now="Соль", owner="3"),
        MovedName(ref_id="3", row=4, was="Соль", now="Сырная котлета", owner="4"),
        MovedName(ref_id="4", row=5, was="Сырная котлета", now="Фритюрное масло", owner="5"),
    )


def test_swap_of_two_names_is_a_shift() -> None:
    moved = moved_names(KNOWN, _known_sheet(id_1="Сахар", id_2="Томаты"))

    assert [(m.ref_id, m.owner) for m in moved] == [("1", "2"), ("2", "1")]
    assert looks_shifted(moved)


def test_spelling_does_not_hide_a_move() -> None:
    """Сравнение — по нормализованным названиям, а в ответе — как написано:
    шеф ищет строку по тому, что видит в листе."""
    moved = moved_names(KNOWN, _known_sheet(id_1="сахар", id_2="ТОМАТЫ"))

    assert moved == (
        MovedName(ref_id="1", row=2, was="Томаты", now="сахар", owner="2"),
        MovedName(ref_id="2", row=3, was="Сахар", now="ТОМАТЫ", owner="1"),
    )


def test_duplicate_moved_away_names_the_id_that_lost_it() -> None:
    """«Сахар» в базе у id 2 и 3. Лист съехал: у id 3 теперь «Соль», а «Сахар»
    пришёл к id 4. Название ушло от id 3 — у id 2 оно осталось."""
    known = {"1": "Томаты", "2": "Сахар", "3": "Сахар", "4": "Соль"}

    sheet = _sheet(("1", "Томаты"), ("2", "Сахар"), ("3", "Соль"), ("4", "Сахар"))

    assert moved_names(known, sheet) == (
        MovedName(ref_id="3", row=4, was="Сахар", now="Соль", owner="4"),
        MovedName(ref_id="4", row=5, was="Соль", now="Сахар", owner="3"),
    )


def test_owner_is_the_smallest_id_that_lost_the_name() -> None:
    """Несколько id потеряли одно название — владелец наименьший, числа — по
    числу: 9 раньше 10, иначе текст причины зависел бы от записи id."""
    known = {"x7": "Сахар", "10": "Сахар", "9": "Сахар", "11": "Соль"}

    sheet = _sheet(("9", "Соль"), ("10", "Перец"), ("x7", "Укроп"), ("11", "Сахар"))

    assert [(m.ref_id, m.owner) for m in moved_names(known, sheet)] == [
        ("9", "11"),
        ("11", "9"),
    ]


def test_moves_come_in_sheet_order() -> None:
    """Порядок — по строкам листа, как бы их ни передали: одна и та же беда
    обязана давать один и тот же текст причины."""
    sheet = _known_sheet(id_1="Сахар", id_2="Томаты")

    moved = moved_names(KNOWN, tuple(reversed(sheet)))

    assert [m.row for m in moved] == [2, 3]


# ---------------------------------------------------------------------------
# Порог
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("count", "shifted"), [(0, False), (1, False), (2, True), (3, True)])
def test_shift_starts_from_two_moved_ids(count: int, shifted: bool) -> None:
    """Одно чужое название бывает и у правки шефа; при сдвиге их десятки."""
    moved = [MovedName(ref_id=str(n), row=n + 1, was="а", now="б", owner="0") for n in range(count)]

    assert looks_shifted(moved) is shifted
    assert SHIFT_MIN_MOVED == 2
