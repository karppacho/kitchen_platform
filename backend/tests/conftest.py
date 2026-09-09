"""Общие фикстуры и дублёры.

В kitchen_bot conftest.py отсутствует, и общие фабрики живут прямо в
``tests/test_calc.py``, откуда их импортируют три других файла. Работает,
но связывает тесты между собой: правка теста калькулятора роняет тесты
расчётки. Здесь общее лежит в conftest с самого начала.

Дублёры рукописные, а не через мок-библиотеку, и это осознанно. Мок,
настроенный «вернуть вот это», проверяет только наши ожидания. Дублёр,
воспроизводящий **настоящее** поведение SDK — включая неудобное, —
проверяет код. Именно неудобное поведение gspread и было причиной
инцидентов.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from kitchen.sync.client import Cells


# N818 требует суффикс Error, но здесь имя обязано совпадать с gspread:
# читатель распознаёт «листа нет» именно по имени класса. Переименование
# превратило бы дублёр в непохожую подделку.
class WorksheetNotFound(Exception):  # noqa: N818
    """Одноимённое исключение gspread.

    Имя важно: читатель распознаёт «листа нет» по имени класса, чтобы не
    тащить gspread в импорты ради одного типа.
    """


class FakeWorksheet:
    """Дублёр листа.

    Воспроизводит то свойство настоящего gspread, из-за которого ломался
    код: **хвостовые пустые ячейки не возвращаются**. Лист на двадцать
    колонок отдаёт строку длиной в три, если остальное пусто. Код,
    написанный в расчёте на прямоугольник, падает на этом с IndexError —
    или, хуже, читает не ту колонку.
    """

    def __init__(self, values: Cells) -> None:
        self._values = [list(row) for row in values]
        self.reads = 0

    def get_all_values(self) -> Cells:
        self.reads += 1
        return [_trim(row) for row in self._values]


class FakeSpreadsheet:
    def __init__(self, sheets: dict[str, FakeWorksheet]) -> None:
        self._sheets = sheets

    def worksheet(self, title: str) -> FakeWorksheet:
        try:
            return self._sheets[title]
        except KeyError:
            raise WorksheetNotFound(title) from None


class FakeSheetsClient:
    """Источник таблиц, не ходящий в сеть."""

    def __init__(self, spreadsheets: dict[str, FakeSpreadsheet]) -> None:
        self._spreadsheets = spreadsheets
        self.opened: list[str] = []

    def open(self, spreadsheet_id: str) -> FakeSpreadsheet:
        self.opened.append(spreadsheet_id)
        return self._spreadsheets[spreadsheet_id]


class ExplodingSpreadsheet:
    """Таблица, обращение к которой всегда падает.

    Негативный контракт: код, который обязан не ходить в сеть, обязан не
    ходить в сеть. Приём взят из kitchen_bot, где так проверяется, что
    http-метод не запускает браузер.
    """

    def __init__(self, message: str = "к этой таблице обращаться нельзя") -> None:
        self._message = message

    def worksheet(self, title: str) -> FakeWorksheet:
        raise AssertionError(f"{self._message} (просили лист «{title}»)")


def _trim(row: list[str]) -> list[str]:
    end = len(row)
    while end and not str(row[end - 1]).strip():
        end -= 1
    return list(row[:end])


@pytest.fixture
def make_client() -> object:
    """Фабрика клиента: ``make_client({"ключ-таблицы": {"Лист": [[...]]}})``."""

    def _make(spreadsheets: dict[str, dict[str, Cells]]) -> FakeSheetsClient:
        return FakeSheetsClient(
            {
                key: FakeSpreadsheet(
                    {title: FakeWorksheet(cells) for title, cells in sheets.items()}
                )
                for key, sheets in spreadsheets.items()
            }
        )

    return _make
