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
    from collections.abc import Mapping, Sequence

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

    def read_many(self, specs: Sequence[SheetSpec]) -> dict[str, SheetData | str]:
        """Прочитать пачку листов, тратя минимум запросов.

        На каждую таблицу уходит два обращения — список листов и один
        `values.batchGet` на все нужные диапазоны, — вместо одного запроса
        на лист. Квота Google 60 запросов в минуту на пользователя, и
        полный отчёт по-старому её выбирал за два прогона.

        Отказ по одному листу не рушит остальные: вместо данных в словарь
        кладётся строка с объяснением. Одна переименованная вкладка не
        должна лишать нас картины целиком.
        """
        by_book: dict[str, list[SheetSpec]] = {}
        for spec in specs:
            by_book.setdefault(spec.spreadsheet, []).append(spec)

        result: dict[str, SheetData | str] = {}
        for book_key, book_specs in by_book.items():
            self._read_book(book_key, book_specs, result)
        return result

    def _read_book(
        self,
        book_key: str,
        specs: Sequence[SheetSpec],
        result: dict[str, SheetData | str],
    ) -> None:
        spreadsheet_id = self._ids.get(book_key)
        if not spreadsheet_id:
            for spec in specs:
                result[_label(spec)] = (
                    f"не задан идентификатор таблицы «{book_key}» "
                    f"(переменные SHEETS_ID_* в окружении)"
                )
            return

        try:
            book = self._client.open(spreadsheet_id)
            existing = {sheet.title for sheet in book.worksheets()}
        except Exception as error:
            for spec in specs:
                result[_label(spec)] = f"не открылась таблица: {error}"
            return

        resolved: list[tuple[SheetSpec, str]] = []
        for spec in specs:
            title = next(
                (name for name in (spec.title, *spec.fallback_titles) if name in existing),
                None,
            )
            if title is None:
                result[_label(spec)] = f"листа «{spec.title}» нет в таблице"
                continue
            resolved.append((spec, title))

        if not resolved:
            return

        try:
            payload = book.values_batch_get([_quote_range(title) for _, title in resolved])
        except Exception as error:
            for spec, _ in resolved:
                result[_label(spec)] = f"не прочитан: {error}"
            return

        # Ответ Sheets API приходит нетипизированным, и сузить его надо
        # явно: strict в этом пакете запрещает Any, а доверять форме чужого
        # JSON без проверки — способ получить падение на пустом листе.
        blocks = payload.get("valueRanges")
        ranges: list[object] = blocks if isinstance(blocks, list) else []

        for index, (spec, title) in enumerate(resolved):
            if index >= len(ranges):
                result[_label(spec)] = "ответ Google короче запроса"
                continue
            result[_label(spec)] = self._parse(spec, title, _values_of(ranges[index]))

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

    if spec.header_rows > len(raw):
        return (f"нет строки заголовков (ожидалась строка {spec.header_rows})",)

    issues: list[str] = []
    for column in spec.columns:
        actual = _header_text(raw, spec.header_rows, column.index)
        expected = column.expected_header
        if _normalise(actual) != _normalise(expected):
            issues.append(
                f"колонка {column.letter}: ожидался заголовок «{expected}», в листе «{actual}»"
            )
    return tuple(issues)


def _header_text(raw: Cells, header_rows: int, column_index: int) -> str:
    """Заголовок колонки при многострочной шапке.

    Ищем снизу вверх. У листа карточек ингредиентов шапка занимает две
    строки с объединёнными ячейками: в первой групповые названия
    («Пищевая и энергетическая ценность ингредиента»), во второй —
    подзаголовки под ними («белки», «жиры»). Для колонок вне групп вторая
    строка пуста, и значащий заголовок остаётся в первой.
    """
    for row_index in range(header_rows - 1, -1, -1):
        value = _cell(raw, row_index, column_index).strip()
        if value:
            return value
    return ""


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


def _values_of(block: object) -> Cells:
    """Достать матрицу значений из одного valueRange.

    Пустой лист приезжает без ключа `values` вовсе — это не отказ, а
    нормальный ответ, и превращается он в пустую матрицу.
    """
    if not isinstance(block, dict):
        return []
    values = block.get("values")
    if not isinstance(values, list):
        return []
    return [[str(cell) for cell in row] if isinstance(row, list) else [] for row in values]


def _label(spec: SheetSpec) -> str:
    """Ключ листа в результатах: «таблица/лист»."""
    return f"{spec.spreadsheet}/{spec.title}"


def _quote_range(title: str) -> str:
    """Имя листа как диапазон для batchGet.

    Кавычки обязательны: имена вроде «Расчётка меню» и «История изменений»
    содержат пробелы, а без кавычек Sheets API разбирает их как ошибку
    синтаксиса диапазона. Одинарная кавычка внутри имени удваивается.
    """
    escaped = title.replace("'", "''")
    return f"'{escaped}'"
