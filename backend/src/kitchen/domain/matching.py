"""Сопоставление названий ингредиентов.

Задача: карточки ингредиентов, которые повара заполняют в одной таблице,
и справочник ING, по которому считается себестоимость, — это две разные
таблицы, между которыми нет никакой связи. Повар оформил ингредиент,
калькулятор его не видит. Склейка справочников и есть главная работа
фазы 1.

Автоматически до конца это не решается, и притворяться, что решается,
опасно: подставить не тот ингредиент хуже, чем не подставить никакого —
себестоимость посчитается по чужой цене и будет выглядеть правдоподобно.
Поэтому здесь только **предложение** кандидатов, а решение принимает шеф.

Правила взяты из справочника:

* имя ингредиента — первичный ключ для людей;
* точное совпадение решает, **только если оно одно**: два активных тёзки
  (сахар по 100 ₽/кг и по 0 ₽/шт) обязаны вызвать переспрос, а не выбор
  первого попавшегося;
* дубли считаются только среди активных — пара «архивная + активная»
  неоднозначности не даёт.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

ARCHIVED = "архив"
"""Статус, исключающий позицию из подбора.

Архивные не предлагаются в новые блюда, но продолжают считаться в
существующих: полтора десятка живых блюд ссылаются на архивные позиции,
и их себестоимость обязана считаться по-прежнему.
"""

SIMILARITY_THRESHOLD = 0.82
"""Ниже этого похожие не показываем.

Подобрано так, чтобы «Огурцы маринованные» и «Огурцы маринованные, не
резанные» попадали в кандидаты, а «Соус сырный» и «Соус чесночный» — нет.
"""

MAX_CANDIDATES = 5

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalise_name(raw: str) -> str:
    """Привести имя к виду, пригодному для сравнения.

    Убирает то, что люди пишут по-разному, и **не трогает** то, что несёт
    смысл. Порядок слов сохраняется: «соус сырный» и «сырный соус» —
    почти наверняка одно, но доказать это мы не можем, и пусть решает шеф.
    """
    text = unicodedata.normalize("NFKC", raw).strip().lower()
    # ё пишут то так, то эдак, и это никогда не различает продукты.
    text = text.replace("ё", "е")
    text = _PUNCTUATION.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


@dataclass(frozen=True, slots=True)
class Entry:
    """Позиция справочника, среди которых ищем."""

    key: str
    """Идентификатор — тот, что стоит в ТТК. Раз присвоен, не меняется."""

    name: str
    status: str = ""

    @property
    def archived(self) -> bool:
        return normalise_name(self.status) == ARCHIVED


@dataclass(frozen=True, slots=True)
class Candidate:
    entry: Entry
    score: float


@dataclass(frozen=True, slots=True)
class Match:
    """Что нашлось для одного названия."""

    query: str
    exact: tuple[Entry, ...]
    similar: tuple[Candidate, ...]

    @property
    def resolved(self) -> Entry | None:
        """Позиция, если ответ однозначен.

        Однозначен он ровно в одном случае: точное совпадение существует и
        оно единственное. Всё остальное — вопрос к человеку.
        """
        return self.exact[0] if len(self.exact) == 1 else None

    @property
    def ambiguous(self) -> bool:
        """Несколько активных тёзок. Подставлять первого попавшегося нельзя."""
        return len(self.exact) > 1

    @property
    def orphan(self) -> bool:
        """Ни точного совпадения, ни похожих — пары в справочнике нет."""
        return not self.exact and not self.similar


class NameIndex:
    """Справочник, по которому идёт подбор.

    Архивные позиции в подбор не попадают, но остаются доступны по ключу:
    расчёт существующих блюд от статуса не зависит.
    """

    def __init__(self, entries: Iterable[Entry]) -> None:
        self._all: dict[str, Entry] = {}
        self._active_by_name: dict[str, list[Entry]] = {}

        for entry in entries:
            self._all[entry.key] = entry
            if entry.archived:
                continue
            self._active_by_name.setdefault(normalise_name(entry.name), []).append(entry)

    def __len__(self) -> int:
        return len(self._all)

    def get(self, key: str) -> Entry | None:
        return self._all.get(key)

    def duplicates(self) -> dict[str, tuple[Entry, ...]]:
        """Активные тёзки.

        Это дефект данных: бот ищет ингредиенты по имени, и два активных
        одинаковых имени делают поиск неоднозначным. Но дефект известный,
        поэтому он не падение, а список — за ним следит храповик в тестах.
        """
        return {
            name: tuple(entries)
            for name, entries in self._active_by_name.items()
            if len(entries) > 1
        }

    def match(self, name: str) -> Match:
        query = normalise_name(name)
        exact = tuple(self._active_by_name.get(query, ()))
        similar = () if exact else self._similar(query)
        return Match(query=query, exact=exact, similar=similar)

    def _similar(self, query: str) -> tuple[Candidate, ...]:
        scored: list[Candidate] = []
        for candidate_name, entries in self._active_by_name.items():
            score = SequenceMatcher(None, query, candidate_name).ratio()
            if score >= SIMILARITY_THRESHOLD:
                scored.extend(Candidate(entry=entry, score=score) for entry in entries)
        # По убыванию похожести, при равенстве — по имени, чтобы порядок был
        # устойчивым: отчёт, меняющийся между прогонами, невозможно сверять.
        scored.sort(key=lambda c: (-c.score, c.entry.name))
        return tuple(scored[:MAX_CANDIDATES])


def match_all(names: Sequence[str], index: NameIndex) -> tuple[Match, ...]:
    """Сопоставить пачку названий."""
    return tuple(index.match(name) for name in names)


@dataclass(frozen=True, slots=True)
class Summary:
    """Разбор пачки сопоставлений по исходам.

    Живёт в домене, а не в скрипте отчёта: это и есть ответ на главный
    вопрос фазы 0, и он обязан быть покрыт тестами, а не проверяться
    глазами при каждом запуске.
    """

    resolved: tuple[Match, ...]
    """Точное совпадение, и оно единственное. Склеивается без человека."""

    ambiguous: tuple[Match, ...]
    """Несколько активных тёзок. Выбирать нельзя — только спрашивать."""

    with_candidates: tuple[Match, ...]
    """Похожее есть, точного нет. Решает шеф."""

    orphans: tuple[Match, ...]
    """Пары в справочнике нет вовсе: ингредиент оформлен, но калькулятор
    его не видит."""

    @property
    def total(self) -> int:
        return (
            len(self.resolved) + len(self.ambiguous) + len(self.with_candidates) + len(self.orphans)
        )

    @property
    def needs_human(self) -> int:
        """Сколько решений придётся принять шефу вручную."""
        return len(self.ambiguous) + len(self.with_candidates) + len(self.orphans)


def summarise(matches: Iterable[Match]) -> Summary:
    """Разложить сопоставления по исходам."""
    resolved: list[Match] = []
    ambiguous: list[Match] = []
    with_candidates: list[Match] = []
    orphans: list[Match] = []

    for match in matches:
        if match.resolved is not None:
            resolved.append(match)
        elif match.ambiguous:
            ambiguous.append(match)
        elif match.similar:
            with_candidates.append(match)
        else:
            orphans.append(match)

    return Summary(
        resolved=tuple(resolved),
        ambiguous=tuple(ambiguous),
        with_candidates=tuple(with_candidates),
        orphans=tuple(orphans),
    )
