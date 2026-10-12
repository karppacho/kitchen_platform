"""Сдвиг строк листа ING: название одного id пришло к другому.

Лист ING книги кухни собран из двух частей. Формула ``QUERY`` выводит
согласованные карточки подряд (B–D, F–K: название, КБЖУ), а ручные колонки
рядом (A — id, E, L–O, Q–T: цены, потери) связаны с выводом только номером
строки. Уйди строка из вывода — карточку перевели в «Нет» или удалили, — и
всё ниже съезжает: по id в базу едут чужие названия и КБЖУ. 04.10.2026 так
молча переименовались 77 ингредиентов, и сайт неделю показывал в составах
блюд чужое.

Признак (решение Александра 12.10): id «получил чужое название», если в листе
у него название X, в базе было другое, а X в базе носил другой id, который в
листе это название потерял — у него теперь иное или его нет в листе. Правка
шефа так не выглядит: новое название либо ничьё, либо осталось у соседа
(дубль). Пустое название, новый id и id, ушедший из листа, не в счёт:
сравнивать не с чем. Сдвиг — когда таких id :data:`SHIFT_MIN_MOVED` и больше.

Названия сравниваются через :func:`normalise_name`: регистр, «ё» и
пунктуация продуктов не различают. В ответе — названия как написаны: шеф
ищет строку по тому, что видит.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from kitchen.domain.matching import normalise_name

SHIFT_MIN_MOVED = 2
"""С какого числа id с чужим названием лист считается съехавшим.

При настоящем сдвиге их десятки: ушла одна строка вывода — съехало всё ниже.
Одно чужое название бывает и у правки шефа: отдал название соседа другой
позиции, а соседа назвал по-новому."""


@dataclass(frozen=True, slots=True)
class SheetName:
    """Название id в листе ING."""

    ref_id: str
    """id из колонки A — как его читает импорт."""

    name: str
    """Название из колонки C — как в листе."""

    row: int
    """Номер строки листа, с единицы."""


@dataclass(frozen=True, slots=True)
class MovedName:
    """id, к которому в листе пришло чужое название."""

    ref_id: str
    row: int
    """Номер строки листа, с единицы."""

    was: str
    """Название этого id в базе."""

    now: str
    """Название в листе."""

    owner: str
    """id, у которого это название было в базе. Таких несколько — наименьший."""


def _id_order(ref_id: str) -> tuple[int, int, str]:
    """Порядок id: числа — по числу (9 раньше 10), прочие — после них, по тексту.

    Иначе владелец, а с ним и текст причины, зависел бы от порядка строк в
    базе, и одна и та же беда давала бы разные причины."""
    if ref_id.isdecimal():
        return (0, int(ref_id), ref_id)
    return (1, 0, ref_id)


def moved_names(known: Mapping[str, str], sheet: Sequence[SheetName]) -> tuple[MovedName, ...]:
    """id листа, к которым пришло чужое название, — в порядке строк листа.

    ``known`` — id → название у ингредиентов в базе, которых не скрыли.
    ``sheet`` — строки листа с непустым id.
    """
    holders: dict[str, list[str]] = {}
    for ref_id, name in known.items():
        holders.setdefault(normalise_name(name), []).append(ref_id)
    names_in_sheet: dict[str, set[str]] = {}
    for line in sheet:
        names_in_sheet.setdefault(line.ref_id, set()).add(normalise_name(line.name))

    moved: list[MovedName] = []
    for line in sorted(sheet, key=lambda entry: entry.row):
        was = known.get(line.ref_id)
        now = normalise_name(line.name)
        if was is None or not now or now == normalise_name(was):
            continue
        # Название ушло от того, у кого в листе его больше нет. Осталось у
        # соседа — это дубль, а не сдвиг.
        lost_it = [
            holder for holder in holders.get(now, ()) if now not in names_in_sheet.get(holder, ())
        ]
        if lost_it:
            moved.append(
                MovedName(
                    ref_id=line.ref_id,
                    row=line.row,
                    was=was,
                    now=line.name,
                    owner=min(lost_it, key=_id_order),
                )
            )
    return tuple(moved)


def looks_shifted(moved: Sequence[MovedName]) -> bool:
    """Съехал ли лист: чужие названия у :data:`SHIFT_MIN_MOVED` id и больше."""
    return len(moved) >= SHIFT_MIN_MOVED
