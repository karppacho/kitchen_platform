"""Чтение листов по описанию из :mod:`kitchen.sync.specs`.

Читатель делает три вещи, каждая из которых уже была причиной проблем:

1. **Сверяет заголовки.** Молчаливый сдвиг колонок — худший из возможных
   отказов: цена начинает читаться из поля веса, расчёт продолжает
   работать, и числа выглядят правдоподобно. Поэтому расхождение
   заголовков возвращается наверх, а не пропускается.

2. **Помнит прежние имена листов.** «Способы приготовления» когда-то
   назывался «Впитывание масла».

3. **Считает хеш строки.** Он понадобится синхронизации для защиты от
   петли: если пришло ровно то, что мы сами записали, применять это
   обратно нельзя.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from kitchen.domain.money import parse_decimal, parse_percent
from kitchen.sync.client import SheetNotFoundError
from kitchen.sync.ownership import Kind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kitchen.sync.client import Cells, SheetsClient
    from kitchen.sync.ownership import SheetSpec

CellValue = str | Decimal | int | None
"""Что может оказаться в разобранной ячейке.

Явный союз вместо ``Any``: строгий mypy в этом пакете запрещает ``Any``, и
это к лучшему — вызывающий код обязан знать, что он получил.
"""


@dataclass(frozen=True, slots=True)
class Row:
    """Одна строка листа."""

    number: int
    """Номер строки в листе, с единицы — как его видит человек.

    Хранится, чтобы диагностика указывала на строку, которую шеф может
    открыть и посмотреть, а не на индекс в массиве.
    """

    values: Mapping[str, CellValue]
    content_hash: str
    """SHA-256 сырых значений. Основа защиты от петли синхронизации."""

    def __getitem__(self, field: str) -> CellValue:
        return self.values[field]


@dataclass(frozen=True, slots=True)
class SheetData:
    """Прочитанный лист."""

    spec: SheetSpec
    title: str
    """Фактическое имя листа: может отличаться от ожидаемого, если
    сработал fallback."""

    rows: tuple[Row, ...]

    header_issues: tuple[str, ...] = ()
    """Расхождения заголовков.

    Не исключение: остановить импорт из-за переименованной колонки было бы
    слишком грубо. Но и промолчать нельзя — список уходит в отчёт.
    """

    @property
    def ok(self) -> bool:
        return not self.header_issues

    def __len__(self) -> int:
        return len(self.rows)


def _cell(raw: Cells, row_index: int, column_index: int) -> str:
    """Значение ячейки с поправкой на рваные строки.

    gspread не возвращает хвостовые пустые ячейки, поэтому строка из листа
    на двадцать колонок может приехать длиной в три.
    """
    row = raw[row_index]
    if column_index >= len(row):
        return ""
    return row[column_index]


def _convert(value: str, kind: Kind) -> CellValue:
    """Разобрать ячейку по её виду.

    Пустое даёт разное в зависимости от вида, и это осознанно:

    * текст → ``""``. Пустая ячейка и стёртая ячейка в листе неотличимы, и
      обе означают «текста нет». ``None`` намекал бы на «неизвестно» и
      заставлял бы проверять на него весь код ниже.
    * число → ``None``. Здесь различие принципиально: «потерь 0%» и «потери
      не заполнены» — разные утверждения, и второе обязано дойти до
      предупреждения, а не раствориться нулём в расчёте.
    """
    text = value.strip()
    if kind is Kind.TEXT:
        return text
    if kind is Kind.DECIMAL:
        return parse_decimal(text)
    if kind is Kind.PERCENT:
        return parse_percent(text)
    # Kind.INT
    parsed = parse_decimal(text)
    return int(parsed) if parsed is not None else None


def _hash(cells: list[str]) -> str:
    # Разделитель, которого не бывает в ячейках, — иначе «a|b» и «a», «b»
    # дали бы один хеш.
    joined = "\x1f".join(cells)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class SheetsReader:
    """Читает листы по их описанию."""

    def __init__(self, client: SheetsClient, spreadsheet_ids: Mapping[str, str]) -> None:
        self._client = client
        self._ids = spreadsheet_ids

    def read(self, spec: SheetSpec, *, title: str | None = None) -> SheetData:
        """Прочитать лист.

        ``title`` перекрывает имя из описания — нужен листам дегустаций,
        которые называются датой.
        """
        spreadsheet_id = self._ids.get(spec.spreadsheet)
        if not spreadsheet_id:
            raise KeyError(
                f"Не задан идентификатор таблицы «{spec.spreadsheet}» — "
                f"проверьте переменные SHEETS_ID_* в окружении"
            )

        spreadsheet = self._client.open(spreadsheet_id)
        wanted = title or spec.title
        candidates = (wanted, *spec.fallback_titles)

        for candidate in candidates:
            try:
                worksheet = spreadsheet.worksheet(candidate)
            except Exception as error:
                if not _looks_missing(error):
                    raise
                continue
            return self._parse(spec, candidate, worksheet.get_all_values())

        raise SheetNotFoundError(
            f"В таблице «{spec.spreadsheet}» нет листа «{wanted}»"
            + (
                f" (пробовал также: {', '.join(spec.fallback_titles)})"
                if spec.fallback_titles
                else ""
            )
        )

    def _parse(self, spec: SheetSpec, title: str, raw: Cells) -> SheetData:
        issues = _check_header(spec, raw)

        rows: list[Row] = []
        for index in range(spec.header_rows, len(raw)):
            cells = [_cell(raw, index, column.index) for column in spec.columns]
            # Полностью пустые строки в листах встречаются в изобилии:
            # шеф оставляет промежутки между блоками.
            if not any(cell.strip() for cell in cells):
                continue

            values: dict[str, CellValue] = {
                column.field: _convert(cell, column.kind)
                for column, cell in zip(spec.columns, cells, strict=True)
            }
            rows.append(
                Row(
                    number=index + 1,
                    values=values,
                    content_hash=_hash(cells),
                )
            )

        return SheetData(spec=spec, title=title, rows=tuple(rows), header_issues=issues)


def _check_header(spec: SheetSpec, raw: Cells) -> tuple[str, ...]:
    """Сверить шапку с описанием.

    Сравнение нестрогое: регистр и лишние пробелы шеф правит регулярно, и
    падать из-за них было бы навязчиво. А вот другой текст в колонке —
    признак того, что таблица переехала.
    """
    if not raw:
        return ("лист пуст",)

    header_index = spec.header_rows - 1
    if header_index >= len(raw):
        return (f"нет строки заголовков (ожидалась строка {spec.header_rows})",)

    issues: list[str] = []
    for column in spec.columns:
        actual = _cell(raw, header_index, column.index)
        if _normalise(actual) != _normalise(column.title):
            issues.append(
                f"колонка {column.letter}: ожидался заголовок «{column.title}», в листе «{actual}»"
            )
    return tuple(issues)


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _looks_missing(error: Exception) -> bool:
    """Похоже ли исключение на «листа нет».

    Проверяем по имени класса, чтобы не тащить gspread в импорты ради
    одного типа: модуль обязан оставаться пригодным к офлайн-тестам.
    """
    names = {type(error).__name__ for error in (error,)} | {
        base.__name__ for base in type(error).__mro__
    }
    return "WorksheetNotFound" in names or isinstance(error, SheetNotFoundError | KeyError)
