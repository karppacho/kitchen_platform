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

import itertools
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
import requests
from gspread.exceptions import APIError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kitchen.sync.client import Cells


# N818 требует суффикс Error, но здесь имя обязано совпадать с gspread:
# читатель распознаёт «листа нет» именно по имени класса. Переименование
# превратило бы дублёр в непохожую подделку.
class WorksheetNotFound(Exception):  # noqa: N818
    """Одноимённое исключение gspread.

    Имя важно: читатель распознаёт «листа нет» по имени класса, чтобы не
    тащить gspread в импорты ради одного типа.
    """


@dataclass(frozen=True, slots=True)
class Formula:
    """Формула в ячейке. Появляется только от записи ``USER_ENTERED``.

    Фальшивка формул не вычисляет и при чтении отдаёт :data:`UNEVALUATED`:
    записанная формула видна при сверке, а не проходит молча.
    """

    text: str


UNEVALUATED = "#ERROR!"
"""Что фальшивка читает из ячейки с формулой."""

Cell = str | int | float | Formula
"""Содержимое ячейки фальшивого листа. Пустая ячейка — пустая строка."""

_SHEET_IDS = itertools.count(1001)
"""Числовые id листов (sheetId) — разные у всех фальшивых листов."""


class FakeWorksheet:
    """Дублёр листа.

    Воспроизводит то свойство настоящего gspread, из-за которого ломался
    код: **хвостовые пустые ячейки не возвращаются**. Лист на двадцать
    колонок отдаёт строку длиной в три, если остальное пусто. Код,
    написанный в расчёте на прямоугольник, падает на этом с IndexError —
    или, хуже, читает не ту колонку.

    Сетка листа конечна, как у настоящего: ``row_count`` строк (по умолчанию
    не меньше 1000 — столько у нового листа Google) и ``col_count`` колонок
    (не меньше 26, A–Z). За её краем Google не пишет и не читает.
    ``row_count`` — снимок на момент открытия листа, как в gspread: после
    ``appendDimension`` свежее число даёт только новое открытие.
    """

    def __init__(
        self,
        values: Cells,
        title: str = "",
        *,
        row_count: int | None = None,
        col_count: int | None = None,
    ) -> None:
        self._cells: list[list[Cell]] = [list(row) for row in values]
        self.title = title
        self.reads = 0
        self.id = next(_SHEET_IDS)
        width = max((len(row) for row in values), default=0)
        self._grid_rows = max(1000, len(values)) if row_count is None else row_count
        self._grid_cols = max(26, width) if col_count is None else col_count
        assert self._grid_rows >= len(values), "строки листа не влезают в его сетку"
        assert self._grid_cols >= width, "колонки листа не влезают в его сетку"
        self.row_count = self._grid_rows

    def get_all_values(self) -> Cells:
        self.reads += 1
        return [_trim([_formatted(cell) for cell in row]) for row in self._cells]

    def _open(self) -> FakeWorksheet:
        """Лист открыли заново: gspread перечитал свойства, row_count свежий."""
        self.row_count = self._grid_rows
        return self

    def _read(self, box: _Box | None, render: Callable[[Cell], object]) -> list[list[object]]:
        """Значения прямоугольника (или всего листа) так, как их отдаёт Google:
        без хвостовых пустых ячеек в строке и без пустых строк в конце."""
        rows: list[list[object]] = []
        top = 0 if box is None else box.top
        bottom = len(self._cells) if box is None else min(box.bottom + 1, len(self._cells))
        for line in self._cells[top:bottom]:
            if box is None:
                cells = line
            else:
                cells = [line[c] if c < len(line) else "" for c in range(box.left, box.right + 1)]
            rows.append(_trim([render(cell) for cell in cells]))
        while rows and not rows[-1]:
            rows.pop()
        return rows

    def _put(self, row: int, column: int, cell: Cell) -> None:
        while len(self._cells) <= row:
            self._cells.append([])
        line = self._cells[row]
        while len(line) <= column:
            line.append("")
        line[column] = cell

    def _clear(self, box: _Box | None) -> None:
        for r, line in enumerate(self._cells):
            for c in range(len(line)):
                if box is None or (box.top <= r <= box.bottom and box.left <= c <= box.right):
                    line[c] = ""


@dataclass(frozen=True, slots=True)
class _Failure:
    """Заказанный тестом отказ одного вызова."""

    applied: bool
    """Успел ли Google сделать своё до того, как вызов упал."""

    error: Exception


_METHODS = ("values_batch_get", "values_batch_update", "values_batch_clear", "batch_update")
"""Вызовы Sheets API, которым тест может заказать отказ."""


class FakeSpreadsheet:
    """Дублёр таблицы.

    Читает и пишет теми же низкоуровневыми вызовами Sheets API, что gspread,
    и отказывает там же, где Google: диапазон, которого не разобрать, лист,
    которого нет, строка за краем сетки («exceeds grid limits»), значений
    больше, чем ячеек в диапазоне, запись без ``valueInputOption``. Ошибки —
    настоящие ``gspread.exceptions.APIError`` с кодом и текстом Google. Тело
    записи проходит через ``json.dumps``, как у requests, и приходит JSON —
    ``Decimal`` в нём падает ``TypeError`` ещё до отправки.

    Осознанные отличия от Google:

    * диапазон без кавычек вокруг имени листа — отказ всегда: Google простое
      имя понял бы, но «Расчётка меню» с пробелом — уже нет;
    * формулы не вычисляются — читаются как «#ERROR!»;
    * лист русский: FORMATTED_VALUE пишет числа с запятой, USER_ENTERED
      понимает целые и числа с запятой, а даты и проценты — нет;
    * диапазоны — только ``'Лист'``, ``'Лист'!A1`` и ``'Лист'!A1:B2``; всё
      прочее — AssertionError, чтобы непонятное не проходило молча.
    """

    def __init__(self, sheets: dict[str, FakeWorksheet]) -> None:
        self._sheets = sheets
        # Счётчики запросов: пакетное чтение затевалось ради экономии квоты,
        # и проверять эту экономию надо числом, а не на слово.
        self.requests = 0
        # Журнал: каждый запрос — имя вызова и то, что ушло бы в Google. По
        # нему видно и тело записи, и порядок «сначала сетка, потом запись».
        self.calls: list[tuple[str, object]] = []
        self._failures: dict[str, list[_Failure]] = {}
        self._tampers: list[tuple[str, str]] = []

    # --- что заказывает тест ------------------------------------------------
    def fail_next(self, method: str, *, applied: bool, error: Exception | None = None) -> None:
        """Следующий вызов ``method`` упадёт; отказ одноразовый.

        ``applied=False`` — Google отказал, ничего не сделав (по умолчанию
        503). ``applied=True`` — Google сделал своё, а ответ потерялся по
        дороге (по умолчанию таймаут чтения): исключение есть, а изменение
        в листе уже лежит.
        """
        assert method in _METHODS, f"фальшивка не умеет отказывать в «{method}»"
        if error is None:
            error = (
                requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)")
                if applied
                else api_error(503, "The service is currently unavailable.", "UNAVAILABLE")
            )
        self._failures.setdefault(method, []).append(_Failure(applied, error))

    def tamper_after_write(self, cell: str, value: str) -> None:
        """Сразу после следующей записи кто-то вписал ``value`` в ``cell``.

        Так выглядит правка шефа между нашей записью и её перечитыванием:
        запись прошла без ошибки, а при сверке ячейка чужая.
        """
        _, box, anchor = self._target(cell)
        assert box is not None and anchor, f"подменить можно одну ячейку, а не «{cell}»"
        self._tampers.append((cell, value))

    # --- Sheets API ---------------------------------------------------------
    def worksheet(self, title: str) -> FakeWorksheet:
        self._sent("worksheet", title)
        try:
            return self._sheets[title]._open()
        except KeyError:
            raise WorksheetNotFound(title) from None

    def worksheets(self) -> list[FakeWorksheet]:
        self._sent("worksheets", None)
        return [sheet._open() for sheet in self._sheets.values()]

    def values_batch_get(
        self, ranges: list[str], params: Mapping[str, str] | None = None
    ) -> dict[str, object]:
        """Дублёр `values.batchGet`.

        Воспроизводит особенности настоящего ответа: диапазоны приходят В ТОМ
        ЖЕ ПОРЯДКЕ, что и запрос; хвостовые пустые ячейки и пустые строки в
        конце не приезжают, а у пустого диапазона ключа `values` нет вовсе.
        """
        options = dict(params or {})
        failure = self._sent("values_batch_get", {"ranges": list(ranges), "params": options})
        render = _renderer(options)
        blocks: list[dict[str, object]] = []
        for text in ranges:
            sheet, box, _ = self._target(text)
            if box is not None:
                _check_grid(text, sheet, box)
            block: dict[str, object] = {"range": text, "majorDimension": "ROWS"}
            rows = sheet._read(box, render)
            if rows:
                block["values"] = rows
            blocks.append(block)
        if failure is not None:
            raise failure.error
        return {"valueRanges": blocks}

    def values_batch_update(self, body: Mapping[str, object] | None = None) -> dict[str, object]:
        """Дублёр `values.batchUpdate`: всё или ничего.

        Диапазон из одной ячейки — точка начала, значения ложатся вправо и
        вниз от неё. Диапазон-прямоугольник — граница: значений больше, чем
        ячеек, — отказ. ``null`` не трогает ячейку, очищает пустая строка.
        """
        wire = _over_the_wire(body or {})
        failure = self._sent("values_batch_update", wire)
        option = wire.get("valueInputOption")
        if option is None:
            raise api_error(400, "'valueInputOption' is required but not specified")
        if option not in ("RAW", "USER_ENTERED"):
            raise api_error(400, f"Invalid value at 'value_input_option' ({option})")

        planned: list[tuple[FakeWorksheet, int, int, Cell]] = []
        responses: list[dict[str, object]] = []
        for item in wire.get("data", []):
            text = item["range"]
            values = item.get("values", [])
            assert item.get("majorDimension", "ROWS") == "ROWS", "фальшивка пишет только строками"
            sheet, box, anchor = self._target(text)
            assert box is not None, f"фальшивка не пишет в лист без адреса ячейки: «{text}»"
            height = len(values)
            width = max((len(row) for row in values), default=0)
            if anchor:
                box = _Box(
                    box.top, box.left, box.top + max(height, 1) - 1, box.left + max(width, 1) - 1
                )
            elif height > box.bottom - box.top + 1:
                raise api_error(
                    400,
                    f"Requested writing within range [{text}], "
                    f"but tried writing to row [{box.top + height}]",
                )
            elif width > box.right - box.left + 1:
                raise api_error(
                    400,
                    f"Requested writing within range [{text}], "
                    f"but tried writing to column [{_column_letter(box.left + width - 1)}]",
                )
            _check_grid(text, sheet, box)
            cells = [
                (sheet, box.top + r, box.left + c, _from_input(value, option))
                for r, row in enumerate(values)
                for c, value in enumerate(row)
                if value is not None
            ]
            planned.extend(cells)
            responses.append({"updatedRange": text, "updatedCells": len(cells)})

        for sheet, row_index, column_index, cell in planned:
            sheet._put(row_index, column_index, cell)
        self._apply_tampers()
        if failure is not None:
            raise failure.error
        return {"totalUpdatedCells": len(planned), "responses": responses}

    def values_batch_clear(
        self,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Дублёр `values.batchClear`: значения пустеют, строки остаются."""
        assert not params, "фальшивка не моделирует параметры очистки"
        wire = _over_the_wire(body or {})
        failure = self._sent("values_batch_clear", wire)
        ranges = wire.get("ranges", [])
        targets: list[tuple[FakeWorksheet, _Box | None]] = []
        for text in ranges:
            sheet, box, _ = self._target(text)
            if box is not None:
                _check_grid(text, sheet, box)
            targets.append((sheet, box))
        for sheet, box in targets:
            sheet._clear(box)
        if failure is not None:
            raise failure.error
        return {"clearedRanges": list(ranges)}

    def batch_update(self, body: Mapping[str, object]) -> dict[str, object]:
        """Дублёр `spreadsheets.batchUpdate`; из запросов — только ``appendDimension``."""
        wire = _over_the_wire(body)
        failure = self._sent("batch_update", wire)
        by_id = {sheet.id: sheet for sheet in self._sheets.values()}
        planned: list[tuple[FakeWorksheet, str, int]] = []
        for index, request in enumerate(wire.get("requests", [])):
            [(kind, spec)] = request.items()
            assert kind == "appendDimension", f"фальшивка не моделирует «{kind}»"
            where = f"Invalid requests[{index}].appendDimension"
            sheet = by_id.get(spec.get("sheetId"))
            if sheet is None:
                raise api_error(400, f"{where}: No grid with id: {spec.get('sheetId')}")
            dimension, length = spec.get("dimension"), spec.get("length")
            if dimension not in ("ROWS", "COLUMNS"):
                raise api_error(400, f"{where}: dimension must be ROWS or COLUMNS")
            if not isinstance(length, int) or length < 1:
                raise api_error(400, f"{where}: length must be greater than 0")
            planned.append((sheet, dimension, length))
        for sheet, dimension, length in planned:
            if dimension == "ROWS":
                sheet._grid_rows += length
            else:
                sheet._grid_cols += length
        if failure is not None:
            raise failure.error
        return {"replies": [{} for _ in planned]}

    # --- внутреннее ---------------------------------------------------------
    def _sent(self, method: str, payload: object) -> _Failure | None:
        """Запрос ушёл: счётчик, журнал и заказанный отказ.

        Отказ «до применения» бросается сразу; «после применения» вызов
        доделывает своё и бросает в конце.
        """
        self.requests += 1
        self.calls.append((method, payload))
        queue = self._failures.get(method)
        failure = queue.pop(0) if queue else None
        if failure is not None and not failure.applied:
            raise failure.error
        return failure

    def _target(self, text: str) -> tuple[FakeWorksheet, _Box | None, bool]:
        """Лист, прямоугольник (``None`` — весь лист) и «одна ячейка-точка
        начала?». Адрес, которого Google не разобрал бы, — его же 400."""
        split = _split_range(text)
        if split is None or split[0] not in self._sheets:
            raise api_error(400, f"Unable to parse range: {text}")
        title, cells = split
        sheet = self._sheets[title]
        if cells is None:
            return sheet, None, False
        parsed = _parse_cells(cells)
        assert parsed is not None, (
            f"фальшивка не разбирает диапазон «{text}»: умеет 'Лист', 'Лист'!A1 и 'Лист'!A1:B2"
        )
        box, anchor = parsed
        return sheet, box, anchor

    def _apply_tampers(self) -> None:
        tampers, self._tampers = self._tampers, []
        for cell, value in tampers:
            sheet, box, _ = self._target(cell)
            assert box is not None
            sheet._put(box.top, box.left, value)


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


def api_error(code: int, message: str, status: str = "INVALID_ARGUMENT") -> APIError:
    """Настоящее исключение gspread — из того же JSON, что присылает Google.

    Не свой двойник: код ловит именно ``gspread.exceptions.APIError`` и
    смотрит на ``code``, и фальшивка обязана бросать то же самое.
    """
    response = requests.Response()
    response.status_code = code
    error = {"code": code, "message": message, "status": status}
    response._content = json.dumps({"error": error}).encode()
    return APIError(response)


@dataclass(frozen=True, slots=True)
class _Box:
    """Прямоугольник ячеек: индексы с нуля, границы включительно."""

    top: int
    left: int
    bottom: int
    right: int


_QUOTED_RANGE = re.compile(r"'((?:[^']|'')+)'(?:!(.+))?")
_CELL = re.compile(r"([A-Z]+)([1-9][0-9]*)")


def _split_range(text: str) -> tuple[str, str | None] | None:
    """«'Лист1'!A6:P6» → («Лист1», «A6:P6»). Кавычка в имени удваивается."""
    match = _QUOTED_RANGE.fullmatch(text)
    if match is None:
        return None
    return match[1].replace("''", "'"), match[2]


def _parse_cells(cells: str) -> tuple[_Box, bool] | None:
    """«A6:P6» → прямоугольник; «A6» → точка начала."""
    start, colon, end = cells.partition(":")
    first = _CELL.fullmatch(start)
    last = _CELL.fullmatch(end) if colon else first
    if first is None or last is None:
        return None
    box = _Box(int(first[2]) - 1, _column_index(first[1]), int(last[2]) - 1, _column_index(last[1]))
    if box.top > box.bottom or box.left > box.right:
        return None
    return box, not colon


def _column_index(letters: str) -> int:
    index = 0
    for char in letters:
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def _column_letter(index: int) -> str:
    letters = ""
    number = index + 1
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _check_grid(text: str, sheet: FakeWorksheet, box: _Box) -> None:
    if box.bottom >= sheet._grid_rows or box.right >= sheet._grid_cols:
        raise api_error(
            400,
            f"Range ({text}) exceeds grid limits. "
            f"Max rows: {sheet._grid_rows}, max columns: {sheet._grid_cols}",
        )


def _over_the_wire(body: object) -> dict[str, object]:
    """Тело запроса глазами Google.

    gspread отдаёт тело requests, а тот кодирует его ``json.dumps`` с
    ``allow_nan=False``: ``Decimal`` падает TypeError, NaN — ValueError, и всё
    это до отправки. Что прошло — приходит уже JSON-ом: кортежи стали
    списками, числа — числами.
    """
    wire: dict[str, object] = json.loads(json.dumps(body, allow_nan=False))
    return wire


_RU_NUMBER = re.compile(r"-?\d+(?:,\d+)?")


def _from_input(value: object, option: str) -> Cell:
    """Значение из тела записи → содержимое ячейки."""
    if isinstance(value, bool):
        raise AssertionError("фальшивка не моделирует логические значения")
    if isinstance(value, int | float):
        return value
    if not isinstance(value, str):
        raise api_error(400, f"Invalid values: {json.dumps(value)}")
    if option == "RAW":
        return value
    # USER_ENTERED — как будто набрал человек в русском листе.
    if value.startswith("'"):
        return value[1:]
    if value.startswith("="):
        return Formula(value)
    if _RU_NUMBER.fullmatch(value):
        return float(value.replace(",", ".")) if "," in value else int(value)
    return value


def _formatted(cell: Cell) -> str:
    """FORMATTED_VALUE — как видит шеф: число с запятой, лист русский."""
    if isinstance(cell, str):
        return cell
    value = _unformatted(cell)
    return value if isinstance(value, str) else str(value).replace(".", ",")


def _unformatted(cell: Cell) -> str | int | float:
    """UNFORMATTED_VALUE — число числом; целое Google отдаёт без «.0»."""
    if isinstance(cell, Formula):
        return UNEVALUATED
    if isinstance(cell, float) and cell.is_integer():
        return int(cell)
    return cell


def _renderer(params: Mapping[str, str]) -> Callable[[Cell], object]:
    unknown = set(params) - {"valueRenderOption", "majorDimension"}
    assert not unknown, f"фальшивка не моделирует параметры чтения {sorted(unknown)}"
    assert params.get("majorDimension", "ROWS") == "ROWS", "фальшивка читает только строками"
    option = params.get("valueRenderOption", "FORMATTED_VALUE")
    if option == "FORMATTED_VALUE":
        return _formatted
    if option == "UNFORMATTED_VALUE":
        return _unformatted
    raise AssertionError(f"фальшивка не моделирует valueRenderOption={option}")


def _trim(row: list[object]) -> list[object]:
    """Строка без хвостовых пустых ячеек — так их отдаёт Google."""
    end = len(row)
    while end and row[end - 1] == "":
        end -= 1
    return row[:end]


@pytest.fixture
def make_client() -> object:
    """Фабрика клиента: ``make_client({"ключ-таблицы": {"Лист": [[...]]}})``."""

    def _make(spreadsheets: dict[str, dict[str, Cells]]) -> FakeSheetsClient:
        return FakeSheetsClient(
            {
                key: FakeSpreadsheet(
                    {title: FakeWorksheet(cells, title) for title, cells in sheets.items()}
                )
                for key, sheets in spreadsheets.items()
            }
        )

    return _make
