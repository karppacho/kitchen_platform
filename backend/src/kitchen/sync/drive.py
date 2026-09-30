"""Фото карточек ингредиентов в Google Drive: загрузка, скачивание, корзина.

Фото лежат только в закрытой папке на общем диске. Доступ к ним даёт папка:
в ней участники — шеф и сервисный аккаунт платформы, больше никто. Поэтому
клиент **никогда не трогает доступы** — ни одного запроса на запись в
``/permissions``. Бот открывал каждое фото «всем, у кого есть ссылка», и
ссылка из листа открывалась в любом браузере; здесь так нельзя.

Запросы — REST Drive v3 обычной сессией requests (в бою — AuthorizedSession
из google-auth, как у клиента таблиц), а не googleapiclient: тот ходит через
httplib2 со своим устройством таймаутов. Каждый запрос:

* с ``supportsAllDrives=true`` — без него файлы общего диска для запроса не
  существуют, и Drive отвечает 404;
* с таймаутом ``(соединение, чтение)`` из настроек — у requests таймаута по
  умолчанию нет, и зависший запрос вешал бы ручку навсегда. Токен
  google-auth обновляет с таймаутом того же запроса.

Отказ Drive превращается в :class:`DriveError`: вид — для кода, текст — для
человека, его видит повар.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import TYPE_CHECKING, Literal

import requests
from google.auth import exceptions as google_errors

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from kitchen.config import Settings

FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

DriveScope = Literal["drive", "drive.file"]

SCOPES: dict[DriveScope, str] = {
    "drive.file": "https://www.googleapis.com/auth/drive.file",
    "drive": "https://www.googleapis.com/auth/drive",
}
"""Охват доступа платформы (настройка ``DRIVE_SCOPE``).

``drive.file`` — только файлы, которые платформа создала сама; ``drive`` —
всё, что открыто сервисному аккаунту. Какого хватает, показывает скрипт
проверки настройки.
"""

INSPECT_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"
"""Охват скрипта проверки: читать сведения о файлах и доступах, не менять."""

API_DISABLED_REASONS = frozenset({"accessNotConfigured", "SERVICE_DISABLED"})
"""Как Drive называет выключенный в проекте Google Cloud API — по-старому и по-новому."""

DriveErrorKind = Literal["quota", "forbidden", "not_found", "unavailable", "bad_reply"]

_FILE_ID = re.compile(r"[A-Za-z0-9_-]+")
"""То же правило, что у ``drive_view_url`` в domain/cards.py."""

_RATE_REASONS = frozenset(
    {"userRateLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded", "RATE_LIMIT_EXCEEDED"}
)
_OPEN_TO = frozenset({"anyone", "domain"})
_CHUNK = 64 * 1024
_PERMISSION_PAGES = 20
_METADATA_FIELDS = "id,name,mimeType,driveId,trashed,capabilities(canAddChildren,canEdit,canTrash)"
_PERMISSION_FIELDS = "nextPageToken,permissions(id,type,role,emailAddress,domain,displayName)"

_QUOTA = (
    "В Google Drive нет места для фото: либо кончилось место на общем диске, либо папка "
    "для фото лежит не на общем диске — своего места у платформы нет. Сообщите администратору."
)
_API_DISABLED = (
    "Google Drive выключен для платформы: включите Drive API в проекте Google Cloud "
    "сервисного аккаунта."
)
_NO_RIGHTS = "Платформе не хватает прав на папку фото в Google Drive. Сообщите администратору."
_KEY_REFUSED = "Google не принял ключ платформы. Сообщите администратору."
_KEY_UNREADABLE = (
    "Не прочитан ключ сервисного аккаунта Google — проверьте GOOGLE_CREDENTIALS_PATH. "
    "Сообщите администратору."
)
_NOT_FOUND = "Не найдено в Google Drive: файл или папку удалили, или у платформы нет к ним доступа."
_RATE = "Google Drive просит подождать: слишком много запросов. Попробуйте через минуту."
_UNAVAILABLE = "Google Drive сейчас не отвечает. Попробуйте ещё раз через минуту."
_BAD_REPLY = (
    "Google Drive ответил непонятно. Попробуйте ещё раз; если повторится — сообщите администратору."
)


class DriveError(RuntimeError):
    """Drive не сделал, что просили.

    ``kind`` — что делать коду: ``quota`` и ``forbidden`` — чинит
    администратор, ``unavailable`` — стоит повторить, ``not_found`` — файла
    нет, ``bad_reply`` — ответ не разобран. Текст исключения — для человека.
    ``status`` и ``reason`` — как ответил Google, для журнала и скрипта
    проверки; ``None`` — ответа не было вовсе.
    """

    def __init__(
        self, kind: DriveErrorKind, message: str, *, status: int | None = None, reason: str = ""
    ) -> None:
        super().__init__(message)
        self.kind: DriveErrorKind = kind
        self.status = status
        self.reason = reason


def explain_drive_error(status: int, body: bytes) -> DriveError:
    """Отказ Drive (код и тело ответа) → ошибка с видом и понятным текстом.

    Причина берётся из тела ``{"error": {"errors": [{"reason": …}]}}``, у новых
    ответов — ещё и из ``details``. Тело не JSON (страница прокси) — вид
    определяет код ответа.
    """
    reasons = _reasons(body)
    disabled = [r for r in reasons if r in API_DISABLED_REASONS]
    reason = disabled[0] if disabled else (reasons[0] if reasons else "")

    def error(kind: DriveErrorKind, message: str) -> DriveError:
        return DriveError(kind, message, status=status, reason=reason)

    if disabled:
        return error("forbidden", _API_DISABLED)
    if "storageQuotaExceeded" in reasons:
        return error("quota", _QUOTA)
    if status == 429 or _RATE_REASONS.intersection(reasons):
        return error("unavailable", _RATE)
    if status == 401:
        return error("forbidden", _KEY_REFUSED)
    if status == 403:
        return error("forbidden", _NO_RIGHTS)
    if status == 404:
        return error("not_found", _NOT_FOUND)
    if status >= 500:
        return error("unavailable", _UNAVAILABLE)
    return error("bad_reply", _BAD_REPLY)


def folder_is_closed(permissions: Iterable[Mapping[str, object]]) -> bool:
    """Закрыта ли папка: нет доступа ни «всем, у кого есть ссылка», ни всему домену.

    Доступ «Ограниченный» в интерфейсе Drive — это ровно отсутствие прав типа
    ``anyone`` и ``domain``; остальные права — у конкретных людей и групп.
    Файлы в папке наследуют её доступы, поэтому закрытая папка — закрытые фото.
    """
    return not any(p.get("type") in _OPEN_TO for p in permissions)


class DriveClient:
    """Файлы в закрытой папке фото.

    Сессия собирается при первом запросе: без ключа приложение стартует, а
    загрузка фото отвечает понятной ошибкой. ``connect`` — фабрика сессии:
    в бою :func:`authorized_session`, в тестах — сессия с фальшивым Drive.
    """

    def __init__(
        self,
        connect: Callable[[], requests.Session],
        *,
        folder_id: str,
        timeout: tuple[int, int],
    ) -> None:
        self._connect = connect
        self._session: requests.Session | None = None
        self.folder_id = folder_id
        self._timeout = timeout

    def upload_jpeg(
        self, content: bytes, *, name: str, app_properties: Mapping[str, str] | None = None
    ) -> str:
        """Положить JPEG в папку фото одним запросом multipart; вернуть id файла.

        Папка — только настроенная закрытая: файл без неё лёг бы в «Мой диск»
        сервисного аккаунта, где нет ни места, ни шефа в доступе.
        ``app_properties`` — пометки платформы на файле (черновик, вид фото).
        """
        if not self.folder_id:
            raise ValueError("Не задана папка для фото: DRIVE_CARDS_FOLDER_ID пуст")
        _check_id(self.folder_id)
        metadata: dict[str, object] = {
            "name": name,
            "mimeType": "image/jpeg",
            "parents": [self.folder_id],
        }
        if app_properties:
            metadata["appProperties"] = dict(app_properties)
        body, content_type = _multipart(metadata, content)
        reply = _json(
            self._send(
                "POST",
                UPLOAD_URL,
                params={"uploadType": "multipart", "fields": "id"},
                data=body,
                headers={"Content-Type": content_type},
            )
        )
        file_id = reply.get("id")
        if not isinstance(file_id, str) or not _FILE_ID.fullmatch(file_id):
            raise DriveError("bad_reply", _BAD_REPLY)
        return file_id

    def download(self, file_id: str, *, max_bytes: int) -> bytes:
        """Содержимое файла, но не больше ``max_bytes`` байт.

        Дальше ``max_bytes`` ответ не читается: файл, подменённый в папке
        огромным, не съест память сервера — отдастся его начало.
        """
        _check_id(file_id)
        if max_bytes < 1:
            raise ValueError(f"max_bytes должен быть положительным, а не {max_bytes}")
        response = self._send("GET", f"{FILES_URL}/{file_id}", params={"alt": "media"}, stream=True)
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in response.iter_content(chunk_size=_CHUNK):
                piece: bytes = chunk[: max_bytes - size]
                chunks.append(piece)
                size += len(piece)
                if size >= max_bytes:
                    break
        except requests.RequestException as error:
            raise DriveError("unavailable", _UNAVAILABLE) from error
        finally:
            response.close()
        return b"".join(chunks)

    def trash(self, file_id: str) -> None:
        """Отправить файл в корзину Drive — не удалить.

        Ошибочно выброшенное фото шеф достанет из корзины сам; удаление
        навсегда платформе не нужно.
        """
        _check_id(file_id)
        reply = _json(
            self._send(
                "PATCH",
                f"{FILES_URL}/{file_id}",
                params={"fields": "id,trashed"},
                json_body={"trashed": True},
            )
        )
        if reply.get("trashed") is not True:
            raise DriveError("bad_reply", _BAD_REPLY)

    def metadata(self, file_id: str) -> dict[str, object]:
        """Сведения о файле или папке: тип, общий диск, корзина и что можно делать.

        ``capabilities`` — права самого сервисного аккаунта: ``canEdit`` у
        таблицы, ``canAddChildren`` у папки.
        """
        _check_id(file_id)
        return _json(
            self._send("GET", f"{FILES_URL}/{file_id}", params={"fields": _METADATA_FIELDS})
        )

    def permissions(self, file_id: str) -> list[dict[str, object]]:
        """Кто имеет доступ к файлу или папке — все страницы списка. Только чтение."""
        _check_id(file_id)
        found: list[dict[str, object]] = []
        params = {"fields": _PERMISSION_FIELDS, "pageSize": "100"}
        for _ in range(_PERMISSION_PAGES):
            page = _json(self._send("GET", f"{FILES_URL}/{file_id}/permissions", params=params))
            items = page.get("permissions", [])
            if not isinstance(items, list):
                raise DriveError("bad_reply", _BAD_REPLY)
            found.extend(dict(item) for item in items if isinstance(item, dict))
            token = page.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return found
            params = {**params, "pageToken": token}
        raise DriveError("bad_reply", _BAD_REPLY)

    # --- внутреннее -----------------------------------------------------------
    def _send(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str],
        data: bytes | None = None,
        json_body: dict[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        stream: bool = False,
    ) -> requests.Response:
        """Один запрос к Drive: общий диск виден, таймаут стоит, отказ объяснён."""
        session = self._connected()
        try:
            response = session.request(
                method,
                url,
                params={**params, "supportsAllDrives": "true"},
                data=data,
                json=json_body,
                headers=headers,
                stream=stream,
                timeout=self._timeout,
            )
        except google_errors.RefreshError as error:
            raise DriveError("forbidden", _KEY_REFUSED) from error
        except (requests.RequestException, google_errors.TransportError) as error:
            raise DriveError("unavailable", _UNAVAILABLE) from error
        if response.status_code < 400:
            return response
        try:
            body = response.content
        except requests.RequestException:
            body = b""
        finally:
            response.close()
        raise explain_drive_error(response.status_code, body)

    def _connected(self) -> requests.Session:
        if self._session is None:
            try:
                self._session = self._connect()
            except (OSError, ValueError) as error:
                raise DriveError("forbidden", _KEY_UNREADABLE) from error
        return self._session


def authorized_session(
    credentials_path: Path, scope: str, *, refresh_timeout: int
) -> requests.Session:
    """Сессия сервисного аккаунта с одним охватом доступа.

    Собрана как у клиента таблиц: ``refresh_timeout`` — таймаут обновления
    токена; ``type: ignore`` — у этих вызовов google-auth нет аннотаций типов.
    Файл ключа читается здесь, сети нет до первого запроса.
    """
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials

    credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(credentials_path), scopes=[scope]
    )
    session: requests.Session = AuthorizedSession(  # type: ignore[no-untyped-call]
        credentials, refresh_timeout=refresh_timeout
    )
    return session


def drive_from_settings(settings: Settings) -> DriveClient:
    """Клиент боевого Drive: охват, папка и таймауты — из настроек."""
    path = settings.google_credentials_path
    scope = SCOPES[settings.drive_scope]
    refresh = settings.google_refresh_timeout
    return DriveClient(
        lambda: authorized_session(path, scope, refresh_timeout=refresh),
        folder_id=settings.drive_cards_folder_id,
        timeout=settings.google_timeout,
    )


def _check_id(file_id: str) -> None:
    """Id вклеивается в адрес запроса: «abc/permissions» превратил бы корзину в
    запрос к доступам. Такой id отвергается до сети."""
    if not _FILE_ID.fullmatch(file_id):
        raise ValueError(f"Непохоже на идентификатор файла Drive: {file_id[:80]!r}")


def _multipart(metadata: Mapping[str, object], content: bytes) -> tuple[bytes, str]:
    """Тело ``multipart/related``: JSON с метаданными, затем сам файл."""
    boundary = secrets.token_hex(16)
    while boundary.encode() in content:
        boundary = secrets.token_hex(16)
    marker = f"--{boundary}".encode()
    body = b"".join(
        (
            marker,
            b"\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n",
            json.dumps(metadata).encode(),
            b"\r\n",
            marker,
            b"\r\nContent-Type: image/jpeg\r\n\r\n",
            content,
            b"\r\n",
            marker,
            b"--\r\n",
        )
    )
    return body, f"multipart/related; boundary={boundary}"


def _json(response: requests.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError as error:
        raise DriveError("bad_reply", _BAD_REPLY) from error
    except requests.RequestException as error:
        raise DriveError("unavailable", _UNAVAILABLE) from error
    finally:
        response.close()
    if not isinstance(payload, dict):
        raise DriveError("bad_reply", _BAD_REPLY)
    return payload


def _reasons(body: bytes) -> list[str]:
    """Причины отказа из тела ответа Google; не JSON — пусто."""
    try:
        payload = json.loads(body)
    except ValueError:
        return []
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return []
    found: list[str] = []
    for key in ("errors", "details"):
        items = error.get(key)
        if isinstance(items, list):
            found.extend(
                item["reason"]
                for item in items
                if isinstance(item, dict) and isinstance(item.get("reason"), str)
            )
    return found
