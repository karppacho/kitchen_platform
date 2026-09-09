"""Чтение листов.

Проверяется в первую очередь то, что ломается молча: рваные строки,
переехавшие колонки, переименованный лист. Отказ с исключением заметен
сразу; неверно прочитанное число живёт в отчётах неделями.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from kitchen.sync import specs
from kitchen.sync.client import SheetNotFoundError
from kitchen.sync.reader import SheetsReader
from tests.conftest import ExplodingSpreadsheet, FakeSheetsClient

IDS = {
    "kitchen": "kitchen-id",
    "ingredient_cards": "cards-id",
    "competitors": "competitors-id",
    "tastings": "tastings-id",
}

# Шапка ING целиком — берётся из описания, чтобы тест не разъезжался с ним.
ING_HEADER = [c.expected_header for c in specs.INGREDIENTS.columns]


def _ing_row(**overrides: str) -> list[str]:
    row = dict.fromkeys((c.field for c in specs.INGREDIENTS.columns), "")
    row.update(overrides)
    return [row[c.field] for c in specs.INGREDIENTS.columns]


def _reader(client: FakeSheetsClient) -> SheetsReader:
    return SheetsReader(client, IDS)


# ---------------------------------------------------------------------------
# Разбор
# ---------------------------------------------------------------------------
def test_reads_rows_with_types(make_client) -> None:
    client = make_client(
        {
            "kitchen-id": {
                "ING": [
                    ING_HEADER,
                    _ing_row(
                        id="1", name="Томаты", price_per_kg="р.177,00", losses_cutting="11,16%"
                    ),
                ]
            }
        }
    )
    data = _reader(client).read(specs.INGREDIENTS)

    assert len(data) == 1
    row = data.rows[0]
    assert row["name"] == "Томаты"
    assert row["price_per_kg"] == Decimal("177.00")
    assert row["losses_cutting"] == Decimal("0.1116"), "проценты обязаны стать долей"
    assert row.number == 2, "номер строки — как в листе, чтобы шеф мог её открыть"


def test_survives_ragged_rows(make_client) -> None:
    """gspread не возвращает хвостовые пустые ячейки.

    Лист на двадцать колонок отдаёт строку длиной в три. Код, ждущий
    прямоугольник, здесь падает с IndexError — или читает не ту колонку.
    """
    client = make_client({"kitchen-id": {"ING": [ING_HEADER, ["7", "Овощи", "Огурцы"]]}})
    row = _reader(client).read(specs.INGREDIENTS).rows[0]

    assert row["name"] == "Огурцы"
    # Отсутствующая колонка читается как пустая — и пустое даёт разное по
    # виду поля: текст «», число None. Различие для чисел принципиально:
    # «потерь 0%» и «потери не заполнены» — разные утверждения.
    assert row["status"] == "", "текст: пустая и отсутствующая ячейка неотличимы"
    assert row["price_per_kg"] is None, "число: отсутствие обязано остаться отсутствием"
    assert row["losses_cutting"] is None


def test_skips_empty_rows(make_client) -> None:
    """Шеф оставляет промежутки между блоками — это не данные."""
    client = make_client(
        {
            "kitchen-id": {
                "ING": [
                    ING_HEADER,
                    _ing_row(id="1", name="Томаты"),
                    [],
                    ["", "", ""],
                    _ing_row(id="2", name="Лук"),
                ]
            }
        }
    )
    data = _reader(client).read(specs.INGREDIENTS)

    assert [r["name"] for r in data.rows] == ["Томаты", "Лук"]
    assert [r.number for r in data.rows] == [2, 5], "номера строк остаются настоящими"


def test_pricing_data_starts_below_two_header_rows(make_client) -> None:
    """Строка 1 — подсказки коммерсам, строка 2 — шапка."""
    hint = ["", "⬇️ тут ставим цену ⬇️", "тут не трогать — считает бот"]
    header = [c.expected_header for c in specs.PRICING_NEW.columns]
    client = make_client(
        {"kitchen-id": {"Расчётка новинки": [hint, header, ["Ролл гриль", "280", "149,08"]]}}
    )
    data = _reader(client).read(specs.PRICING_NEW)

    assert len(data) == 1
    assert data.rows[0]["name"] == "Ролл гриль"
    assert data.rows[0]["price_sale"] == Decimal("280")
    assert data.rows[0].number == 3


# ---------------------------------------------------------------------------
# Сверка заголовков: защита от переехавшей таблицы
# ---------------------------------------------------------------------------
def test_clean_header_has_no_issues(make_client) -> None:
    client = make_client({"kitchen-id": {"ING": [ING_HEADER]}})
    assert _reader(client).read(specs.INGREDIENTS).ok


def test_header_drift_is_reported_not_swallowed(make_client) -> None:
    """Сдвиг колонок — худший из отказов.

    Он не роняет расчёт: цена просто начинает читаться из поля веса, и
    числа выглядят правдоподобно. Поэтому расхождение обязано дойти до
    отчёта, а не потеряться.
    """
    broken = list(ING_HEADER)
    broken[11] = "Цена за 1 штуку"  # было «Цена за 1 кг»
    client = make_client({"kitchen-id": {"ING": [broken]}})

    data = _reader(client).read(specs.INGREDIENTS)

    assert not data.ok
    assert any("колонка L" in issue for issue in data.header_issues)
    assert any("Цена за 1 штуку" in issue for issue in data.header_issues)


def test_header_comparison_forgives_case_and_spaces(make_client) -> None:
    """Регистр и лишние пробелы шеф правит регулярно — падать из-за них навязчиво."""
    sloppy = ["  " + title.upper() + " " for title in ING_HEADER]
    client = make_client({"kitchen-id": {"ING": [sloppy]}})
    assert _reader(client).read(specs.INGREDIENTS).ok


def test_empty_sheet_is_reported(make_client) -> None:
    client = make_client({"kitchen-id": {"ING": []}})
    data = _reader(client).read(specs.INGREDIENTS)
    assert data.header_issues == ("лист пуст",)


# ---------------------------------------------------------------------------
# Имена листов
# ---------------------------------------------------------------------------
def test_falls_back_to_previous_sheet_name(make_client) -> None:
    """«Способы приготовления» когда-то назывался «Впитывание масла»."""
    header = [c.expected_header for c in specs.COOKING_METHODS.columns]
    client = make_client({"kitchen-id": {"Впитывание масла": [header, ["1", "фри", "жарка"]]}})

    data = _reader(client).read(specs.COOKING_METHODS)

    assert data.title == "Впитывание масла"
    assert len(data) == 1


def test_missing_sheet_names_what_was_tried(make_client) -> None:
    client = make_client({"kitchen-id": {"Другой лист": [[]]}})
    with pytest.raises(SheetNotFoundError, match="Впитывание масла"):
        _reader(client).read(specs.COOKING_METHODS)


def test_explicit_title_overrides_spec(make_client) -> None:
    """Листы дегустаций называются датой."""
    header = [c.expected_header for c in specs.TASTING_RATINGS.columns]
    client = make_client(
        {"tastings-id": {"05.05.2026": [header, ["1", "Иван", "Соус — Heinz", "8", "9"]]}}
    )
    data = _reader(client).read(specs.TASTING_RATINGS, title="05.05.2026")

    assert data.rows[0]["jury_name"] == "Иван"
    assert data.rows[0]["visual"] == 8


# ---------------------------------------------------------------------------
# Контракты
# ---------------------------------------------------------------------------
def test_reads_only_the_requested_spreadsheet(make_client) -> None:
    """Читатель не должен трогать чужие таблицы.

    Негативный контракт: обращение к любой другой таблице падает с
    объяснением, а не проходит незаметно.
    """
    client = make_client({"kitchen-id": {"ING": [ING_HEADER]}})
    client._spreadsheets["cards-id"] = ExplodingSpreadsheet()

    _reader(client).read(specs.INGREDIENTS)

    assert client.opened == ["kitchen-id"]


def test_unknown_spreadsheet_key_is_loud(make_client) -> None:
    reader = SheetsReader(make_client({}), {})
    with pytest.raises(KeyError, match="SHEETS_ID"):
        reader.read(specs.INGREDIENTS)


def test_content_hash_tracks_values_not_formatting(make_client) -> None:
    """Хеш — основа защиты от петли синхронизации.

    Одинаковое содержимое обязано давать одинаковый хеш, разное — разный.
    Иначе либо зациклимся на собственной записи, либо не заметим правку.
    """
    first = make_client({"kitchen-id": {"ING": [ING_HEADER, _ing_row(id="1", name="Томаты")]}})
    same = make_client({"kitchen-id": {"ING": [ING_HEADER, _ing_row(id="1", name="Томаты")]}})
    other = make_client({"kitchen-id": {"ING": [ING_HEADER, _ing_row(id="1", name="Томаты ")]}})

    a = _reader(first).read(specs.INGREDIENTS).rows[0].content_hash
    b = _reader(same).read(specs.INGREDIENTS).rows[0].content_hash
    c = _reader(other).read(specs.INGREDIENTS).rows[0].content_hash

    assert a == b
    assert a != c, "хвостовой пробел — тоже правка, и синхронизация обязана её увидеть"


def test_merged_two_row_header_is_understood(make_client) -> None:
    """Шапка карточек занимает две строки с объединёнными ячейками.

    В первой строке групповые названия («Пищевая и энергетическая ценность
    ингредиента»), во второй — подзаголовки под ними («белки», «жиры»). Для
    колонок вне групп вторая строка пуста, и значащий заголовок остаётся в
    первой. Читатель ищет снизу вверх; иначе половина колонок выглядела бы
    как «заголовок пропал», и настоящий переезд таблицы утонул бы в шуме.
    """
    spec = specs.INGREDIENT_CARDS
    group_row: list[str] = []
    sub_row: list[str] = []
    for column in spec.columns:
        # H..K и S..U — под групповыми названиями: подзаголовок во второй
        # строке, в первой стоит название группы.
        if column.letter in {"H", "I", "J", "K", "S", "T", "U"}:
            group_row.append("Пищевая ценность" if column.letter in {"H", "I", "J", "K"} else "Вид")
            sub_row.append(column.expected_header)
        else:
            group_row.append(column.expected_header)
            sub_row.append("")

    client = make_client({"cards-id": {"Лист1": [group_row, sub_row, _card_row()]}})
    data = _reader(client).read(spec)

    assert data.ok, f"расхождения: {data.header_issues}"
    assert len(data) == 1


def _card_row() -> list[str]:
    row = dict.fromkeys((c.field for c in specs.INGREDIENT_CARDS.columns), "")
    row["name"] = "Томаты"
    return [row[c.field] for c in specs.INGREDIENT_CARDS.columns]


# ---------------------------------------------------------------------------
# Пакетное чтение
# ---------------------------------------------------------------------------
def test_read_many_spends_two_requests_per_spreadsheet(make_client) -> None:
    """Экономия квоты — числом, а не на слово.

    Квота Google — 60 запросов в минуту на пользователя, и полный отчёт по
    листу за раз её выбирал за два прогона. На таблицу должно уходить два
    обращения: список листов и один batchGet, сколько бы листов ни читали.
    """
    header = [c.expected_header for c in specs.INGREDIENTS.columns]
    pack_header = [c.expected_header for c in specs.PACKAGING.columns]
    dish_header = [c.expected_header for c in specs.DISHES.columns]
    client = make_client(
        {
            "kitchen-id": {
                "ING": [header, _ing_row(id="1", name="Томаты")],
                "Упаковка": [pack_header, ["u1", "Коробка"]],
                "Блюда": [dish_header, ["B001", "Ролл"]],
            }
        }
    )

    data = _reader(client).read_many([specs.INGREDIENTS, specs.PACKAGING, specs.DISHES])

    assert len(data) == 3
    book = client._spreadsheets["kitchen-id"]
    assert book.requests == 2, f"на три листа ушло {book.requests} запросов вместо двух"


def test_read_many_returns_parsed_rows(make_client) -> None:
    header = [c.expected_header for c in specs.INGREDIENTS.columns]
    client = make_client(
        {"kitchen-id": {"ING": [header, _ing_row(id="1", name="Томаты", price_per_kg="177")]}}
    )

    data = _reader(client).read_many([specs.INGREDIENTS])
    sheet = data["kitchen/ING"]

    assert not isinstance(sheet, str)
    assert sheet.rows[0]["name"] == "Томаты"
    assert sheet.rows[0]["price_per_kg"] == Decimal("177")


def test_read_many_survives_missing_sheet(make_client) -> None:
    """Одна переименованная вкладка не должна лишать картины целиком."""
    header = [c.expected_header for c in specs.INGREDIENTS.columns]
    client = make_client({"kitchen-id": {"ING": [header, _ing_row(id="1", name="Томаты")]}})

    data = _reader(client).read_many([specs.INGREDIENTS, specs.DISHES])

    assert not isinstance(data["kitchen/ING"], str), "этот лист прочитан"
    assert data["kitchen/Блюда"] == "листа «Блюда» нет в таблице"


def test_read_many_uses_fallback_title(make_client) -> None:
    header = [c.expected_header for c in specs.COOKING_METHODS.columns]
    client = make_client({"kitchen-id": {"Впитывание масла": [header, ["1", "фри"]]}})

    sheet = _reader(client).read_many([specs.COOKING_METHODS])["kitchen/Способы приготовления"]

    assert not isinstance(sheet, str)
    assert sheet.title == "Впитывание масла"


def test_read_many_handles_empty_sheet(make_client) -> None:
    """У пустого листа Google не присылает ключ values вовсе."""
    client = make_client({"kitchen-id": {"ING": []}})

    sheet = _reader(client).read_many([specs.INGREDIENTS])["kitchen/ING"]

    assert not isinstance(sheet, str)
    assert len(sheet) == 0
    assert sheet.header_issues == ("лист пуст",)
