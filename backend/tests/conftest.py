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
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

import pytest
import requests
from gspread.exceptions import APIError

from kitchen.domain.cards import APPROVED, REJECTED
from kitchen.sync import specs

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

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
    """Формула в ячейке: от записи ``USER_ENTERED`` или в листе, который
    собрал тест (формулы шефа в ING).

    Фальшивка формул не вычисляет. Значение формулы — ``value``, если его
    задал тест: так выглядит готовый лист шефа, где «=SUM(Q7+R7+S7)» уже
    показывает «0%». Без него при чтении — :data:`UNEVALUATED`: записанная
    формула видна при сверке, а не проходит молча. В FORMULA-чтении видна
    сама формула, ``text``.
    """

    text: str
    value: str | int | float | None = None


@dataclass(frozen=True, slots=True)
class Spill:
    """Ячейка, которую заполнила формула-массив из другой ячейки, — вывод
    ``QUERY`` в листе ING.

    Своего содержимого у неё нет: значение видно в FORMATTED и UNFORMATTED, а
    в FORMULA-чтении ячейка пуста. Запись поверх неё в Google ломает формулу
    у всего вывода (#REF!); фальшивка этого не моделирует и такую запись
    отклоняет AssertionError. Очистка её не трогает: своего значения, которое
    можно стереть, у неё нет.
    """

    value: str | int | float


UNEVALUATED = "#ERROR!"
"""Что фальшивка читает из ячейки с формулой без заданного значения."""

Cell = str | int | float | Formula | Spill
"""Содержимое ячейки фальшивого листа. Пустая ячейка — пустая строка."""

FORMATS = ("0%", "0.00%", ";;;")
"""Числовые форматы, которые фальшивка показывает в FORMATTED-чтении:
процент целым и с двумя знаками — так оформлены потери в ING, — и «;;;»,
который прячет значение целиком. Прочие числа — как есть, с запятой."""

_SHEET_IDS = itertools.count(1001)
"""Числовые id листов (sheetId) — разные у всех фальшивых листов."""


class FakeWorksheet:
    """Дублёр листа.

    ``get_all_values`` отдаёт строки **без хвостовых пустых ячеек**: лист на
    двадцать колонок отдаёт строку длиной в три, если остальное пусто. Это
    строже gspread 6.2 — его ``get_all_values`` выравнивает строки до
    прямоугольника (``fill_gaps``). Рваные строки в жизни приходят из
    ``values_batch_get`` — им читает листы и импорт, и писатель строки. Код,
    написанный в расчёте на прямоугольник, падает на них с IndexError — или,
    хуже, читает не ту колонку, — и фальшивка ловит это на любом пути чтения.

    Сетка листа конечна, как у настоящего: ``row_count`` строк (по умолчанию
    не меньше 1000 — столько у нового листа Google) и ``col_count`` колонок
    (не меньше 26, A–Z). За её краем Google не пишет и не читает.
    ``row_count`` — снимок, как в gspread: у этого объекта — на момент
    создания, у листа из ``worksheet()``/``worksheets()`` — на момент
    открытия (см. :class:`OpenedWorksheet`).

    ``formats`` — числовой формат ячеек (:data:`FORMATS`) по адресам A1 —
    ячейке «Q7» или прямоугольнику «Q2:S10». Формат принадлежит ячейке, а не
    значению: запись и очистка его не меняют, вставка строк сдвигает его
    вместе со строками — как у Google.
    """

    def __init__(
        self,
        values: Sequence[Sequence[Cell]],
        title: str = "",
        *,
        row_count: int | None = None,
        col_count: int | None = None,
        formats: Mapping[str, str] | None = None,
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
        self._formats: dict[tuple[int, int], str] = {}
        for where, pattern in (formats or {}).items():
            self.set_format(where, pattern)

    def get_all_values(self) -> Cells:
        self.reads += 1
        return [
            _trim([_formatted(cell, self._formats.get((r, c))) for c, cell in enumerate(row)])
            for r, row in enumerate(self._cells)
        ]

    # --- для тестов: мимо Sheets API, запросов не добавляет ------------------
    def cell(self, where: str) -> Cell:
        """Содержимое ячейки «L7» как есть: формула — :class:`Formula`, вывод
        QUERY — :class:`Spill`."""
        parsed = _parse_cells(where)
        assert parsed is not None and parsed[1], f"ячейка — это «L7», а не «{where}»"
        box = parsed[0]
        return self._at(box.top, box.left)

    def put(self, where: str, cell: Cell) -> None:
        """Положить в ячейку «L7» что угодно — формулу, вывод QUERY, число:
        так тест собирает лист, каким его оставил шеф."""
        parsed = _parse_cells(where)
        assert parsed is not None and parsed[1], f"ячейка — это «L7», а не «{where}»"
        box = parsed[0]
        self._put(box.top, box.left, cell)

    def set_format(self, where: str, pattern: str | None) -> None:
        """Оформить ячейку или прямоугольник; ``None`` — снять формат."""
        assert pattern is None or pattern in FORMATS, f"фальшивка не знает формат «{pattern}»"
        parsed = _parse_cells(where)
        assert parsed is not None, f"адрес — «Q7» или «Q2:S10», а не «{where}»"
        box = parsed[0]
        for r in range(box.top, box.bottom + 1):
            for c in range(box.left, box.right + 1):
                if pattern is None:
                    self._formats.pop((r, c), None)
                else:
                    self._formats[(r, c)] = pattern

    def _open(self) -> OpenedWorksheet:
        """Лист открыли заново: новый объект со свежим снимком свойств."""
        return OpenedWorksheet(self)

    def _at(self, row: int, column: int) -> Cell:
        line = self._cells[row] if row < len(self._cells) else []
        return line[column] if column < len(line) else ""

    def _read(
        self, box: _Box | None, render: Callable[[Cell, str | None], object]
    ) -> list[list[object]]:
        """Значения прямоугольника (или всего листа) так, как их отдаёт Google:
        без хвостовых пустых ячеек в строке и без пустых строк в конце."""
        rows: list[list[object]] = []
        top = 0 if box is None else box.top
        bottom = len(self._cells) if box is None else min(box.bottom + 1, len(self._cells))
        for r in range(top, bottom):
            line = self._cells[r]
            columns = range(len(line)) if box is None else range(box.left, box.right + 1)
            rows.append(_trim([render(self._at(r, c), self._formats.get((r, c))) for c in columns]))
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
                inside = box is None or (box.top <= r <= box.bottom and box.left <= c <= box.right)
                if inside and not isinstance(line[c], Spill):
                    line[c] = ""

    def _insert_rows(self, index: int, count: int) -> None:
        """Вставить ``count`` пустых строк перед строкой с индексом ``index``
        (с нуля): всё ниже съезжает вместе с оформлением, сетка растёт — как
        у Google. Кроме вывода формулы-массива (:meth:`_move_rows`)."""
        self._move_rows(index, count)
        self._grid_rows += count

    def _delete_rows(self, index: int, count: int) -> None:
        """Удалить ``count`` строк, начиная с индекса ``index`` (с нуля): всё
        ниже подтягивается вверх вместе с оформлением, сетка сжимается."""
        self._move_rows(index, -count)
        self._grid_rows -= count

    def _array_anchor(self) -> int | None:
        """Индекс строки якоря формулы-массива: формула прямо над верхней
        ячейкой вывода в её колонке или сама эта строка (вывод в соседних
        колонках начинается на строке якоря). Вывода нет — ``None``."""
        tops: dict[int, int] = {}
        for r, line in enumerate(self._cells):
            for c, cell in enumerate(line):
                if isinstance(cell, Spill):
                    tops.setdefault(c, r)
        if not tops:
            return None
        return min(r - 1 if isinstance(self._at(r - 1, c), Formula) else r for c, r in tops.items())

    def _move_rows(self, index: int, shift: int) -> None:
        """Сдвинуть строки от индекса ``index`` на ``shift`` (вниз — вставка,
        вверх — удаление строк ``index … index−shift−1``).

        Как в Google: вывод формулы-массива (:class:`Spill`) привязан к её
        якорю. Вставка или удаление ниже якоря сдвигает ручные ячейки и
        формулы строк, а вывод остаётся на своих номерах строк. Вставка выше
        якоря сдвигает и якорь, и весь вывод. Ручная ячейка, съехавшая на
        место вывода, в Google сломала бы формулу (#REF!) — фальшивка этого
        не моделирует: AssertionError.
        """
        anchor = self._array_anchor()
        deleted = range(index, index - shift) if shift < 0 else range(0)
        if anchor is None or index <= anchor:
            # Вывода нет или правка выше якоря — съезжает всё.
            if shift < 0:
                del self._cells[index : index - shift]
            elif index < len(self._cells):
                self._cells[index:index] = [[] for _ in range(shift)]
        else:
            placed: dict[tuple[int, int], Cell] = {}
            moving: list[tuple[int, int, Cell]] = []
            for r, line in enumerate(self._cells):
                for c, cell in enumerate(line):
                    if isinstance(cell, Spill):
                        placed[(r, c)] = cell
                    elif r not in deleted:
                        moving.append((r + shift if r >= index else r, c, cell))
            for r, c, cell in moving:
                if (r, c) in placed:
                    assert cell == "", f"ручная ячейка съехала на вывод QUERY: {cell!r}"
                elif cell != "":
                    placed[(r, c)] = cell
            self._cells = [[] for _ in range(max(len(self._cells) + shift, 0))]
            for (r, c), cell in sorted(placed.items()):
                self._put(r, c, cell)
        self._formats = {
            (r + shift if r >= index else r, c): pattern
            for (r, c), pattern in self._formats.items()
            if r not in deleted
        }


class OpenedWorksheet:
    """Лист, как его отдаёт gspread при открытии.

    Каждое открытие — новый объект со своим снимком свойств (``title``,
    ``row_count``), а ячейки и сетка общие с исходным листом. Поэтому у
    ранее открытого листа число строк не меняется ни от ``appendDimension``,
    ни от нового открытия — как у объекта gspread, который держит свойства,
    прочитанные при открытии.
    """

    def __init__(self, sheet: FakeWorksheet) -> None:
        self._sheet = sheet
        self.title = sheet.title
        self.id = sheet.id
        self.row_count = sheet._grid_rows

    def get_all_values(self) -> Cells:
        return self._sheet.get_all_values()


@dataclass(frozen=True, slots=True)
class _Failure:
    """Заказанный тестом отказ одного вызова."""

    applied: bool
    """Успел ли Google сделать своё до того, как вызов упал."""

    error: Exception


_METHODS = (
    "worksheet",
    "worksheets",
    "values_batch_get",
    "values_batch_update",
    "values_batch_clear",
    "batch_update",
)
"""Вызовы Sheets API, которым тест может заказать отказ. Открытие листа —
тоже запрос, и у писателя он первый."""

_MOMENTS = ("before_write", "after_write", "after_clear", "now")
"""Когда шеф успевает что-то сделать в листе.

``before_write`` — между нашим чтением листа и записью: срабатывает, когда
приходит следующий ``values_batch_update``, до того как он что-то сделал.
``after_write`` — сразу после того, как запись легла, до её перечитывания
(и до ответа: при отказе «после применения» — тоже). ``after_clear`` —
сразу после того, как легла очистка. ``now`` — сразу, между нашими
вызовами: так шеф правит лист часы спустя, пока прежняя попытка записи
висит незавершённой."""


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
    * формулы не вычисляются — читаются как «#ERROR!» или как значение,
      которое задал тест (:class:`Formula`);
    * запись поверх вывода формулы-массива (:class:`Spill`) — AssertionError:
      в Google она сломала бы формулу у всего вывода, а фальшивка этого не
      моделирует;
    * лист русский, но из форматов фальшивка знает только :data:`FORMATS`:
      без формата FORMATTED_VALUE пишет число как есть, с запятой («12,5»), а
      в жизни колонка с форматом покажет «12,50», округлит или разобьёт
      разряды («1 030»);
    * USER_ENTERED понимает целые и числа с запятой; то, что Google
      угадал бы как дату, процент, дробь с точкой или число с пробелами
      («12.5», «5%», «01.02.2026», «1 030»), — AssertionError, а не текст;
    * ``updatedRange`` в ответе записи повторяет запрошенный диапазон, а не
      нормализует его, как Google;
    * ``get_all_values`` отдаёт рваные строки — без хвостовых пустых ячеек,
      как пакетное чтение, — а gspread 6.2 выравнивает их до прямоугольника
      (``fill_gaps``); код обязан переживать обе формы;
    * ``get_all_values`` оставляет пустые строки в конце листа (пакетное
      чтение их, как и Google, отрезает);
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
        # Что шеф сделает в листе и когда (см. _MOMENTS): по порядку заказа.
        self._chef: list[tuple[str, Callable[[], None]]] = []

    # --- что заказывает тест ------------------------------------------------
    def fail_next(self, method: str, *, applied: bool, error: Exception | None = None) -> None:
        """Следующий вызов ``method`` упадёт; отказ одноразовый.

        ``applied=False`` — Google отказал, ничего не сделав (по умолчанию
        503). ``applied=True`` — Google сделал своё, а ответ потерялся по
        дороге (по умолчанию таймаут чтения): исключение есть, а изменение
        в листе уже лежит. Уронить можно и открытие листа — ``worksheet``,
        ``worksheets``.
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
        self.chef_edits_cell(cell, value, moment="after_write")

    def chef_edits_cell(self, cell: str, value: str, *, moment: str) -> None:
        """Шеф вписал ``value`` в одну ячейку ``cell`` — в окне ``moment``.

        Правка одноразовая и срабатывает на ближайшем подходящем вызове (см.
        :data:`_MOMENTS`): до записи её значение наша запись может затереть,
        после записи — сверка увидит чужую ячейку.
        """
        assert moment in _MOMENTS, f"шеф не действует в окне «{moment}»: есть {_MOMENTS}"
        sheet, box, anchor = self._target(cell)
        assert box is not None and anchor, f"вписать можно в одну ячейку, а не в «{cell}»"
        top, left = box.top, box.left
        self._order(moment, lambda: sheet._put(top, left, value))

    def chef_inserts_rows(self, title: str, above: int, count: int = 1, *, moment: str) -> None:
        """Шеф вставил ``count`` пустых строк над строкой ``above`` листа
        ``title`` — в окне ``moment``.

        Всё, что было в строке ``above`` и ниже, съезжает на ``count`` строк
        вниз, сетка листа растёт — как от «Вставить строку выше» в Google.
        Номера строк, которые писатель запомнил при чтении, после этого
        указывают на чужое.
        """
        assert moment in _MOMENTS, f"шеф не действует в окне «{moment}»: есть {_MOMENTS}"
        assert title in self._sheets, f"листа «{title}» нет"
        assert above >= 1, f"строки считаются с единицы, а не «{above}»"
        assert count >= 1, f"вставить можно хотя бы одну строку, а не «{count}»"
        sheet = self._sheets[title]
        assert above <= sheet._grid_rows, f"строки {above} нет в сетке листа «{title}»"
        self._order(moment, lambda: sheet._insert_rows(above - 1, count))

    def chef_deletes_rows(self, title: str, row: int, count: int = 1, *, moment: str) -> None:
        """Шеф удалил ``count`` строк, начиная со строки ``row`` листа
        ``title``, — в окне ``moment``. Всё ниже подтягивается вверх, сетка
        сжимается; вывод формулы-массива ниже её якоря остаётся на своих
        номерах строк — как в Google."""
        assert moment in _MOMENTS, f"шеф не действует в окне «{moment}»: есть {_MOMENTS}"
        assert title in self._sheets, f"листа «{title}» нет"
        assert row >= 1, f"строки считаются с единицы, а не «{row}»"
        assert count >= 1, f"удалить можно хотя бы одну строку, а не «{count}»"
        sheet = self._sheets[title]
        assert row + count - 1 <= sheet._grid_rows, f"строк {row}… нет в сетке листа «{title}»"
        self._order(moment, lambda: sheet._delete_rows(row - 1, count))

    def _order(self, moment: str, action: Callable[[], None]) -> None:
        if moment == "now":
            action()
        else:
            self._chef.append((moment, action))

    def chef_waiting(self) -> list[str]:
        """Окна заказанных, но ещё не сработавших правок шефа.

        Правка, которая так и не сработала, значит, что тест проверял не то,
        что думал: пустой список — все правки случились."""
        return [moment for moment, _ in self._chef]

    # --- Sheets API ---------------------------------------------------------
    def worksheet(self, title: str) -> OpenedWorksheet:
        failure = self._sent("worksheet", title)
        sheet = self._sheets.get(title)
        if sheet is None:
            raise WorksheetNotFound(title)
        opened = sheet._open()
        if failure is not None:
            raise failure.error
        return opened

    def worksheets(self) -> list[OpenedWorksheet]:
        failure = self._sent("worksheets", None)
        opened = [sheet._open() for sheet in self._sheets.values()]
        if failure is not None:
            raise failure.error
        return opened

    def values_batch_get(
        self, ranges: list[str], params: dict[str, str] | None = None
    ) -> dict[str, object]:
        """Дублёр `values.batchGet`.

        Воспроизводит особенности настоящего ответа: диапазоны приходят В ТОМ
        ЖЕ ПОРЯДКЕ, что и запрос; хвостовые пустые ячейки и пустые строки в
        конце не приезжают, а у пустого диапазона ключа `values` нет вовсе.

        И особенность gspread 6.2: ``ranges`` он дописывает прямо в
        переданный словарь ``params`` — словарь вызывающего меняется,
        неизменяемое отображение падает ``TypeError``.
        """
        if params is None:
            params = {}
        params["ranges"] = ranges  # как в gspread 6.2, http_client.values_batch_get
        options = {key: value for key, value in params.items() if key != "ranges"}
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
        # Шеф успел раньше, чем запрос дошёл до Google, — и при отказе тоже:
        # его правка от нашего запроса не зависит.
        self._apply_chef("before_write")
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
            for target, r, c, _ in cells:
                assert not isinstance(target._at(r, c), Spill), (
                    f"запись поверх вывода QUERY в «{text}»: в Google формула сломалась бы "
                    "у всего вывода (#REF!)"
                )
            planned.extend(cells)
            responses.append({"updatedRange": text, "updatedCells": len(cells)})

        for sheet, row_index, column_index, cell in planned:
            sheet._put(row_index, column_index, cell)
        self._apply_chef("after_write")
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
        self._apply_chef("after_clear")
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

    def _apply_chef(self, moment: str) -> None:
        """Сделать то, что шеф заказал на это окно, — по порядку заказа."""
        now = [action for when, action in self._chef if when == moment]
        self._chef = [(when, action) for when, action in self._chef if when != moment]
        for action in now:
            action()


class FakeSheetsClient:
    """Источник таблиц, не ходящий в сеть.

    Как ``GspreadClient``: вид с другими таймаутами — тот же источник (тест
    видит, какие таймауты просили), ``close`` — закрыть сессию (тест видит,
    что закрыли)."""

    def __init__(self, spreadsheets: dict[str, FakeSpreadsheet]) -> None:
        self._spreadsheets = spreadsheets
        self.opened: list[str] = []
        self.timeouts: list[tuple[int, int]] = []
        self.closed = 0

    def open(self, spreadsheet_id: str) -> FakeSpreadsheet:
        self.opened.append(spreadsheet_id)
        return self._spreadsheets[spreadsheet_id]

    def with_timeout(self, timeout: tuple[int, int]) -> FakeSheetsClient:
        self.timeouts.append(timeout)
        return self

    def close(self) -> None:
        self.closed += 1


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
_GUESSED = re.compile(r"\s*[-+]?\d[\d.,/: -]*%?\s*")
"""Похоже на число, дату или процент, но не целое и не число с запятой:
«12.5», «5%», «01.02.2026», «1 030». Что из этого сделает Google в русском
листе, фальшивка не моделирует."""


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
    if _GUESSED.fullmatch(value):
        raise AssertionError(
            f"фальшивка не знает, во что USER_ENTERED превратит «{value}»: "
            f"Google в русском листе угадал бы дату, процент или число"
        )
    return value


def _formatted(cell: Cell, pattern: str | None = None) -> str:
    """FORMATTED_VALUE — как видит шеф: число с запятой, лист русский;
    проценты и «;;;» — по формату ячейки (:data:`FORMATS`)."""
    value = _unformatted(cell)
    if pattern == ";;;":
        return ""
    if isinstance(value, str):
        return value
    if pattern in ("0%", "0.00%"):
        places = 0 if pattern == "0%" else 2
        share = (Decimal(repr(value)) * 100).quantize(
            Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
        )
        return f"{share}%".replace(".", ",")
    return str(value).replace(".", ",")


def _unformatted(cell: Cell, pattern: str | None = None) -> str | int | float:
    """UNFORMATTED_VALUE — число числом; целое Google отдаёт без «.0»."""
    if isinstance(cell, Formula):
        value: str | int | float = UNEVALUATED if cell.value is None else cell.value
    elif isinstance(cell, Spill):
        value = cell.value
    else:
        value = cell
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _formula(cell: Cell, pattern: str | None = None) -> str | int | float:
    """FORMULA — формула текстом, значения без оформления (числа числами), а
    ячейка, которую заполнила чужая формула-массив, пуста."""
    if isinstance(cell, Formula):
        return cell.text
    if isinstance(cell, Spill):
        return ""
    return _unformatted(cell)


def _renderer(params: Mapping[str, str]) -> Callable[[Cell, str | None], object]:
    unknown = set(params) - {"valueRenderOption", "majorDimension"}
    assert not unknown, f"фальшивка не моделирует параметры чтения {sorted(unknown)}"
    assert params.get("majorDimension", "ROWS") == "ROWS", "фальшивка читает только строками"
    option = params.get("valueRenderOption", "FORMATTED_VALUE")
    if option == "FORMATTED_VALUE":
        return _formatted
    if option == "UNFORMATTED_VALUE":
        return _unformatted
    if option == "FORMULA":
        return _formula
    raise AssertionError(f"фальшивка не моделирует valueRenderOption={option}")


def _trim(row: list[object]) -> list[object]:
    """Строка без хвостовых пустых ячеек — так их отдаёт Google."""
    end = len(row)
    while end and row[end - 1] == "":
        end -= 1
    return row[:end]


# ---------------------------------------------------------------------------
# Книга кухни с листом ING и книга карточек — как настоящие (этап 6)
# ---------------------------------------------------------------------------
QUERY_B = "=QUERY('Импорт карточек'!A3:V;\"select A, B, C where V = 'Да'\";0)"
"""Первая формула зоны: категория, название и полное название каждой
карточки «Да» — в B–D."""

QUERY_F = "=QUERY('Импорт карточек'!A3:V;\"select E, G, H, I, J, K where V = 'Да'\";0)"
"""Вторая: изготовитель, состав и КБЖУ — в F–K."""


@dataclass(frozen=True, slots=True)
class IngLine:
    """Карточка «Да» и её строка в зоне QUERY листа ING."""

    name: str
    ref_id: int | None = None
    """id в A. ``None`` — строка ждёт переноса: заготовка шефа с умолчаниями."""
    l_formula: bool = False
    """L — формула от M, как в части строк настоящего листа."""
    category: str = "Соусы"


ING_OLD: tuple[tuple[int, str], ...] = ((1, "Лук"), (99, "Сахар"))
"""Старые строки над якорем — id и название, всё вписано руками. «Сахар» —
тёзка карточки из зоны QUERY: по названию он совпал бы, но это не вывод
формулы."""

ING_ZONE: tuple[IngLine, ...] = (
    IngLine("Кетчуп", ref_id=129),
    IngLine("Моцарелла", ref_id=130, l_formula=True, category="Сыры"),
    IngLine("Соус Барбекю"),
    IngLine("Соус Сырный", l_formula=True),
    IngLine("Сахар", category="Бакалея"),
)
"""Зона QUERY с 4-й строки: две перенесённые карточки и три, ждущие переноса."""

CARDS_ORDER: tuple[tuple[str, str], ...] = (
    ("Кетчуп", APPROVED),
    ("Майонез", REJECTED),
    ("Моцарелла", APPROVED),
    ("Соус Барбекю", APPROVED),
    ("Соус Сырный", APPROVED),
    ("Сахар", APPROVED),
    ("Горчица", ""),
)
"""Книга карточек: название и «Согласован» по строкам с 3-й. Карточки «Да» по
порядку — ровно зона QUERY листа ING."""

_TEMPLATE: dict[str, Cell] = {"L": 0, "M": 0, "N": "кг", "Q": 0, "R": 0, "S": 0, "T": "активный"}
"""Умолчания строки-заготовки: A и E пусты, P — формула."""

_PERCENT = {"P": "0%", "Q": "0.00%", "R": "0%", "S": "0%"}
"""Потери в ING оформлены процентами: в ячейке доля, шеф видит «5,00%»."""


def ing_sheet(
    zone: Sequence[IngLine] = ING_ZONE,
    *,
    old: Sequence[tuple[int, str]] = ING_OLD,
    templates: int = 2,
    row_count: int | None = None,
) -> FakeWorksheet:
    """Лист ING, устроенный как настоящий.

    * Шапка; старые строки, вписанные руками целиком.
    * Зона QUERY: в B строки якоря — :data:`QUERY_B`, в F — :data:`QUERY_F`;
      B–D и F–K каждой карточки — их вывод: виден в FORMATTED, пуст в
      FORMULA. У перенесённой карточки ручные A, E, L, M, N, Q–T заполнены;
      у ждущей — заготовка (:data:`_TEMPLATE`). L — формула от M у отмеченных.
    * Ниже — заготовки без карточек.
    * P — формула ``SUM(Q+R+S)`` во всех строках; P, Q, R, S оформлены
      процентами. id в FORMULA- и UNFORMATTED-чтении — числа.

    ``row_count`` — строк в сетке листа (по умолчанию 1000): равное числу
    строк — последняя строка листа на краю сетки.
    """
    lines: list[list[Cell]] = [[c.expected_header for c in specs.INGREDIENTS.columns]]
    for ref_id, name in old:
        lines.append(
            _ing_line(
                len(lines) + 1,
                {"A": ref_id, "B": "Овощи", "C": name, "D": name, "E": name, "F": "Ферма"},
                {"H": 1, "I": 0.5, "J": 8, "K": 40, "L": 120, "M": 1200, "N": "кг"},
                {"Q": 0.05, "R": 0.1, "S": 0, "T": "активный"},
            )
        )
    for offset, line in enumerate(zone):
        number = len(lines) + 1
        anchor = offset == 0
        query: dict[str, Cell] = {
            "B": Formula(QUERY_B, line.category) if anchor else Spill(line.category),
            "C": Spill(line.name),
            "D": Spill(f"{line.name}, полное наименование"),
            "F": Formula(QUERY_F, "Завод") if anchor else Spill("Завод"),
            "G": Spill("по ТУ"),
            "H": Spill(1.5),
            "I": Spill(12.5),
            "J": Spill(3),
            "K": Spill(150),
        }
        manual: dict[str, Cell] = dict(_TEMPLATE)
        if line.ref_id is not None:
            manual.update({"A": line.ref_id, "E": line.name, "L": 250, "M": 1250, "Q": 0.05})
        if line.l_formula:
            price = manual["M"]
            assert isinstance(price, int)
            manual["L"] = Formula(f"=M{number}/5", price / 5)
        lines.append(_ing_line(number, query, manual))
    for _ in range(templates):
        lines.append(_ing_line(len(lines) + 1, _TEMPLATE))
    last = len(lines)
    formats = {f"{letter}2:{letter}{last}": pattern for letter, pattern in _PERCENT.items()}
    return FakeWorksheet(lines, "ING", row_count=row_count, formats=formats)


def _ing_line(number: int, *parts: Mapping[str, Cell]) -> list[Cell]:
    """Строка ING: ячейки по буквам и формула P шефа над Q, R, S."""
    line: list[Cell] = [""] * len(specs.INGREDIENTS.columns)
    for part in parts:
        for letter, cell in part.items():
            line[_column_index(letter)] = cell
    losses = [line[_column_index(letter)] for letter in "QRS"]
    total = sum(loss for loss in losses if isinstance(loss, int | float))
    line[_column_index("P")] = Formula(f"=SUM(Q{number}+R{number}+S{number})", total)
    return line


def cards_book_sheet(cards: Sequence[tuple[str, str]] = CARDS_ORDER) -> FakeWorksheet:
    """«Лист1» книги карточек: шапка в две строки, затем карточки —
    название (B) и «Согласован» (V)."""
    spec = specs.INGREDIENT_CARDS
    lines: list[list[Cell]] = [[c.expected_header for c in spec.columns], []]
    for name, approval in cards:
        line: list[Cell] = [""] * len(spec.columns)
        line[spec.column("category").index] = "Соусы"
        line[spec.column("name").index] = name
        line[spec.column("approval_status").index] = approval
        lines.append(line)
    return FakeWorksheet(lines, "Лист1")


def reference_client(
    *, ing: FakeWorksheet | None = None, cards: FakeWorksheet | None = None
) -> FakeSheetsClient:
    """Книга кухни с листом ING и книга карточек; ключи таблиц — как в
    ``tests.fake_sheets.IDS``."""
    return FakeSheetsClient(
        {
            "kitchen-id": FakeSpreadsheet({"ING": ing if ing is not None else ing_sheet()}),
            "cards-id": FakeSpreadsheet(
                {"Лист1": cards if cards is not None else cards_book_sheet()}
            ),
        }
    )


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
