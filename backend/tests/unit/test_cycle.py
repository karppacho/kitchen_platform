"""Решения цикла синхронизации по книгам — без базы и без сети."""

from __future__ import annotations

import pytest
import requests
from gspread.exceptions import APIError, SpreadsheetNotFound

from kitchen.sync import specs
from kitchen.sync.cycle import BOOKS, explain, judge
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetData, SheetsReader
from tests.conftest import FakeSheetsClient, FakeSpreadsheet, FakeWorksheet
from tests.fake_sheets import IDS, header, kitchen_sheets, row, sheets_client

ACCESS = "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"
NOT_FOUND = (
    "таблица не найдена — проверьте её идентификатор в настройках (SHEETS_ID_*) "
    "и доступ сервисного аккаунта"
)
NO_ANSWER = "Google не ответил — следующая попытка через 5 минут"


def _read(client: FakeSheetsClient) -> dict[str, SheetData | str]:
    return SheetsReader(client, IDS).read_many(Importer.SPECS)


def test_books_cover_every_imported_sheet() -> None:
    """Лист, которого нет ни в одной книге, воркер не перенёс бы никогда."""
    in_books = sorted(spec.title for book in BOOKS.values() for spec in book)
    assert in_books == sorted(spec.title for spec in Importer.SPECS)
    # Лист, приписанный к чужой книге, решал бы её судьбу по другой таблице.
    strangers = [
        (book, spec.title)
        for book, sheets in BOOKS.items()
        for spec in sheets
        if spec.spreadsheet != book
    ]
    assert strangers == []


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


def test_fallback_sheet_title_keeps_fingerprint() -> None:
    """Лист под прежним именем «Впитывание масла» — то же содержимое, а не повод
    переносить книгу: в отпечатке имя из описания, а не найденное в таблице."""
    renamed = sheets_client(
        missing=("Способы приготовления",),
        kitchen={"Впитывание масла": kitchen_sheets()["Способы приготовления"]},
    )

    verdict = judge("kitchen", _read(renamed))

    assert verdict.problem is None
    assert verdict.fingerprint == judge("kitchen", _read(sheets_client())).fingerprint


def test_shifted_columns_block_the_book() -> None:
    ing = kitchen_sheets()["ING"]
    ing[0] = list(ing[0])
    ing[0][1] = "Совсем другая колонка"

    verdict = judge("kitchen", _read(sheets_client(kitchen={"ING": ing})))

    assert verdict.fingerprint is None
    assert verdict.problem is not None
    assert "в листе «ING» сдвинулись колонки" in verdict.problem
    assert "Совсем другая колонка" in verdict.problem


def _renamed(*columns: int) -> list[list[str]]:
    """Лист ING, у которого в шапке переименованы колонки с этими номерами (с нуля)."""
    ing = kitchen_sheets()["ING"]
    ing[0] = [f"Другое {n}" if n in columns else cell for n, cell in enumerate(ing[0])]
    return ing


def test_many_shifted_columns_are_cut_short() -> None:
    """Съехавший лист даёт расхождение в каждой колонке, а полосу на сайте читают
    целиком: называем первые три, остальные — числом."""
    client = sheets_client(kitchen={"ING": _renamed(1, 2, 3, 4, 5)})

    problem = judge("kitchen", _read(client)).problem

    assert problem is not None
    assert problem.count("ожидался заголовок") == 3
    assert problem.endswith("; и ещё 2")


def test_problems_of_different_sheets_are_told_apart() -> None:
    """Расхождения внутри листа разделяет «; », причины разных листов — « | »."""
    client = sheets_client(kitchen={"ING": _renamed(1, 2)}, missing=("ТТК",))

    problem = judge("kitchen", _read(client)).problem

    assert problem is not None
    assert problem.split(" | ") == [
        "в листе «ING» сдвинулись колонки — "
        "колонка B: ожидался заголовок «Категория», в листе «Другое 1»; "
        "колонка C: ожидался заголовок «Наименование ингредиента», в листе «Другое 2»",
        "листа «ТТК» нет в таблице",
    ]


def test_empty_sheet_is_named() -> None:
    verdict = judge("kitchen", _read(sheets_client(kitchen={"ТТК": []})))

    assert verdict.problem == "лист «ТТК» пуст"


def test_missing_header_row_is_named() -> None:
    """Шапка листа карточек — в две строки; в листе из одной строки её нет."""
    cards = [header(specs.INGREDIENT_CARDS)]

    verdict = judge("ingredient_cards", _read(sheets_client(cards=cards)))

    assert verdict.problem == "лист «Лист1»: нет строки заголовков (ожидалась строка 2)"


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


class NotFoundSpreadsheet:
    """Таблица, которую Google не нашёл: неверный идентификатор или её удалили."""

    def worksheets(self) -> list[object]:
        raise RuntimeError("APIError: [404]: Requested entity was not found.")


def test_unopened_book_is_one_problem_without_sheet_names() -> None:
    """Отказ открытия таблицы приходит на каждый её лист. Дело не в листе:
    причина одна и без имён листов."""
    client = sheets_client()
    client._spreadsheets["kitchen-id"] = NotFoundSpreadsheet()

    assert judge("kitchen", _read(client)).problem == NOT_FOUND


# ---------------------------------------------------------------------------
# Открытие таблицы — тем, что на самом деле бросает gspread 6.2
# ---------------------------------------------------------------------------
# Исключения — настоящие классы gspread, а не дублёры: дублёр закрепил бы наше
# представление о тексте исключения, а неверным оказалось именно оно (403
# приходит голым PermissionError(), а не строкой APIError).


class KitchenWontOpen(FakeSheetsClient):
    """`open_by_key` падает на таблице кухни; карточки открываются как обычно."""

    def __init__(self, error: Exception) -> None:
        super().__init__(sheets_client()._spreadsheets)
        self._error = error

    def open(self, spreadsheet_id: str) -> FakeSpreadsheet:
        if spreadsheet_id == IDS["kitchen"]:
            raise self._error
        return super().open(spreadsheet_id)


def _response(status: int, body: bytes = b"") -> requests.Response:
    """Ответ requests, из которого gspread строит свои исключения."""
    response = requests.Response()
    response.status_code = status
    response._content = body
    return response


def _kitchen_problem(error: Exception) -> str | None:
    return judge("kitchen", _read(KitchenWontOpen(error))).problem


def test_forbidden_spreadsheet_is_access() -> None:
    """На 403 gspread бросает голый `PermissionError()` — текста нет вовсе."""
    assert _kitchen_problem(PermissionError()) == ACCESS


def test_unknown_spreadsheet_is_not_found() -> None:
    """На 404 gspread бросает `SpreadsheetNotFound`, чей текст — «<Response [404]>»."""
    assert _kitchen_problem(SpreadsheetNotFound(_response(404))) == NOT_FOUND


KEY_FILE = "/etc/kitchen-platform/service_account.json"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            PermissionError(13, "Permission denied", KEY_FILE),
            f"не удалось открыть таблицу: PermissionError: [Errno 13] Permission denied: "
            f"'{KEY_FILE}'",
        ),
        (
            FileNotFoundError(2, "No such file or directory", KEY_FILE),
            f"не удалось открыть таблицу: FileNotFoundError: [Errno 2] No such file or "
            f"directory: '{KEY_FILE}'",
        ),
    ],
)
def test_local_os_error_is_not_about_the_spreadsheet(error: Exception, expected: str) -> None:
    """Ключ сервисного аккаунта не читается или его нет — это наша ошибка, а не
    ответ Google: «доступ закрыт» или «таблица не найдена» увели бы искать не там."""
    assert _kitchen_problem(error) == expected


def test_google_error_page_is_no_answer() -> None:
    """На 502 Google отдаёт HTML-страницу: gspread пишет в тексте код -1, а
    настоящий код остаётся только в ответе."""
    page = b"<!DOCTYPE html><title>Error 502 (Server Error)!!1</title>"

    assert _kitchen_problem(APIError(_response(502, page))) == NO_ANSWER


def test_network_failure_is_no_answer_despite_errno() -> None:
    """Сетевой сбой тоже несёт [Errno] — от сокета. Это «Google не ответил», а не
    ошибка на нашей стороне, и перевод у него прежний."""
    error = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='sheets.googleapis.com', port=443): Max retries exceeded "
        "with url: /v4/spreadsheets/kitchen-id (Caused by NewConnectionError("
        "\"HTTPSConnection(host='sheets.googleapis.com', port=443): Failed to establish "
        'a new connection: [Errno 111] Connection refused"))'
    )

    assert _kitchen_problem(error) == NO_ANSWER


class BatchReadFails(FakeSpreadsheet):
    """Список листов пришёл, а пакетное чтение значений упало."""

    def values_batch_get(self, ranges: list[str]) -> dict[str, object]:
        raise RuntimeError("что-то совсем новое")


def test_failed_batch_read_is_one_problem() -> None:
    """Значения всех листов читаются одним запросом — и отказ у них один."""
    client = sheets_client()
    client._spreadsheets["kitchen-id"] = BatchReadFails(
        {title: FakeWorksheet(cells, title) for title, cells in kitchen_sheets().items()}
    )

    problem = judge("kitchen", _read(client)).problem

    assert problem == "не удалось прочитать таблицу: RuntimeError: что-то совсем новое"


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
        ("APIError: [503]: The service is currently unavailable.", "Google не ответил"),
        ("листа «ТТК» нет в таблице", "листа «ТТК» нет в таблице"),
        ("что-то совсем новое", "не удалось прочитать лист «ING»: что-то совсем новое"),
    ],
)
def test_explain_speaks_plainly(error: str, expected: str) -> None:
    """Причину видят все, в том числе шеф: код исключения — не объяснение."""
    assert expected in explain("ING", error)


@pytest.mark.parametrize(
    "error",
    [
        "APIError: [500]: Unknown Error.",
        "APIError: [502]: Bad Gateway",
        "APIError: [503]: Try again later",
        "APIError: [504]: Deadline expired before operation could complete.",
        "APIError: [-1]: Internal error encountered.",
        "APIError: [-1]: Backend Error",
        "APIError: [-1]: The service is currently unavailable.",
    ],
)
def test_every_google_failure_sign_means_no_answer(error: str) -> None:
    """Каждый признак сбоя у Google (5xx) — отдельным случаем: признак, выпавший
    из списка, вернул бы шефу текст исключения."""
    assert explain("ING", error) == NO_ANSWER
