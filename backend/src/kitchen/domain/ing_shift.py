"""Сдвиг строк листа ING: название одного id пришло к другому или пропало.

Лист ING книги кухни собран из двух частей. Формула ``QUERY`` выводит
согласованные карточки подряд (B–D, F–K: название, КБЖУ), а ручные колонки
рядом (A — id, E, L–O, Q–T: цены, потери) связаны с выводом только номером
строки. Уйди строка из вывода — карточку перевели в «Нет» или удалили, — и
всё ниже съезжает: по id в базу едут чужие названия и КБЖУ, а последнему id
вывода не остаётся вовсе. 04.10.2026 так молча переименовались 77
ингредиентов, и сайт неделю показывал в составах блюд чужое.

Признак (решения Александра 12.10) — две части:

* id «получил чужое название», если в листе у него название X, в базе было
  другое, а X в базе носил другой id, который в листе это название потерял —
  у него теперь иное или его нет в листе. Правка шефа так не выглядит: новое
  название либо ничьё, либо осталось у соседа (дубль). Новый id и id,
  ушедший из листа, не в счёт: сравнивать не с чем. Одно такое название
  бывает и у правки шефа — сдвиг с :data:`SHIFT_MIN_MOVED` id.
* id «потерял название», если в строке листа у него название пусто, а в
  базе было. Законно так не бывает: правка шефа название меняет, а не
  стирает. Так выглядит уход последней карточки вывода — съезжать нечему, и
  чужих названий нет, — и пустая копия id, оставленная после выравнивания.
  Сдвиг — уже с :data:`SHIFT_MIN_LOST` id.

Не всякое пропавшее название — сдвиг (решение по ревью 15.10). Вывод
``QUERY`` приходит через ``IMPORTRANGE`` и может пропасть целиком: тогда
чужих названий нет, а пустых — у всех id. Это «пропал вывод формулы», у него
свой совет — ручные колонки не трогать. Какая из двух бед — решает
:func:`ing_trouble`.

Названия сравниваются через :func:`normalise_name`: регистр, «ё» и
пунктуация продуктов не различают, а название из одной пунктуации — пусто. В
ответе — названия как написаны: шеф ищет строку по тому, что видит.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from kitchen.domain.matching import normalise_name

SHIFT_MIN_MOVED = 2
"""С какого числа id с чужим названием лист считается съехавшим.

При настоящем сдвиге их десятки: ушла одна строка вывода — съехало всё ниже.
Одно чужое название бывает и у правки шефа: отдал название соседа другой
позиции, а соседа назвал по-новому."""

SHIFT_MIN_LOST = 1
"""С какого числа id, потерявших название, лист считается съехавшим.

С одного: законно название у известного id не пропадает, а уход последней
карточки вывода других следов не оставляет — ни одного чужого названия."""

QUERY_GONE_MIN_LOST = 3
"""С какого числа id без названия — при ни одном чужом — пропал вывод формулы.

Вывод ``QUERY`` приходит через ``IMPORTRANGE`` и может пропасть целиком: нет
доступа к листу импорта карточек — пусто у всех id зоны, у десятков. Уход
одной-двух последних карточек вывода даёт одно-два пустых. Ошибиться в
сторону «пропал вывод» безопасно: его совет ничего не стирает. Ошибиться в
сторону сдвига — нет: совет «очистите строки без названия» при пустом выводе
стёр бы цены и потери всей зоны, а других копий у них нет."""

IngTrouble = Literal["shift", "query_gone"]
"""Беда листа ING: строки съехали — или пропал вывод формулы ``QUERY``."""


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


@dataclass(frozen=True, slots=True)
class LostName:
    """Строка листа, где у id пусто название, которое в базе было."""

    ref_id: str
    row: int
    """Номер строки листа, с единицы."""

    was: str
    """Название этого id в базе."""


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
    ``sheet`` — строки листа с непустым id. Ответ — по строке на каждое
    чужое название: id, который стоит в листе дважды, может дать две.
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


def moved_id_count(moved: Sequence[MovedName]) -> int:
    """Сколько разных id получили чужое название.

    Не строк: id, который стоит в листе дважды (дефект данных), — одна
    позиция, и сам по себе сдвига не даёт."""
    return len({m.ref_id for m in moved})


def lost_names(known: Mapping[str, str], sheet: Sequence[SheetName]) -> tuple[LostName, ...]:
    """Строки листа, где id потерял название, — в порядке строк листа.

    ``known`` и ``sheet`` — как у :func:`moved_names`. Считается каждая пустая
    строка, даже если в другой строке листа у того же id название есть:
    импорт берёт последнюю строку id, и пустая копия стёрла бы название.
    """
    lost: list[LostName] = []
    for line in sorted(sheet, key=lambda entry: entry.row):
        was = known.get(line.ref_id)
        # Новый id и id, у которого названия не было и в базе, не в счёт:
        # терять нечего.
        if was is None or not normalise_name(was) or normalise_name(line.name):
            continue
        lost.append(LostName(ref_id=line.ref_id, row=line.row, was=was))
    return tuple(lost)


def lost_id_count(lost: Sequence[LostName]) -> int:
    """Сколько разных id потеряли название — не строк, как у чужих."""
    return len({item.ref_id for item in lost})


def shift_id_count(moved: Sequence[MovedName], lost: Sequence[LostName]) -> int:
    """Сколько разных id задел признак: с чужим названием или без названия.

    id в обоих списках — одна позиция. Это число сверяют, прежде чем принять
    съехавший лист вручную."""
    return len({m.ref_id for m in moved} | {item.ref_id for item in lost})


def looks_shifted(moved: Sequence[MovedName], lost: Sequence[LostName]) -> bool:
    """Сработал ли признак: чужие названия у :data:`SHIFT_MIN_MOVED` id и
    больше или пропавшие — у :data:`SHIFT_MIN_LOST`.

    Что это за беда — сдвиг или пропал вывод формулы, — решает
    :func:`ing_trouble`; по нему и решают."""
    return moved_id_count(moved) >= SHIFT_MIN_MOVED or lost_id_count(lost) >= SHIFT_MIN_LOST


def ing_trouble(moved: Sequence[MovedName], lost: Sequence[LostName]) -> IngTrouble | None:
    """Что с листом ING: ``None`` — переносить можно, ``"shift"`` — строки
    съехали, ``"query_gone"`` — пропал вывод формулы ``QUERY``.

    Пропал вывод — ни одного чужого названия, а пропавшие у
    :data:`QUERY_GONE_MIN_LOST` id и больше. Есть хоть одно чужое — вывод на
    месте и съехал: это сдвиг, сколько бы названий ни пропало. Одна функция
    на всех — цикл и ручной перенос решают по ней одинаково.
    """
    if not looks_shifted(moved, lost):
        return None
    if not moved and lost_id_count(lost) >= QUERY_GONE_MIN_LOST:
        return "query_gone"
    return "shift"
