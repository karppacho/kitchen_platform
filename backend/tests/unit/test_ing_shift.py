"""Сдвиг строк ING: название одного id пришло к другому или пропало.

Ручные колонки ING (id, цены) связаны с выводом ``QUERY`` (названия, КБЖУ)
только номером строки. Ушла строка из вывода — всё ниже съехало, и по id в
базу приехали бы чужие названия, а у последнего id — пустое. Главный риск
здесь — ложная тревога на обычной правке шефа: она остановила бы перенос всей
таблицы кухни. Поэтому тесты в первую очередь проверяют, чего признак сдвигом
не считает.
"""

from __future__ import annotations

import pytest

from kitchen.domain.ing_shift import (
    SHIFT_MIN_LOST,
    SHIFT_MIN_MOVED,
    LostName,
    MovedName,
    SheetName,
    looks_shifted,
    lost_names,
    moved_id_count,
    moved_names,
    shift_id_count,
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
    """Пустое название — не чужое, даже когда пустым в базе было название
    другого id, а в листе тот получил своё: так бывает с последней строкой
    после прошлого сдвига, которую импорт уже перенёс без названия. Пропажу
    названия считает :func:`lost_names`."""
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
    assert looks_shifted(moved, ())


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


def test_owner_gone_from_sheet_lost_the_name() -> None:
    """Шеф удалил ручные ячейки строки id 4 со сдвигом вверх: id 4 из листа
    пропал, а вывод ``QUERY`` остался на месте — «Сырная котлета» пришла к
    id 5. Владелец, которого в листе нет, название потерял."""
    sheet = _sheet(("1", "Томаты"), ("2", "Сахар"), ("3", "Соль"), ("5", "Сырная котлета"))

    assert moved_names(KNOWN, sheet) == (
        MovedName(ref_id="5", row=5, was="Фритюрное масло", now="Сырная котлета", owner="4"),
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


def test_owner_tie_is_broken_by_text() -> None:
    """«7» и «007» — одно число, но разные id. Владелец не зависит от порядка
    строк в базе: при равном числе — по тексту."""
    known = {"7": "Сахар", "007": "Сахар", "8": "Соль"}

    sheet = _sheet(("7", "Перец"), ("007", "Укроп"), ("8", "Сахар"))

    [moved] = moved_names(known, sheet)
    assert moved.owner == "007"


def test_superscript_digit_id_does_not_break_owner_order() -> None:
    """«²» для `str.isdigit` — цифра, а `int` его не берёт: такой id в базе не
    должен ронять цикл синхронизации. Он не число — и идёт после чисел."""
    known = {"²": "Сахар", "10": "Сахар", "11": "Соль"}

    sheet = _sheet(("²", "Перец"), ("10", "Укроп"), ("11", "Сахар"))

    [moved] = moved_names(known, sheet)
    assert moved.owner == "10"


def test_moves_come_in_sheet_order() -> None:
    """Порядок — по строкам листа, как бы их ни передали: одна и та же беда
    обязана давать один и тот же текст причины."""
    sheet = _known_sheet(id_1="Сахар", id_2="Томаты")

    moved = moved_names(KNOWN, tuple(reversed(sheet)))

    assert [m.row for m in moved] == [2, 3]


# ---------------------------------------------------------------------------
# Пропавшее название
# ---------------------------------------------------------------------------


def test_empty_name_of_known_id_is_lost() -> None:
    """У id 3 в базе «Соль», а в листе название пусто: законно так не бывает —
    импорт стёр бы название. Одного такого id хватает для сдвига."""
    lost = lost_names(KNOWN, _known_sheet(id_3=""))

    assert lost == (LostName(ref_id="3", row=4, was="Соль"),)
    assert looks_shifted((), lost)


def test_name_of_punctuation_only_is_lost() -> None:
    """Пусто — после :func:`normalise_name`: прочерк вместо названия продукт не
    называет, и импорт перенёс бы его вместо «Соли»."""
    assert lost_names(KNOWN, _known_sheet(id_3="—")) == (LostName(ref_id="3", row=4, was="Соль"),)


def test_empty_name_that_was_empty_in_base_is_not_lost() -> None:
    """Названия не было и в базе — терять нечего: такую строку импорт уже
    перенёс, и сайт её уже показывает."""
    known = {**KNOWN, "6": ""}

    sheet = (*_known_sheet(), SheetName(ref_id="6", name="", row=7))

    assert lost_names(known, sheet) == ()


def test_new_id_with_empty_name_is_not_lost() -> None:
    """Нового id в базе нет — сравнивать не с чем."""
    sheet = (*_known_sheet(), SheetName(ref_id="6", name="", row=7))

    assert lost_names(KNOWN, sheet) == ()


def test_empty_copy_of_a_named_id_is_lost() -> None:
    """Лист выровняли, а ручные ячейки последней строки не очистили: id 3 стоит
    дважды — со своим названием и в пустой строке ниже. Импорт берёт последнюю
    строку id, и пустая копия стёрла бы «Соль». Считается пустая строка."""
    sheet = (*_known_sheet(), SheetName(ref_id="3", name="", row=7))

    assert lost_names(KNOWN, sheet) == (LostName(ref_id="3", row=7, was="Соль"),)


def test_lost_names_come_in_sheet_order() -> None:
    sheet = _known_sheet(id_2="", id_4="")

    lost = lost_names(KNOWN, tuple(reversed(sheet)))

    assert [(item.ref_id, item.row) for item in lost] == [("2", 3), ("4", 5)]


def test_last_card_gone_is_a_shift() -> None:
    """Из вывода ``QUERY`` ушла последняя строка — съезжать нечему, чужих
    названий нет. У последнего id название пропало — этого хватает."""
    sheet = _known_sheet(id_5="")

    assert moved_names(KNOWN, sheet) == ()
    assert lost_names(KNOWN, sheet) == (LostName(ref_id="5", row=6, was="Фритюрное масло"),)
    assert looks_shifted(moved_names(KNOWN, sheet), lost_names(KNOWN, sheet))


def test_second_to_last_card_gone_is_a_shift() -> None:
    """Ушла предпоследняя строка вывода: одно чужое название — ниже порога, —
    и у последнего id название пропало. Вместе — сдвиг."""
    sheet = _known_sheet(id_4="Фритюрное масло", id_5="")

    moved = moved_names(KNOWN, sheet)
    lost = lost_names(KNOWN, sheet)

    assert moved == (
        MovedName(ref_id="4", row=5, was="Сырная котлета", now="Фритюрное масло", owner="5"),
    )
    assert lost == (LostName(ref_id="5", row=6, was="Фритюрное масло"),)
    assert looks_shifted(moved, lost)


# ---------------------------------------------------------------------------
# Порог
# ---------------------------------------------------------------------------


def _moved_ids(*ref_ids: str) -> list[MovedName]:
    return [
        MovedName(ref_id=ref_id, row=n, was="а", now="б", owner="0")
        for n, ref_id in enumerate(ref_ids, start=2)
    ]


def _lost_ids(*ref_ids: str) -> list[LostName]:
    return [LostName(ref_id=ref_id, row=n, was="а") for n, ref_id in enumerate(ref_ids, start=2)]


@pytest.mark.parametrize(("count", "shifted"), [(0, False), (1, False), (2, True), (3, True)])
def test_shift_starts_from_two_moved_ids(count: int, shifted: bool) -> None:
    """Одно чужое название бывает и у правки шефа; при сдвиге их десятки."""
    moved = _moved_ids(*(str(n) for n in range(count)))

    assert looks_shifted(moved, ()) is shifted
    assert SHIFT_MIN_MOVED == 2


@pytest.mark.parametrize(
    ("moved", "lost", "shifted"),
    [
        ((), (), False),
        ((), ("5",), True),
        (("4",), (), False),
        (("4",), ("5",), True),
        ((), ("5", "5"), True),
    ],
    ids=["ничего", "одно пропавшее", "одно чужое", "чужое и пропавшее", "id пропал дважды"],
)
def test_one_lost_name_is_enough(
    moved: tuple[str, ...], lost: tuple[str, ...], shifted: bool
) -> None:
    """Законно название у известного id не пропадает: правка шефа его меняет,
    а не стирает. Поэтому пропавшее — сигнал уже с одного id."""
    assert looks_shifted(_moved_ids(*moved), _lost_ids(*lost)) is shifted
    assert SHIFT_MIN_LOST == 1


def test_shift_id_count_joins_both_kinds() -> None:
    """Сколько id задел признак: чужое название и пропавшее — без повторов.
    id в двух строках листа — одна позиция."""
    moved = _moved_ids("1", "2", "2")
    lost = _lost_ids("2", "3", "3")

    assert shift_id_count(moved, lost) == 3
    assert shift_id_count(moved, ()) == 2
    assert shift_id_count((), lost) == 2


def test_one_id_in_two_rows_is_one_id() -> None:
    """id стоит в листе дважды (дефект данных), и в обеих строках — чужое
    название. Это одна позиция, а не сдвиг: порог считает id, а не строки.
    Два разных id — уже сдвиг (обмен названиями выше)."""
    sheet = _sheet(
        ("1", "Сахар"),
        ("1", "Соль"),
        ("2", "Перец"),
        ("3", "Укроп"),
        ("4", "Сырная котлета"),
        ("5", "Фритюрное масло"),
    )

    moved = moved_names(KNOWN, sheet)

    assert [(m.ref_id, m.row) for m in moved] == [("1", 2), ("1", 3)], "строки — каждая"
    assert moved_id_count(moved) == 1
    assert not looks_shifted(moved, ())
