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

        credentials = Credentials.from_service_account_file(
            str(self._credentials_path), scopes=list(self.SCOPES)
        )

        # Таймауты обязательны. У google-auth их по умолчанию нет вообще:
        # зависший запрос вешает воркер молча и навсегда. В kitchen_bot эти
        # числа появились после реального зависания.
        session = AuthorizedSession(credentials, refresh_timeout=self._refresh_timeout)
        session.timeout = self._timeout

        self._gc = gspread.Client(auth=credentials, session=session)
        return self._gc

    def open(self, spreadsheet_id: str) -> Spreadsheet:
        client = self._client()
        return client.open_by_key(spreadsheet_id)  # type: ignore[attr-defined, no-any-return]
