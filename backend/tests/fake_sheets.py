"""Фальшивые таблицы для тестов импорта и синхронизации.

Одни и те же листы нужны офлайн-тестам цикла и интеграционным тестам
импорта; держать две копии значило бы однажды проверять разное.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kitchen.sync import specs
from tests.conftest import FakeSheetsClient, FakeSpreadsheet, FakeWorksheet

if TYPE_CHECKING:
    from kitchen.sync.ownership import SheetSpec

IDS = {"kitchen": "kitchen-id", "ingredient_cards": "cards-id"}


def header(spec: SheetSpec) -> list[str]:
    return [c.expected_header for c in spec.columns]


def row(spec: SheetSpec, **values: str) -> list[str]:
    cells = dict.fromkeys((c.field for c in spec.columns), "")
    cells.update(values)
    return [cells[c.field] for c in spec.columns]


def kitchen_sheets() -> dict[str, list[list[str]]]:
    """Книга кухни с разумным содержимым; каждый вызов — свежая копия."""
    return {
        "ING": [
            header(specs.INGREDIENTS),
            row(specs.INGREDIENTS, id="1", name="Томаты", price_per_kg="177", status="активное"),
            row(specs.INGREDIENTS, id="2", name="Сахар", price_per_kg="100", status="активное"),
            row(specs.INGREDIENTS, id="3", name="Сахар", price_per_kg="0", status="активное"),
        ],
        "Упаковка": [
            header(specs.PACKAGING),
            row(specs.PACKAGING, id="u1", name="Коробка", price_per_piece="12"),
        ],
        "Способы приготовления": [
            header(specs.COOKING_METHODS),
            row(specs.COOKING_METHODS, id="m1", method="Фритюр"),
        ],
        "Блюда": [
            header(specs.DISHES),
            row(specs.DISHES, id="B001", name="Ролл", price_menu="280", status="активное"),
        ],
        "ТТК": [
            header(specs.TTK),
            row(specs.TTK, dish_id="B001", ingredient_id="1", net_weight_g="100"),
            row(specs.TTK, dish_id="B001", packaging_id="u1", net_weight_g="0"),
        ],
    }


def cards_sheet() -> list[list[str]]:
    return [
        header(specs.INGREDIENT_CARDS),
        [""] * len(specs.INGREDIENT_CARDS.columns),
        row(specs.INGREDIENT_CARDS, name="Томаты", supplier="Поставщик"),
        row(specs.INGREDIENT_CARDS, name="Сахар"),
        row(specs.INGREDIENT_CARDS, name="Пастрами из индейки"),
    ]


def sheets_client(
    *,
    kitchen: dict[str, list[list[str]]] | None = None,
    cards: list[list[str]] | None = None,
    missing: tuple[str, ...] = (),
) -> FakeSheetsClient:
    """Фальшивые таблицы: `kitchen` заменяет листы кухни по имени, `cards` —
    лист карточек, `missing` — листы кухни, которых в таблице нет вовсе."""
    book = kitchen_sheets()
    book.update(kitchen or {})
    for title in missing:
        del book[title]
    return FakeSheetsClient(
        {
            "kitchen-id": FakeSpreadsheet({t: FakeWorksheet(v, t) for t, v in book.items()}),
            "cards-id": FakeSpreadsheet(
                {"Лист1": FakeWorksheet(cards if cards is not None else cards_sheet(), "Лист1")}
            ),
        }
    )
