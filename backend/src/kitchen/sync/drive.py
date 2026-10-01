"""Фото карточек ингредиентов в Google Drive: загрузка, скачивание, корзина.

Фото лежат в папке фото из настроек — сейчас это прежняя папка бота на
«Моём диске» её владельца (решение 01.10). Файлы загружает сервисный
аккаунт платформы, и место они занимают его — своё, 15 ГБ: кончиться может
оно. Папка может лежать и на общем диске — тогда место общего диска.

Доступ к фото даёт папка, а не файл: файлы наследуют её доступы. Владелец
поставил папке «все со ссылкой — читатель», чтобы ссылка из листа
открывалась у шефа, как при боте. Сама платформа **никогда не трогает
доступы** — ни одного запроса на запись в ``/permissions``: бот открывал
доступ каждому фото сам, здесь так нельзя. Что папка не открыта на
изменение, проверяет скрипт настройки (:func:`folder_access`).

Запросы — REST Drive v3 обычной сессией requests (в бою — AuthorizedSession
из google-auth, как у клиента таблиц), а не googleapiclient: тот ходит через
httplib2 со своим устройством таймаутов. Каждый запрос:

* к файлам — с ``supportsAllDrives=true``: без него файлы общего диска для
  запроса не существуют, и Drive отвечает 404;
* с таймаутом ``(соединение, чтение)`` из настроек — у requests таймаута по
  умолчанию нет, и зависший запрос вешал бы ручку навсегда. Токен
  google-auth обновляет с таймаутом того же запроса (см. тест).

Корзина — только для файлов из папки фото: с охватом ``drive`` платформа
видит всё, что открыто сервисному аккаунту, и неверный id не должен
выбросить чужой файл.

Отказ Drive превращается в :class:`DriveError`: вид — для кода, текст — для
человека, его видит повар.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import requests
from google.auth import exceptions as google_errors

from kitchen.domain.cards import DRIVE_FILE_ID

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from pathlib import Path

    from kitchen.config import Settings

FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
ABOUT_URL = "https://www.googleapis.com/drive/v3/about"

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

DriveErrorKind = Literal["quota", "forbidden", "not_found", "unavailable", "bad_reply", "too_large"]

FolderAccess = Literal["closed", "readable_by_link", "editable_by_link"]
"""Кому открыта папка фото помимо конкретных людей и групп: никому; по ссылке
(или всему домену) — только смотреть; по ссылке — и менять."""

_RATE_REASONS = frozenset(
    {"userRateLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded", "RATE_LIMIT_EXCEEDED"}
)
_OPEN_TO = frozenset({"anyone", "domain"})
_LOOK_ONLY_ROLES = frozenset({"reader", "commenter"})
"""Роли, с которыми файл не изменить и не удалить. Комментатор только
оставляет заметки — для фото это то же чтение по ссылке."""
_CHUNK = 64 * 1024
_PERMISSION_PAGES = 20
_METADATA_FIELDS = "id,name,mimeType,driveId,trashed,capabilities(canAddChildren,canEdit,canTrash)"
_PERMISSION_FIELDS = "nextPageToken,permissions(id,type,role,emailAddress,domain,displayName)"
_QUOTA_FIELDS = "storageQuota(limit,usage)"

_QUOTA = (
    "В Google Drive нет места для фото: кончилось место у сервисного аккаунта платформы — "
    "фото хранятся на нём. Сообщите администратору."
)
_API_DISABLED = (
    "Google Drive выключен для платформы: включите Drive API в проекте Google Cloud "
    "сервисного аккаунта. Сообщите администратору."
)
_TOO_LARGE = "Файл в Google Drive больше допустимого размера. Сообщите администратору."
_NOT_IN_FOLDER = "Файл не из папки фото — в корзину он не отправлен. Сообщите администратору."
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
    нет, ``bad_reply`` — ответ не разобран, ``too_large`` — файл больше
    допустимого (его подменили в папке). Текст исключения — для человека.
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


def folder_access(permissions: Iterable[Mapping[str, object]]) -> FolderAccess:
    """Кому открыта папка, кроме конкретных людей и групп.

    Доступ «Ограниченный» в интерфейсе Drive — это ровно отсутствие прав типа
    ``anyone`` («все, у кого есть ссылка») и ``domain`` (весь домен);
    остальные права — у конкретных людей и групп. Файлы в папке наследуют её
    доступы.

    * ``closed`` — таких прав нет: фото видят только участники;
    * ``readable_by_link`` — все такие права «читатель» или «комментатор»:
      фото видны всем, у кого есть ссылка, но удалить их нельзя;
    * ``editable_by_link`` — хоть одно такое право шире: фото может удалить
      кто угодно. Незнакомая или пустая роль — тоже сюда: безопаснее считать
      её правом записи.

    Платформа доступы только читает — выдаёт и снимает их человек.
    """
    roles = [p.get("role") for p in permissions if p.get("type") in _OPEN_TO]
    if not roles:
        return "closed"
    if all(role in _LOOK_ONLY_ROLES for role in roles):
        return "readable_by_link"
    return "editable_by_link"


@dataclass(frozen=True, slots=True)
class StorageQuota:
    """Место на Google Drive сервисного аккаунта, в байтах.

    ``limit`` — ``None``: предела нет (так Drive отвечает безлимитным
    аккаунтам), а не ноль."""

    usage: int
    limit: int | None

    @property
    def free(self) -> int | None:
        """Сколько осталось; ``None`` — предела нет."""
        return None if self.limit is None else max(0, self.limit - self.usage)


class DriveClient:
    """Файлы в папке фото.

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

        Папка — только настроенная папка фото: файл без неё лёг бы в корень
        «Моего диска» сервисного аккаунта, куда у шефа доступа нет.
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
        if not isinstance(file_id, str) or not DRIVE_FILE_ID.fullmatch(file_id):
            raise DriveError("bad_reply", _BAD_REPLY)
        return file_id

    def download(self, file_id: str, *, max_bytes: int) -> bytes:
        """Содержимое файла не больше ``max_bytes`` байт; больше — отказ ``too_large``.

        Молча обрезанный файл — битая картинка у шефа или мусор в
        распознавании без следа; понятный отказ лучше. Читается не больше
        ``max_bytes`` и ещё одного куска в 64 КБ — того, на котором предел
        перейдён: подменённый огромным файл не съест память.
        """
        _check_id(file_id)
        if max_bytes < 1:
            raise ValueError(f"max_bytes должен быть положительным, а не {max_bytes}")
        response = self._send("GET", f"{FILES_URL}/{file_id}", params={"alt": "media"}, stream=True)
        chunks: list[bytes] = []
        size = 0
        try:
            for chunk in response.iter_content(chunk_size=_CHUNK):
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise DriveError("too_large", _TOO_LARGE)
        except requests.RequestException as error:
            raise DriveError("unavailable", _UNAVAILABLE) from error
        finally:
            response.close()
        return b"".join(chunks)

    def trash(self, file_id: str) -> None:
        """Отправить файл из папки фото в корзину Drive — не удалить.

        Ошибочно выброшенное фото шеф достанет из корзины сам; удаление
        навсегда платформе не нужно. С охватом ``drive`` платформа видит всё,
        что открыто сервисному аккаунту, поэтому сначала проверяется, что файл
        лежит в папке фото: неверный id не отправит в корзину чужой файл.
        Файла уже нет — цель достигнута, это не ошибка.
        """
        _check_id(file_id)
        if not self.folder_id:
            raise ValueError("Не задана папка для фото: DRIVE_CARDS_FOLDER_ID пуст")
        try:
            where = _json(
                self._send("GET", f"{FILES_URL}/{file_id}", params={"fields": "id,parents"})
            )
            parents = where.get("parents")
            if not isinstance(parents, list) or self.folder_id not in parents:
                raise DriveError("forbidden", _NOT_IN_FOLDER, reason="notInPhotoFolder")
            reply = _json(
                self._send(
                    "PATCH",
                    f"{FILES_URL}/{file_id}",
                    params={"fields": "id,trashed"},
                    json_body={"trashed": True},
                )
            )
        except DriveError as error:
            if error.kind == "not_found":
                return
            raise
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

    def storage_quota(self) -> StorageQuota:
        """Сколько места на Drive у сервисного аккаунта занято и какой предел.

        Фото в папке на «Моём диске» владельца принадлежат сервисному аккаунту
        и занимают его место. Только числа — ни адресов, ни имён. Только чтение.
        """
        reply = _json(
            self._send("GET", ABOUT_URL, params={"fields": _QUOTA_FIELDS}, all_drives=False)
        )
        quota = reply.get("storageQuota")
        if not isinstance(quota, dict):
            raise DriveError("bad_reply", _BAD_REPLY)
        limit = quota.get("limit")
        return StorageQuota(
            usage=_byte_count(quota.get("usage")),
            limit=None if limit is None else _byte_count(limit),
        )

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
        all_drives: bool = True,
    ) -> requests.Response:
        """Один запрос к Drive: общий диск виден, таймаут стоит, отказ объяснён.

        ``all_drives=False`` — запрос не к файлам (``about``): параметра
        ``supportsAllDrives`` у него нет, и незнакомое Google лучше не слать."""
        session = self._connected()
        drives = {"supportsAllDrives": "true"} if all_drives else {}
        try:
            response = session.request(
                method,
                url,
                params={**params, **drives},
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


def authorized_session(credentials_path: Path, scope: str) -> requests.Session:
    """Сессия сервисного аккаунта с одним охватом доступа.

    Файл ключа читается здесь, сети нет до первого запроса. Отдельного
    таймаута обновления токена нет: google-auth (2.57) обновляет токен с
    таймаутом самого запроса (``functools.partial(self._auth_request,
    timeout=…)``), а свой ``refresh_timeout`` лишь хранит. Таймаут на каждом
    запросе клиента закрывает и обновление — это проверяет тест.
    ``type: ignore`` — у этих вызовов google-auth нет аннотаций типов.
    """
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials

    credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        str(credentials_path), scopes=[scope]
    )
    session: requests.Session = AuthorizedSession(credentials)  # type: ignore[no-untyped-call]
    return session


def drive_from_settings(settings: Settings) -> DriveClient:
    """Клиент боевого Drive: охват, папка и таймауты — из настроек."""
    path = settings.google_credentials_path
    scope = SCOPES[settings.drive_scope]
    return DriveClient(
        lambda: authorized_session(path, scope),
        folder_id=settings.drive_cards_folder_id,
        timeout=settings.google_timeout,
    )


def _check_id(file_id: str) -> None:
    """Id вклеивается в адрес запроса: «abc/permissions» превратил бы корзину в
    запрос к доступам. Такой id отвергается до сети."""
    if not DRIVE_FILE_ID.fullmatch(file_id):
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


def _byte_count(value: object) -> int:
    """Число байт из ответа Drive: int64 у Google приходит строкой цифр."""
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise DriveError("bad_reply", _BAD_REPLY)
    return int(value)


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
