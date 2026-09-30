"""Фальшивый Google Drive: REST v3 на уровне транспорта requests.

Клиент Drive ходит обычной сессией requests (в бою — AuthorizedSession, её
наследник). Фальшивка — адаптер транспорта, который монтируется в сессию
вместо сети: код отправляет те же байты, те же параметры и тот же таймаут,
что ушли бы в Google, а фальшивка разбирает их так, как разбирает Google.
Мок «вернуть вот это» проверял бы только наши ожидания.

Воспроизводится то, на что опирается код:

* загрузка — только ``uploadType=multipart``, тело ``multipart/related``:
  первая часть — JSON с метаданными, вторая — содержимое файла;
* файл общего диска без ``supportsAllDrives=true`` для запроса не
  существует — 404 ``notFound``, как у Google;
* файл без папки или в папке не на общем диске ложится в «Мой диск»
  сервисного аккаунта, а своего места у него нет — 403
  ``storageQuotaExceeded``;
* метаданные загрузки — только ``name``, ``mimeType``, ``parents``,
  ``appProperties``; ``appProperties`` — не больше 124 байт на пару;
* сколько байт файла клиент прочитал из ответа — :attr:`FakeDrive.served`;
* ошибки — телом ``{"error": {"errors": [{"reason": …}], "code": …}}``;
  любой отказ (``accessNotConfigured``, ``storageQuotaExceeded``…) тест
  заказывает через :meth:`FakeDrive.fail_next`, обрыв сети — через
  :meth:`FakeDrive.drop_next`;
* запись в ``/permissions`` фальшивка выполняет, как выполнил бы Google, и
  запоминает: проверять, что её не было, — дело теста.

Каждый запрос попадает в :attr:`FakeDrive.sent` — метод, путь, параметры,
заголовки, тело и таймаут. Запрос, которого фальшивка не моделирует, —
AssertionError, чтобы непонятное не проходило молча.
"""

from __future__ import annotations

import io
import itertools
import json
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

import requests
from requests.adapters import BaseAdapter

HOST = "www.googleapis.com"
FILES = "/drive/v3/files"
UPLOAD = "/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"

FOLDER_ID = "photos-folder"
SHARED_DRIVE_ID = "shared-drive-1"
ROBOT = "robot@example.iam.gserviceaccount.com"
"""Выдуманный адрес сервисного аккаунта платформы."""
CHEF = "chef@example.com"

_PROPERTY_LIMIT = 124
"""Байт на пару ключ–значение в appProperties — предел Google."""

_UPLOAD_KEYS = frozenset({"name", "mimeType", "parents", "appProperties"})
"""Метаданные загрузки, которые фальшивка понимает. Всё прочее (например,
``copyRequiresWriterPermission`` или ``writersCanShare``) — AssertionError:
фальшивка, молча принимающая незнакомое, пропустила бы и опасное."""

_MESSAGES = {
    "accessNotConfigured": (
        "Google Drive API has not been used in project 000000000000 before or it is "
        "disabled. Enable it by visiting the Google Cloud console, then retry."
    ),
    "storageQuotaExceeded": (
        "Service Accounts do not have storage quota. Leverage shared drives, "
        "or use OAuth delegation instead."
    ),
    "insufficientFilePermissions": "The user does not have sufficient permissions for this file.",
    "userRateLimitExceeded": "User rate limit exceeded.",
    "backendError": "Backend Error",
}


@dataclass(frozen=True, slots=True)
class Sent:
    """Запрос, как его увидел бы Google."""

    method: str
    path: str
    params: dict[str, str]
    headers: dict[str, str]
    body: bytes
    timeout: object


@dataclass(slots=True)
class FakeFile:
    """Файл или папка фальшивого Drive."""

    id: str
    name: str
    mime_type: str
    parents: list[str] = field(default_factory=list)
    drive_id: str | None = None
    """Общий диск, на котором лежит файл; ``None`` — «Мой диск»."""
    content: bytes = b""
    app_properties: dict[str, str] = field(default_factory=dict)
    trashed: bool = False
    permissions: list[dict[str, object]] = field(default_factory=list)
    can_edit: bool = True
    can_add_children: bool = False
    own: bool = False
    """Создан через этот Drive — только такие видит доступ ``drive.file``."""
    metadata: dict[str, object] = field(default_factory=dict)
    """Метаданные, с которыми файл загрузили, — как пришли в запросе."""


def closed_folder_permissions() -> list[dict[str, object]]:
    """Доступ «Ограниченный»: платформа — «Менеджер контента», шеф — «Автор»."""
    return [
        {"id": "p-robot", "type": "user", "role": "fileOrganizer", "emailAddress": ROBOT},
        {"id": "p-chef", "type": "user", "role": "writer", "emailAddress": CHEF},
    ]


@dataclass(frozen=True, slots=True)
class _Failure:
    method: str | None
    status: int
    reason: str
    message: str


class _Served(io.BytesIO):
    """Тело ответа с файлом, которое считает, сколько из него прочитали."""

    def __init__(self, content: bytes, drive: FakeDrive) -> None:
        super().__init__(content)
        self._drive = drive

    def read(self, size: int | None = -1) -> bytes:
        data = super().read(size)
        self._drive.served += len(data)
        return data


class _Refusal(Exception):  # noqa: N818 — не ошибка фальшивки, а ответ Google
    def __init__(self, status: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message


class FakeDrive(BaseAdapter):
    """Дублёр Drive REST v3.

    По умолчанию в нём одна папка :data:`FOLDER_ID` на общем диске, закрытая
    (:func:`closed_folder_permissions`), и в неё можно добавлять файлы.
    ``only_own_files=True`` — так Drive отвечает приложению с доступом
    ``drive.file``: всё, что создано не им, для него не существует.
    """

    def __init__(self, *, only_own_files: bool = False) -> None:
        super().__init__()
        self.files: dict[str, FakeFile] = {}
        self.sent: list[Sent] = []
        self.served = 0
        """Сколько байт содержимого файлов клиент реально прочитал из ответов."""
        self.only_own_files = only_own_files
        self._failures: list[_Failure] = []
        self._drops: list[Exception] = []
        self._ids = itertools.count(1)
        self.add(
            FakeFile(
                FOLDER_ID,
                "Карточки ингредиентов — фото",
                FOLDER_MIME,
                drive_id=SHARED_DRIVE_ID,
                can_add_children=True,
                permissions=closed_folder_permissions(),
            )
        )

    # --- что заказывает тест ------------------------------------------------
    def add(self, file: FakeFile) -> FakeFile:
        self.files[file.id] = file
        return file

    @property
    def folder(self) -> FakeFile:
        return self.files[FOLDER_ID]

    def session(self) -> requests.Session:
        """Сессия, у которой вместо сети — эта фальшивка."""
        session = requests.Session()
        session.mount("https://", self)
        session.mount("http://", self)
        return session

    def fail_next(
        self, status: int, reason: str, *, method: str | None = None, message: str = ""
    ) -> None:
        """Следующий запрос (с этим методом, если задан) получит отказ Google."""
        text = message or _MESSAGES.get(reason, reason)
        self._failures.append(_Failure(method, status, reason, text))

    def drop_next(self, error: Exception) -> None:
        """Следующий запрос оборвётся в сети — до Google он не дошёл."""
        self._drops.append(error)

    def permission_writes(self) -> list[Sent]:
        """Запросы, которые меняли бы доступ: всё к ``/permissions``, кроме чтения."""
        return [s for s in self.sent if "/permissions" in s.path and s.method != "GET"]

    # --- транспорт ----------------------------------------------------------
    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float, float] | tuple[float, None] | None = None,
        verify: bool | str = True,
        cert: bytes | str | tuple[bytes | str, bytes | str] | None = None,
        proxies: object = None,
    ) -> requests.Response:
        url = urlsplit(request.url or "")
        assert url.netloc == HOST, f"фальшивка — это только {HOST}, а не «{url.netloc}»"
        body = request.body or b""
        sent = Sent(
            method=request.method or "",
            path=url.path,
            params=dict(parse_qsl(url.query, keep_blank_values=True)),
            headers=dict(request.headers),
            body=body.encode() if isinstance(body, str) else body,
            timeout=timeout,
        )
        self.sent.append(sent)
        if self._drops:
            raise self._drops.pop(0)
        failure = self._take_failure(sent.method)
        try:
            if failure is not None:
                raise _Refusal(failure.status, failure.reason, failure.message)
            status, content, content_type = self._route(sent)
        except _Refusal as refusal:
            error = {
                "errors": [
                    {"domain": "global", "reason": refusal.reason, "message": refusal.message}
                ],
                "code": refusal.status,
                "message": refusal.message,
            }
            status = refusal.status
            content = json.dumps({"error": error}).encode()
            content_type = "application/json; charset=UTF-8"
        response = _response(request, status, content, content_type)
        if status == 200 and sent.params.get("alt") == "media":
            response.raw = _Served(content, self)
        return response

    def close(self) -> None:
        """Соединений нет — закрывать нечего."""

    # --- Drive API ----------------------------------------------------------
    def _route(self, sent: Sent) -> tuple[int, bytes, str]:
        if sent.path == UPLOAD and sent.method == "POST":
            return _json(self._upload(sent))
        assert sent.path.startswith(FILES + "/"), f"фальшивка не моделирует {sent.path}"
        file_id, _, tail = sent.path[len(FILES) + 1 :].partition("/")
        file = self._lookup(sent, file_id)
        if tail == "":
            if sent.method == "GET" and sent.params.get("alt") == "media":
                return self._media(file)
            if sent.method == "GET":
                return _json(_resource(file, sent.params.get("fields")))
            if sent.method == "PATCH":
                return _json(self._patch(file, sent))
            if sent.method == "DELETE":
                del self.files[file.id]
                return 204, b"", "text/plain"
        if tail == "permissions" and sent.method == "GET":
            return _json(_permission_page(file, sent.params))
        if tail == "permissions" and sent.method == "POST":
            permission = dict(json.loads(sent.body))
            permission["id"] = f"p-{next(self._ids)}"
            file.permissions.append(permission)
            return _json(permission)
        if tail.startswith("permissions/") and sent.method in ("PATCH", "DELETE"):
            return 204, b"", "text/plain"
        raise AssertionError(f"фальшивка не моделирует {sent.method} {sent.path}")

    def _lookup(self, sent: Sent, file_id: str) -> FakeFile:
        """Файл по id — с теми же «не существует», что у Google."""
        file = self.files.get(file_id)
        if (
            file is None
            or (file.drive_id is not None and sent.params.get("supportsAllDrives") != "true")
            or (self.only_own_files and not file.own)
        ):
            raise _Refusal(404, "notFound", f"File not found: {file_id}.")
        return file

    def _upload(self, sent: Sent) -> dict[str, object]:
        if sent.params.get("uploadType") != "multipart":
            raise _Refusal(400, "badRequest", "Invalid value for uploadType")
        content_type = sent.headers.get("Content-Type", "")
        kind, _, rest = content_type.partition(";")
        boundary = rest.strip().removeprefix("boundary=").strip('"')
        if kind.strip() != "multipart/related" or not rest.strip().startswith("boundary="):
            raise _Refusal(400, "badContent", f"Unsupported content type: {content_type}")
        parts = _split_multipart(sent.body, boundary)
        if len(parts) != 2:
            raise _Refusal(400, "badContent", "Multipart body must have exactly two parts")
        (meta_headers, meta_body), (media_headers, media) = parts
        if not meta_headers.get("content-type", "").startswith("application/json"):
            raise _Refusal(400, "badContent", "First part must be JSON metadata")
        metadata = json.loads(meta_body)
        assert isinstance(metadata, dict), "метаданные файла — JSON-объект"
        unknown = set(metadata) - _UPLOAD_KEYS
        assert not unknown, f"фальшивка не моделирует метаданные загрузки {sorted(unknown)}"

        parents = [str(p) for p in metadata.get("parents", [])]
        folders = [self._lookup(sent, p) for p in parents]
        drive_id = folders[0].drive_id if folders else None
        for folder in folders:
            if folder.mime_type != FOLDER_MIME:
                raise _Refusal(400, "invalidParent", f"Parent is not a folder: {folder.id}")
            if not folder.can_add_children:
                raise _Refusal(
                    403, "insufficientFilePermissions", _MESSAGES["insufficientFilePermissions"]
                )
        if drive_id is None:
            raise _Refusal(403, "storageQuotaExceeded", _MESSAGES["storageQuotaExceeded"])

        properties = {str(k): str(v) for k, v in metadata.get("appProperties", {}).items()}
        for key, value in properties.items():
            if len(key.encode()) + len(value.encode()) > _PROPERTY_LIMIT:
                raise _Refusal(
                    400,
                    "badRequest",
                    f"The limit of {_PROPERTY_LIMIT} bytes for a property's key and value "
                    "has been exceeded",
                )
        file = self.add(
            FakeFile(
                id=f"fake-file-{next(self._ids)}",
                name=str(metadata.get("name", "Untitled")),
                mime_type=str(metadata.get("mimeType") or media_headers.get("content-type", "")),
                parents=parents,
                drive_id=drive_id,
                content=media,
                app_properties=properties,
                own=True,
                metadata=dict(metadata),
            )
        )
        return _resource(file, sent.params.get("fields"))

    def _media(self, file: FakeFile) -> tuple[int, bytes, str]:
        if file.mime_type.startswith("application/vnd.google-apps."):
            raise _Refusal(
                403, "fileNotDownloadable", "Only files with binary content can be downloaded"
            )
        return 200, file.content, file.mime_type

    def _patch(self, file: FakeFile, sent: Sent) -> dict[str, object]:
        assert sent.headers.get("Content-Type", "").startswith("application/json"), (
            "PATCH файла — JSON-тело"
        )
        changes = json.loads(sent.body)
        unknown = set(changes) - {"trashed", "name"}
        assert not unknown, f"фальшивка не моделирует изменение {sorted(unknown)}"
        if "trashed" in changes:
            file.trashed = bool(changes["trashed"])
        if "name" in changes:
            file.name = str(changes["name"])
        return _resource(file, sent.params.get("fields"))

    def _take_failure(self, method: str) -> _Failure | None:
        for index, failure in enumerate(self._failures):
            if failure.method is None or failure.method == method:
                return self._failures.pop(index)
        return None


def _resource(file: FakeFile, fields: str | None) -> dict[str, object]:
    """Файл в представлении Drive; ``fields`` выбирает, что вернуть."""
    full: dict[str, object] = {
        "kind": "drive#file",
        "id": file.id,
        "name": file.name,
        "mimeType": file.mime_type,
        "parents": list(file.parents),
        "trashed": file.trashed,
        "capabilities": {
            "canEdit": file.can_edit,
            "canAddChildren": file.can_add_children,
            "canTrash": file.can_edit,
        },
    }
    if file.drive_id is not None:
        full["driveId"] = file.drive_id
    if file.app_properties:
        full["appProperties"] = dict(file.app_properties)
    if not file.mime_type.startswith("application/vnd.google-apps."):
        full["size"] = str(len(file.content))
    if fields is None:
        return {k: full[k] for k in ("kind", "id", "name", "mimeType")}
    return _select(full, fields)


def _permission_page(file: FakeFile, params: dict[str, str]) -> dict[str, object]:
    size = int(params.get("pageSize", "100"))
    start = int(params.get("pageToken", "0"))
    page: dict[str, object] = {"kind": "drive#permissionList"}
    page["permissions"] = file.permissions[start : start + size]
    if start + size < len(file.permissions):
        page["nextPageToken"] = str(start + size)
    fields = params.get("fields")
    return page if fields is None else _select(page, fields)


def _select(resource: dict[str, object], fields: str) -> dict[str, object]:
    """Частичный ответ Google: ``id,name,capabilities(canEdit)``."""
    chosen: dict[str, object] = {}
    for item in _split_fields(fields):
        name, _, inner = item.partition("(")
        if name not in resource:
            continue
        value = resource[name]
        if inner:
            sub = inner.removesuffix(")")
            if isinstance(value, dict):
                value = _select(value, sub)
            elif isinstance(value, list):
                value = [_select(v, sub) if isinstance(v, dict) else v for v in value]
        chosen[name] = value
    return chosen


def _split_fields(fields: str) -> list[str]:
    """Запятые верхнего уровня: ``a,b(c,d)`` → ``["a", "b(c,d)"]``."""
    items: list[str] = []
    depth = 0
    current = ""
    for char in fields:
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
            continue
        depth += {"(": 1, ")": -1}.get(char, 0)
        current += char
    if current.strip():
        items.append(current.strip())
    return items


def _split_multipart(body: bytes, boundary: str) -> list[tuple[dict[str, str], bytes]]:
    """Части тела ``multipart/related`` по RFC 2046: разделитель — CRLF и
    ``--граница``, последний — ``--граница--``."""
    delimiter = b"--" + boundary.encode()
    closing = b"\r\n" + delimiter + b"--"
    end = body.rfind(closing)
    if not boundary or not body.startswith(delimiter + b"\r\n") or end < 0:
        raise _Refusal(400, "badContent", "Malformed multipart body")
    parts: list[tuple[dict[str, str], bytes]] = []
    for chunk in body[len(delimiter) + 2 : end].split(b"\r\n" + delimiter + b"\r\n"):
        head, separator, content = chunk.partition(b"\r\n\r\n")
        if not separator:
            raise _Refusal(400, "badContent", "Multipart part without headers")
        headers: dict[str, str] = {}
        for line in head.decode("ascii").split("\r\n"):
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        parts.append((headers, content))
    return parts


def _json(payload: dict[str, object]) -> tuple[int, bytes, str]:
    return 200, json.dumps(payload).encode(), "application/json; charset=UTF-8"


def _response(
    request: requests.PreparedRequest, status: int, content: bytes, content_type: str
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.headers["Content-Type"] = content_type
    response.raw = io.BytesIO(content)
    response.url = request.url or ""
    response.request = request
    response.reason = "OK" if status < 400 else "Error"
    return response
