"""Доступ к Google Sheets.

Работа с таблицей описана протоколами, а не прямым обращением к gspread.
Это не абстракция ради абстракции: в kitchen_bot тесты записи в Sheets
существуют именно потому, что там подменяется точка подключения
(``monkeypatch.setattr(d, "_connect", ...)``). Здесь то же самое сделано
через тип, поэтому дублёр не нужно подсовывать хитростью — он просто
другая реализация.

Единственная реализация, ходящая в сеть, — :class:`GspreadClient`.

Методы протоколов названы и устроены как у gspread 6.2 — это его
низкоуровневые вызовы Sheets API, тела запросов и ответы идут как есть.
Своей обёртки над ними нет: настоящая таблица gspread подходит под
протокол без переходника.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import requests
    from google.auth.credentials import Credentials as GoogleCredentials

# Значения листа, как их видит человек (FORMATTED_VALUE, по умолчанию), —
# всегда строки: gspread не типизирует ячейки. Числами числа приезжают только
# при UNFORMATTED_VALUE, и разбирать такой ответ — дело вызывающего.
Cells = list[list[str]]


@runtime_checkable
class Worksheet(Protocol):
    """Лист таблицы."""

    @property
    def title(self) -> str:
        """Имя листа, как его видит человек."""
        ...

    @property
    def id(self) -> int:
        """Числовой идентификатор листа (sheetId).

        По нему, а не по имени, адресуются изменения структуры в
        :meth:`Spreadsheet.batch_update` — например, ``appendDimension``.
        """
        ...

    @property
    def row_count(self) -> int:
        """Сколько строк в сетке листа — всего, а не заполненных.

        Это снимок на момент открытия листа: после ``appendDimension``
        gspread его не обновляет, свежее число даёт только заново открытый
        лист.
        """
        ...

    def get_all_values(self) -> Cells:
        """Все значения листа.

        gspread 6.2 выравнивает строки до прямоугольника (``fill_gaps``).
        Рваные строки — без хвостовых пустых ячеек — приходят из
        :meth:`Spreadsheet.values_batch_get`, которым читают листы и импорт,
        и писатель строки; код чтения обязан переживать обе формы."""
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

    def values_batch_get(
        self, ranges: list[str], params: dict[str, str] | None = None
    ) -> dict[str, object]:
        """Несколько диапазонов ОДНИМ запросом.

        Ради этого метода всё и затевалось: квота Google — 60 запросов в
        минуту на пользователя, и чтение по листу за раз её выбирает.
        Возвращает ответ Sheets API как есть: ``{"valueRanges": [...]}``.

        Диапазоны — в нотации A1 с именем листа в кавычках:
        ``'Лист1'!A6:P6``. ``params`` — параметры запроса как есть. Главный
        из них ``valueRenderOption``: по умолчанию ``FORMATTED_VALUE`` —
        строки, как их видит человек («12,5»); ``UNFORMATTED_VALUE`` отдаёт
        числа числами — так записанное сверяется с тем, что писали.

        gspread дописывает ``ranges`` в переданный словарь — передавайте
        свежий на каждый вызов. Словарь-константа испортится после первого
        же чтения, неизменяемое отображение упадёт ``TypeError``; поэтому
        здесь ``dict``, а не ``Mapping``.
        """
        ...

    def values_batch_update(self, body: Mapping[str, object]) -> dict[str, object]:
        """Записать несколько диапазонов ОДНИМ запросом (``values.batchUpdate``).

        Тело — как у Sheets API: ``valueInputOption`` и ``data`` со списком
        ``{"range": …, "values": [[…]]}``. Ошибка в одном диапазоне (неверный
        адрес, край сетки, значений больше, чем ячеек) отклоняет весь
        запрос — не ложится ни один.

        ``RAW`` кладёт значения как есть: строка остаётся строкой, даже
        если начинается с «=». ``USER_ENTERED`` разбирает их, как будто их
        набрал человек: «=…» становится формулой, «12,5» — числом. Текст с
        этикетки пишется только ``RAW``.

        Тело уходит через ``json.dumps``: ``Decimal`` в нём — ``TypeError``
        ещё до отправки.
        """
        ...

    def values_batch_clear(
        self,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Очистить значения диапазонов (``values.batchClear``).

        Тело — ``{"ranges": [...]}``. Строки остаются на месте: очистка, а не
        удаление, не сдвигает раскладку и номера строк. Порядок параметров —
        как у gspread, поэтому ``body`` передают по имени.
        """
        ...

    def batch_update(self, body: Mapping[str, object]) -> dict[str, object]:
        """Изменить структуру таблицы (``spreadsheets.batchUpdate``).

        Нужен один вид запроса — ``appendDimension``: дописать строки в конец
        сетки, когда свободная строка лежит за её краем. Иначе запись
        значений падает с «exceeds grid limits».
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

    Вход в Google — ключ, сессия с пулом соединений, токен — один на клиента
    и на все его виды с другими таймаутами (:meth:`with_timeout`); закрывает
    его :meth:`close`.
    """

    def __init__(self, credentials_path: Path, *, timeout: tuple[int, int] = (10, 60)) -> None:
        self._credentials_path = credentials_path
        self._timeout = timeout
        self._gc: object | None = None
        self._books: dict[str, Spreadsheet] = {}
        self._login: tuple[GoogleCredentials, requests.Session] | None = None
        self._parent: GspreadClient | None = None

    # Достаточно для чтения и записи листов; drive нужен, чтобы открыть
    # таблицу по ключу. Более широких прав не просим.
    SCOPES = (
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    )

    def with_timeout(self, timeout: tuple[int, int]) -> GspreadClient:
        """Тот же вход в Google, но свои таймауты запросов.

        Таймаут у gspread — на клиента целиком, а не на запрос. Вид — свой
        клиент gspread поверх той же сессии: второго ключа, токена и пула
        соединений не нужно. Таблицы вид открывает сам — открытая таблица
        помнит клиента, через которого её открыли, и его таймауты.
        """
        view = GspreadClient(self._credentials_path, timeout=timeout)
        view._parent = self
        return view

    def close(self) -> None:
        """Закрыть соединения сессии — общей с видами. Дальше клиент не работает."""
        if self._parent is not None:
            self._parent.close()
        elif self._login is not None:
            self._login[1].close()

    def _sign_in(self) -> tuple[GoogleCredentials, requests.Session]:
        """Ключ сервисного аккаунта и сессия — одни на клиента и его виды."""
        if self._parent is not None:
            return self._parent._sign_in()
        if self._login is not None:
            return self._login

        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials

        # google-auth и gspread не типизированы: под strict каждый их вызов
        # выглядит как обращение к нетипизированной функции. Это граница с
        # чужой библиотекой, а не наш долг.
        credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            str(self._credentials_path), scopes=list(self.SCOPES)
        )
        # Без refresh_timeout: google-auth 2.57 его только хранит и не
        # применяет — токен обновляется с таймаутом самого запроса (см. ниже).
        session: requests.Session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
        self._login = (credentials, session)
        return self._login

    def _client(self) -> object:
        if self._gc is not None:
            return self._gc

        import gspread

        # Таймауты обязательны: по умолчанию их нет вообще, и зависший запрос
        # вешает воркер молча и навсегда. Ставятся они одним местом —
        # client.set_timeout(...): gspread передаёт таймаут в каждый запрос,
        # а AuthorizedSession обновляет токен с таймаутом того же запроса
        # (functools.partial(self._auth_request, timeout=…)). Свой
        # refresh_timeout у сессии google-auth 2.57 только хранит и не
        # применяет — его и не передаём (тест в test_sheets_client.py).
        #
        # Присвоить `session.timeout` нельзя: AuthorizedSession — наследник
        # requests.Session, у которого такого атрибута нет, и присваивание
        # молча ничего не делает. Поймано mypy в полном окружении.
        credentials, session = self._sign_in()
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
