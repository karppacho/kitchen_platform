"""Доступ к Google Sheets.

Работа с таблицей описана протоколами, а не прямым обращением к gspread.
Это не абстракция ради абстракции: в kitchen_bot тесты записи в Sheets
существуют именно потому, что там подменяется точка подключения
(``monkeypatch.setattr(d, "_connect", ...)``). Здесь то же самое сделано
через тип, поэтому дублёр не нужно подсовывать хитростью — он просто
другая реализация.

Единственная реализация, ходящая в сеть, — :class:`GspreadClient`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

# Значения из листа всегда приезжают строками: gspread не типизирует ячейки.
Cells = list[list[str]]


@runtime_checkable
class Worksheet(Protocol):
    """Лист таблицы."""

    @property
    def title(self) -> str:
        """Имя листа, как его видит человек."""
        ...

    def get_all_values(self) -> Cells:
        """Все значения листа. Строки бывают рваными: хвостовые пустые
        ячейки gspread не возвращает, и читатель обязан это учитывать."""
        ...


class Spreadsheet(Protocol):
    """Таблица целиком."""

    def worksheet(self, title: str) -> Worksheet:
        """Лист по имени.

        Отсутствующий лист — исключение; читатель ловит его и пробует
        прежние имена из ``SheetSpec.fallback_titles``.
        """
        ...

    def worksheets(self) -> list[Worksheet]:
        """Все листы таблицы. Один запрос вместо попытки открыть каждый."""
        ...

    def values_batch_get(self, ranges: list[str]) -> dict[str, object]:
        """Несколько диапазонов ОДНИМ запросом.

        Ради этого метода всё и затевалось: квота Google — 60 запросов в
        минуту на пользователя, и чтение по листу за раз её выбирает.
        Возвращает ответ Sheets API как есть: ``{"valueRanges": [...]}``.
        """
        ...


class SheetsClient(Protocol):
    """Источник таблиц."""

    def open(self, spreadsheet_id: str) -> Spreadsheet: ...


class SheetNotFoundError(LookupError):
    """Листа с таким именем в таблице нет."""


class GspreadClient:
    """Реальный клиент.

    Соединение ленивое: конструктор не ходит в сеть, поэтому объект можно
    создать при старте процесса, не завися от доступности Google.
    """

    def __init__(
        self,
        credentials_path: Path,
        *,
        timeout: tuple[int, int] = (10, 60),
        refresh_timeout: int = 15,
    ) -> None:
        self._credentials_path = credentials_path
        self._timeout = timeout
        self._refresh_timeout = refresh_timeout
        self._gc: object | None = None
        self._books: dict[str, Spreadsheet] = {}

    # Достаточно для чтения и записи листов; drive нужен, чтобы открыть
    # таблицу по ключу. Более широких прав не просим.
    SCOPES = (
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    )

    def _client(self) -> object:
        if self._gc is not None:
            return self._gc

        import gspread
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials

        # google-auth и gspread не типизированы: под strict каждый их вызов
        # выглядит как обращение к нетипизированной функции. Это граница с
        # чужой библиотекой, а не наш долг.
        credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            str(self._credentials_path), scopes=list(self.SCOPES)
        )

        # Таймауты обязательны: по умолчанию их нет вообще, и зависший запрос
        # вешает воркер молча и навсегда. Ставятся они в двух РАЗНЫХ местах,
        # и это не дублирование:
        #
        #   refresh_timeout у сессии  — на обновление токена;
        #   client.set_timeout(...)   — на сами запросы к Sheets.
        #
        # Присвоить `session.timeout` нельзя: AuthorizedSession — наследник
        # requests.Session, у которого такого атрибута нет, и присваивание
        # молча ничего не делает. Поймано mypy в полном окружении.
        session = AuthorizedSession(  # type: ignore[no-untyped-call]
            credentials, refresh_timeout=self._refresh_timeout
        )

        client = gspread.Client(auth=credentials, session=session)
        client.set_timeout(self._timeout)
        self._gc = client
        return self._gc

    def open(self, spreadsheet_id: str) -> Spreadsheet:
        """Таблица по ключу. Результат кешируется.

        `open_by_key` — это сетевой запрос, а не разыменование ссылки.
        Открывать таблицу заново перед чтением каждого листа значит удвоить
        расход квоты на ровном месте.
        """
        cached = self._books.get(spreadsheet_id)
        if cached is not None:
            return cached
        book: Spreadsheet = self._client().open_by_key(spreadsheet_id)  # type: ignore[attr-defined]
        self._books[spreadsheet_id] = book
        return book
