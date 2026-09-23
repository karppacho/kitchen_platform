"""Решения цикла синхронизации по книгам — без базы и без сети."""

from __future__ import annotations

import pytest

from kitchen.sync import specs
from kitchen.sync.cycle import BOOKS, explain, judge
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetData, SheetsReader
from tests.conftest import FakeSheetsClient
from tests.fake_sheets import IDS, kitchen_sheets, row, sheets_client


def _read(client: FakeSheetsClient) -> dict[str, SheetData | str]:
    return SheetsReader(client, IDS).read_many(Importer.SPECS)


def test_books_cover_every_imported_sheet() -> None:
    """Лист, которого нет ни в одной книге, воркер не перенёс бы никогда."""
    in_books = sorted(spec.title for book in BOOKS.values() for spec in book)
    assert in_books == sorted(spec.title for spec in Importer.SPECS)


def test_readable_book_gets_fingerprint() -> None:
    verdict = judge("kitchen", _read(sheets_client()))

    assert verdict.problem is None
    assert verdict.fingerprint is not None and len(verdict.fingerprint) == 64


def test_fingerprint_follows_content() -> None:
    ing = kitchen_sheets()["ING"]
    ing[1] = row(specs.INGREDIENTS, id="1", name="Томаты", price_per_kg="170", status="активное")

    first = judge("kitchen", _read(sheets_client())).fingerprint
    again = judge("kitchen", _read(sheets_client())).fingerprint
    changed = judge("kitchen", _read(sheets_client(kitchen={"ING": ing}))).fingerprint

    assert first == again
    assert changed != first


def test_unread_column_does_not_wake_import() -> None:
    """Заметка в колонке, которую мы не импортируем, — не повод переносить книгу."""
    ing = kitchen_sheets()["ING"]
    ing[1] = [*ing[1], "заметка на полях"]

    assert (
        judge("kitchen", _read(sheets_client(kitchen={"ING": ing}))).fingerprint
        == judge("kitchen", _read(sheets_client())).fingerprint
    )


def test_shifted_columns_block_the_book() -> None:
    ing = kitchen_sheets()["ING"]
    ing[0] = list(ing[0])
    ing[0][1] = "Совсем другая колонка"

    verdict = judge("kitchen", _read(sheets_client(kitchen={"ING": ing})))

    assert verdict.fingerprint is None
    assert verdict.problem is not None
    assert "в листе «ING» сдвинулись колонки" in verdict.problem
    assert "Совсем другая колонка" in verdict.problem


def test_empty_sheet_is_named() -> None:
    verdict = judge("kitchen", _read(sheets_client(kitchen={"ТТК": []})))

    assert verdict.problem == "лист «ТТК» пуст"


def test_missing_sheet_blocks_the_book() -> None:
    verdict = judge("kitchen", _read(sheets_client(missing=("ТТК",))))

    assert verdict.problem is not None
    assert "листа «ТТК» нет в таблице" in verdict.problem


class QuotaSpreadsheet:
    """Таблица, на которую Google ответил отказом по квоте."""

    def worksheets(self) -> list[object]:
        raise RuntimeError("APIError: [429]: Quota exceeded for quota metric 'Read requests'")


def test_book_failure_is_explained_once_and_spares_other_book() -> None:
    """Отказ открытия приходит на каждый лист книги — причину говорим один раз.
    Карточки при этом читаются: книги независимы."""
    client = sheets_client()
    client._spreadsheets["kitchen-id"] = QuotaSpreadsheet()
    sheets = _read(client)

    kitchen = judge("kitchen", sheets)
    assert (
        kitchen.problem
        == "Google временно ограничил число запросов — следующая попытка через 5 минут"
    )
    assert judge("ingredient_cards", sheets).problem is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            "APIError: [403]: The caller does not have permission",
            "доступ платформы к таблице закрыт",
        ),
        (
            "ReadTimeout: HTTPSConnectionPool(host='sheets.googleapis.com'): Read timed out",
            "Google не ответил",
        ),
        ("листа «ТТК» нет в таблице", "листа «ТТК» нет в таблице"),
        ("что-то совсем новое", "не удалось прочитать лист «ING»: что-то совсем новое"),
    ],
)
def test_explain_speaks_plainly(error: str, expected: str) -> None:
    """Причину видят все, в том числе шеф: код исключения — не объяснение."""
    assert expected in explain("ING", error)
