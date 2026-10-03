"""Проверить настройку «Нового ингредиента»: ключ, таблицы, папку фото, загрузку.

Запускать на сервере после настройки папки фото и после любой правки
доступов — к таблицам, к папке, к ключу сервисного аккаунта::

    docker compose -f infra/docker-compose.yml exec api python scripts/check_cards_setup.py

С ключом бота — сравнить аккаунты. Ключ передаётся через stdin, второй
копии внутри контейнера не нужно (``-T`` — чтобы stdin дошёл до скрипта).
Печатается только «тот же аккаунт» или «другой аккаунт», без адресов::

    docker compose -f infra/docker-compose.yml exec -T api \\
        python scripts/check_cards_setup.py --bot-key - < /путь/к/ключу-бота.json

С ``--llm`` — ещё и пробный вызов модели распознавания через polza.ai тем же
клиентом и той же моделью (``LLM_VISION_MODEL``), что распознают этикетки.
Печатаются адрес, модель, цена вызова в рублях и время; ключ — никогда.
Картинка пробы — серый квадрат, собранный самим скриптом, не фото. Вызов
стоит денег (копейки), поэтому только по флагу::

    docker compose -f infra/docker-compose.yml exec api \\
        python scripts/check_cards_setup.py --llm

Что проверяется:

* адрес сервисного аккаунта платформы — печатается: его вписывают в доступ
  папки и таблиц. Сам ключ не печатается никогда;
* книги карточек и кухни — у платформы право редактирования: она пишет в
  «Лист1» карточек и в ручные колонки ING кухни (ADR-0003). Какие книги
  открыты, скрипт берёт из ворот записи: у закрытой книги право
  редактирования — предупреждение, лишнее право лучше снять;
* папка фото — это папка и не в корзине; где лежит: на «Моём диске»
  владельца (так сейчас, решение 01.10: фото загружает сервисный аккаунт,
  место — его; печатается, сколько занято и свободно) или на общем диске;
  кому открыта: открыта по ссылке или всему домену на изменение — ошибка
  (фото может удалить кто угодно), только на чтение — предупреждение (фото
  видны всем, у кого есть ссылка, или всем в домене); в неё можно добавлять
  файлы; кто имеет к ней доступ. Роли названы так, как их пишет Drive там,
  где лежит папка: ``writer`` на общем диске — «Автор», на «Моём диске» —
  «Редактор»; «Менеджер контента» бывает только на общем диске;
* пробная загрузка 1 КБ JPEG → скачивание → корзина: сначала с узким доступом
  ``drive.file``; если ему не хватает прав или он не видит папку — с
  ``drive``. Проба прошла только с ``drive``, а выставлен ``drive.file`` —
  ошибка: фото не сохранятся. Выставлено шире нужного — предупреждение;
* с ``--llm`` — пробный вызов модели: ключ принят, деньги на счёте есть,
  модель найдена, связь есть.

Скрипт ничего не меняет в доступах и таблицах — только читает (кроме самой
пробы в папке фото); исправляет человек. Код выхода 0 — всё обязательное
прошло (с ``--llm`` — и пробный вызов); предупреждения его не портят.
"""

from __future__ import annotations

import argparse
import codecs
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic import ValidationError

from kitchen.config import Settings, load_settings
from kitchen.llm.polza import LlmError, polza_from_settings
from kitchen.sync.drive import (
    API_DISABLED_REASONS,
    INSPECT_SCOPE,
    SCOPES,
    DriveClient,
    DriveError,
    DriveScope,
    authorized_session,
    folder_access,
)
from kitchen.sync.ownership import WRITE_OPEN

RULE = "─" * 78
FOLDER_MIME = "application/vnd.google-apps.folder"
ORDER: tuple[DriveScope, ...] = ("drive.file", "drive")
"""Сначала узкий доступ: хватит его — шире не нужно."""

SCOPE_KINDS = frozenset({"not_found", "forbidden"})
"""Отказы, которые лечит более широкий доступ. Остальные — нет: Google не
ответил, нет места, ответ не разобран — расширять доступ бессмысленно."""

Outcome = Literal["ok", "narrow", "stop"]
"""Итог пробы с одним доступом: прошла; не хватило доступа; дальше не идти."""

_COMMON_ROLES = {"owner": "Владелец", "commenter": "Комментатор", "reader": "Читатель"}


@dataclass(frozen=True, slots=True)
class Place:
    """Где лежит папка фото. Роли Drive называет по-разному в зависимости от
    места, а администратор ищет в интерфейсе ту роль, что напечатана."""

    roles: Mapping[str, str]
    """Роль API → её название в интерфейсе Drive."""
    needed: str
    """Роль сервисного аккаунта, с которой он добавляет файлы в папку и
    выбрасывает свои в корзину."""


SHARED_DRIVE = Place(
    roles={
        **_COMMON_ROLES,
        "organizer": "Менеджер",
        "fileOrganizer": "Менеджер контента",
        "writer": "Автор",
    },
    needed="Менеджер контента",
)
"""Общий диск: ``writer`` — «Автор», выбрасывать в корзину может «Менеджер контента»."""

MY_DRIVE = Place(roles={**_COMMON_ROLES, "writer": "Редактор"}, needed="Редактор")
"""«Мой диск»: «Менеджеров» нет, ``writer`` — «Редактор»; ему хватает и на
загрузку, и на корзину своих фото."""


def _probe_jpeg() -> bytes:
    """1 КБ с подписью JPEG: начало FFD8FF, конец FFD9 — как проверяет платформа.

    Картинки внутри нет: заголовок JFIF, комментарий-заполнитель и конец.
    Drive хранит байты как есть, а проверяется, что они вернутся те же.
    """
    head = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    tail = b"\xff\xd9"
    filler = 1024 - len(head) - len(tail) - 4
    comment = b"kitchen-platform: setup check".ljust(filler, b".")
    return head + b"\xff\xfe" + (filler + 2).to_bytes(2, "big") + comment + tail


PROBE = _probe_jpeg()


def _probe_image(side: int = 64) -> bytes:
    """Серый квадрат ``side``×``side`` — настоящий JPEG, собранный здесь же.

    Модели нужна картинка, которую она сможет открыть; фото для этого не
    нужно, а библиотек для картинок у платформы нет. Базовый JPEG в оттенках
    серого, все пиксели 128: после сдвига уровня у каждого блока 8×8 один
    коэффициент — нулевой DC — и сразу «конец блока». Таблицы Хаффмана — по
    одному коду длиной в бит: «0» — DC без изменения, «0» — конец блока. Блок
    — два нулевых бита, вся картинка — (side/8)² × 2 бит нулей. 64 пикселя —
    с запасом: совсем крошечные картинки (у Qwen — меньше 11 пикселей по
    стороне) модели зрения отвергают.
    """

    def segment(marker: int, body: bytes) -> bytes:
        return bytes((0xFF, marker)) + (len(body) + 2).to_bytes(2, "big") + body

    one_code = bytes((1, *([0] * 15)))  # один код длиной в один бит
    blocks = (side // 8) ** 2
    return b"".join(
        (
            b"\xff\xd8",
            segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"),
            segment(0xDB, b"\x00" + b"\x01" * 64),  # таблица квантования из единиц
            segment(0xC0, b"\x08" + side.to_bytes(2, "big") * 2 + b"\x01\x01\x11\x00"),
            segment(0xC4, b"\x00" + one_code + b"\x00"),  # DC: «0» — разница 0
            segment(0xC4, b"\x10" + one_code + b"\x00"),  # AC: «0» — конец блока
            segment(0xDA, b"\x01\x01\x00\x00\x3f\x00"),
            bytes(blocks * 2 // 8),
            b"\xff\xd9",
        )
    )


LLM_PROBE_IMAGE = _probe_image()
LLM_PROBE_SYSTEM = 'Это проверка связи платформы с моделью. Ответь JSON-объектом {"ok": true}.'
LLM_PROBE_PROMPT = 'Верни {"ok": true}.'
LLM_PROBE_MAX_TOKENS = 50

NOT_UTF8 = (
    "файл не в кодировке UTF-8 — похоже, его пересохранили Блокнотом как «Юникод» "
    "(UTF-16); сохраните ключ в UTF-8"
)
BOM = codecs.BOM_UTF8.decode("utf-8")
"""Метка порядка байтов (U+FEFF). Не литералом: невидимый символ в исходнике
легко потерять, и проверка превратилась бы в ``startswith("")`` — «с BOM» любой ключ."""
WITH_BOM = (
    "в начале файла метка BOM — так Блокнот сохраняет «UTF-8 с BOM»; сохраните ключ в UTF-8 без BOM"
)


class Report:
    """Вывод по разделам и итог: ошибка портит код выхода, предупреждение — нет."""

    def __init__(self) -> None:
        self.failed = False
        self.warned = False

    def section(self, title: str) -> None:
        print()
        print(title)
        print(RULE)

    def ok(self, text: str) -> None:
        print(f"  OK        {text}")

    def warn(self, text: str) -> None:
        self.warned = True
        print(f"  ВНИМАНИЕ  {text}")

    def fail(self, text: str) -> None:
        self.failed = True
        print(f"  ОШИБКА    {text}")

    def note(self, text: str) -> None:
        print(f"            {text}".rstrip())

    def finish(self) -> int:
        print()
        print(RULE)
        if self.failed:
            print("ИТОГ: есть ошибки — «Новый ингредиент» не заработает, пока их не исправить.")
            return 1
        if self.warned:
            print("ИТОГ: обязательное в порядке; прочтите предупреждения выше.")
            return 0
        print("ИТОГ: всё в порядке.")
        return 0


class _DriveOff(Exception):  # noqa: N818 — не сбой скрипта, а сигнал «дальше не идти»
    """Drive API выключен: все дальнейшие запросы получат тот же отказ."""

    def __init__(self, error: DriveError) -> None:
        super().__init__(str(error))
        self.error = error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверить настройку «Нового ингредиента»")
    parser.add_argument(
        "--bot-key",
        metavar="ПУТЬ|-",
        help=(
            "ключ сервисного аккаунта бота — сказать, тот же это аккаунт или другой; "
            "«-» — читать ключ из stdin: exec -T api python scripts/check_cards_setup.py "
            "--bot-key - < bot.json"
        ),
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="ещё и пробный вызов модели распознавания через polza.ai (стоит копейки)",
    )
    args = parser.parse_args(argv)
    report = Report()

    print(RULE)
    print("ПРОВЕРКА НАСТРОЙКИ: НОВЫЙ ИНГРЕДИЕНТ")
    print(RULE)

    try:
        settings = load_settings()
    except ValidationError as error:
        _settings_errors(error, report)
        return report.finish()

    email = _account(settings.google_credentials_path, args.bot_key, report)
    if email is not None:
        try:
            _check_books(settings, report)
            place = _check_folder(settings, email, report)
            if place is not None:
                _probe(settings, place, report)
        except _DriveOff as off:
            report.fail(_why(off.error))

    if args.llm:
        _check_llm(settings, report)

    return report.finish()


def _settings_errors(error: ValidationError, report: Report) -> None:
    """Ошибки .env — строками, без трассировки. Значений в них нет: настройки
    прячут входные данные (там могут быть секреты)."""
    report.section("НАСТРОЙКИ")
    for problem in error.errors():
        name = ".".join(str(part) for part in problem["loc"]).upper()
        text = str(problem["msg"]).removeprefix("Value error, ")
        report.fail(text if name in text else f"{name}: {text}")


# ---------------------------------------------------------------------------
# Ключ
# ---------------------------------------------------------------------------
def _account(path: Path, bot_key: str | None, report: Report) -> str | None:
    report.section("СЕРВИСНЫЙ АККАУНТ")
    email, problem = _client_email(*_read(path))
    if email is None:
        report.fail(f"ключ не прочитан — проверьте GOOGLE_CREDENTIALS_PATH ({path}): {problem}")
        return None
    report.ok(f"адрес: {email}")
    report.note("этот адрес вписывают в доступ папки фото и таблиц")
    if bot_key is not None:
        bot, problem = _client_email(*(_read_stdin() if bot_key == "-" else _read(Path(bot_key))))
        if bot is None:
            report.warn(f"ключ бота не прочитан — сравнить аккаунты не вышло: {problem}")
        elif bot == email:
            report.ok("ключ бота: тот же аккаунт — доступы бота у платформы уже есть")
        else:
            report.ok("ключ бота: другой аккаунт — доступы бота платформе не достались")
    return email


def _read(path: Path) -> tuple[str | None, str]:
    """Текст ключа или ``None`` и почему не прочитан."""
    try:
        return path.read_text(encoding="utf-8"), ""
    except UnicodeDecodeError:
        return None, NOT_UTF8
    except OSError:
        return None, "файла нет или его не открыть на чтение"


def _read_stdin() -> tuple[str | None, str]:
    """Ключ из stdin — байтами и строго в UTF-8.

    Текстовый stdin в контейнере разбирает непонятные байты молча (режим
    UTF-8 Python), и ключ в UTF-16 превратился бы в мусор без объяснения.
    """
    raw = getattr(sys.stdin, "buffer", None)
    try:
        return (sys.stdin.read() if raw is None else raw.read().decode("utf-8")), ""
    except UnicodeDecodeError:
        return None, NOT_UTF8
    except OSError:
        return None, "stdin не прочитан"


def _client_email(text: str | None, problem: str) -> tuple[str | None, str]:
    """Только адрес из ключа; остальное содержимое ключа не выходит отсюда."""
    if text is None:
        return None, problem
    if "\x00" in text:
        # UTF-16 без метки порядка байтов — формально UTF-8, но с нулями.
        return None, NOT_UTF8
    if text.startswith(BOM):
        # Разбор JSON у Google на этой метке падает так же, как здесь.
        return None, WITH_BOM
    try:
        data = json.loads(text)
    except ValueError:
        return None, "в файле не JSON — это не ключ сервисного аккаунта"
    email = data.get("client_email") if isinstance(data, dict) else None
    if isinstance(email, str) and email:
        return email, ""
    return None, "в файле нет client_email — это не ключ сервисного аккаунта"


# ---------------------------------------------------------------------------
# Таблицы
# ---------------------------------------------------------------------------
def _check_books(settings: Settings, report: Report) -> None:
    """Право редактирования нужно ровно тем книгам, запись в которые открыта.

    Смотрит полный доступ ``drive``, но только чтением (GET): ``drive.file``
    книг не видит — их создал человек, а при доступе «только чтение» Drive,
    возможно, занизил бы право редактирования, и книга кухни молча получила
    бы «только чтение».
    """
    report.section("ТАБЛИЦЫ")
    client = _client(settings, SCOPES["drive"])
    books = (
        ("ingredient_cards", "книга карточек", "SHEETS_ID_INGREDIENT_CARDS"),
        ("kitchen", "книга кухни", "SHEETS_ID_KITCHEN"),
    )
    ids = {
        "ingredient_cards": settings.sheets_id_ingredient_cards,
        "kitchen": settings.sheets_id_kitchen,
    }
    for key, title, setting in books:
        book_id = ids[key]
        writes = key in WRITE_OPEN
        problem = report.fail if writes else report.warn
        if not book_id:
            problem(f"{title}: не задан {setting}")
            continue
        try:
            can_edit = _capability(client.metadata(book_id), "canEdit")
        except DriveError as error:
            _stop_if_disabled(error)
            problem(f"{title}: не открылась — {_why(error)}")
            continue
        if writes and can_edit:
            report.ok(f"{title}: есть право редактирования — платформа сюда пишет")
        elif writes:
            problem(
                f"{title}: нет права редактирования — дайте сервисному аккаунту роль «Редактор»"
            )
        elif can_edit:
            report.warn(
                f"{title}: есть право редактирования, а писать сюда платформе нельзя — "
                "книга закрыта для записи. Хватит роли «Читатель»"
            )
        else:
            report.ok(f"{title}: только чтение — платформа сюда не пишет")


# ---------------------------------------------------------------------------
# Папка фото
# ---------------------------------------------------------------------------
def _check_folder(settings: Settings, email: str, report: Report) -> Place | None:
    """Папка годится для фото? Не папка, в корзине или открыта на изменение —
    пробный файл туда не кладётся: папку сначала чинят (``None``). Годится —
    где она лежит: от этого зависят названия ролей в подсказках.

    Сведения о папке, её доступах и месте аккаунта читает охват «только
    чтение сведений».
    """
    report.section("ПАПКА ФОТО")
    folder_id = settings.drive_cards_folder_id
    if not folder_id:
        report.fail("не задан DRIVE_CARDS_FOLDER_ID — id папки фото")
        return None
    client = _client(settings, INSPECT_SCOPE)
    try:
        folder = client.metadata(folder_id)
        people = client.permissions(folder_id)
    except DriveError as error:
        _stop_if_disabled(error)
        report.fail(f"папка не открылась — {_why(error)}")
        return None

    passed = True
    place = SHARED_DRIVE if folder.get("driveId") else MY_DRIVE
    if folder.get("mimeType") != FOLDER_MIME:
        report.fail("DRIVE_CARDS_FOLDER_ID указывает не на папку")
        passed = False
    elif place is MY_DRIVE:
        # Фото в такой папке принадлежат сервисному аккаунту и занимают его
        # место (решение 01.10, живая проба прошла) — кончиться может оно.
        report.ok("папка на «Моём диске» владельца; место — у сервисного аккаунта")
        _account_space(client, report)
    else:
        report.ok("папка на общем диске")
    if folder.get("trashed") is True:
        report.fail("папка в корзине")
        passed = False
    access = folder_access(people)
    if access == "closed":
        report.ok("закрыта: доступ «Ограниченный», по ссылке посторонний не откроет")
    elif access == "readable_by_link":
        # Право «все, у кого есть ссылка» шире права домена: есть оно — о нём.
        by_link = any(person.get("type") == "anyone" for person in people)
        report.warn(f"фото видны {'всем, у кого есть ссылка' if by_link else 'всем в домене'}")
    else:
        report.fail(
            "папка открыта на изменение всем по ссылке — фото может удалить кто угодно. "
            "Оставьте по ссылке только «Читатель» или поставьте доступ «Ограниченный»"
        )
        passed = False
    # Сведения читаются охватом «только чтение», поэтому здесь — подсказка, а
    # решает пробная загрузка ниже: не прошла ни с одним доступом — ошибка.
    if _capability(folder, "canAddChildren"):
        report.ok("можно добавлять файлы")
    else:
        report.warn(
            "по сведениям Drive, добавлять файлы нельзя — сервисному аккаунту нужна роль "
            f"«{place.needed}»; проверит пробная загрузка"
        )

    report.note("")
    report.note("Доступ к папке:")
    for person in people:
        role = place.roles.get(str(person.get("role")), str(person.get("role")))
        mark = "  ← платформа" if person.get("emailAddress") == email else ""
        report.note(f"  · {role:18} {_who(person)}{mark}")
    return place if passed else None


def _account_space(client: DriveClient, report: Report) -> None:
    """Сколько места у сервисного аккаунта занято и свободно — только числа.

    Подсказка, а не проверка: не узнали — решает пробная загрузка ниже."""
    try:
        quota = client.storage_quota()
    except DriveError as error:
        _stop_if_disabled(error)
        report.note(f"место сервисного аккаунта узнать не удалось — {_why(error)}")
        return
    if quota.limit is None or quota.free is None:
        report.note(f"место сервисного аккаунта: занято {_size(quota.usage)}, предела нет")
        return
    report.note(
        f"место сервисного аккаунта: занято {_size(quota.usage)}, "
        f"свободно {_size(quota.free)} из {_size(quota.limit)}"
    )


_UNITS = (("ГБ", 1024**3), ("МБ", 1024**2), ("КБ", 1024))


def _size(count: int) -> str:
    """Байты — для человека: «1,5 ГБ», «512 МБ», «0 байт». Без float: Decimal."""
    for unit, size in _UNITS:
        if count >= size:
            value = (Decimal(count) / size).quantize(Decimal("0.1"), ROUND_HALF_UP)
            return f"{value:f}".removesuffix(".0").replace(".", ",") + f" {unit}"
    return f"{count} байт"


def _who(person: dict[str, object]) -> str:
    kind = person.get("type")
    if kind == "anyone":
        return "все, у кого есть ссылка"
    if kind == "domain":
        return f"весь домен {person.get('domain', '')}".strip()
    address = str(person.get("emailAddress") or person.get("displayName") or "—")
    return f"группа {address}" if kind == "group" else address


# ---------------------------------------------------------------------------
# Пробная загрузка
# ---------------------------------------------------------------------------
def _probe(settings: Settings, place: Place, report: Report) -> None:
    report.section("ПРОБНАЯ ЗАГРУЗКА: 1 КБ JPEG → скачивание → корзина")
    for scope in ORDER:
        outcome = _try_probe(_client(settings, SCOPES[scope]), scope, place, report)
        if outcome == "ok":
            _advise(scope, settings.drive_scope, report)
            return
        if outcome == "stop":
            return
    report.fail("пробный файл не прошёл ни с одним доступом — сохранять фото платформа не сможет")


def _try_probe(client: DriveClient, scope: DriveScope, place: Place, report: Report) -> Outcome:
    name = f"проверка-настройки_{datetime.now(UTC):%Y-%m-%d_%H-%M-%S}.jpg"
    try:
        file_id = client.upload_jpeg(PROBE, name=name, app_properties={"purpose": "setup-check"})
    except DriveError as error:
        _stop_if_disabled(error)
        if error.kind in SCOPE_KINDS:
            report.note(f"{scope}: загрузка не удалась — {_why(error)}")
            return "narrow"
        # Ответ мог потеряться уже после того, как Google создал файл.
        report.fail(
            f"{scope}: загрузка не удалась — {_why(error)}{_retry(error)} Если в папке "
            f"появился файл «{name}», удалите его руками."
        )
        return "stop"

    outcome: Outcome = "ok"
    try:
        if client.download(file_id, max_bytes=len(PROBE)) != PROBE:
            report.fail(f"{scope}: скачанный файл не совпал с загруженным")
            outcome = "stop"
    except DriveError as error:
        outcome = _step_failed(f"{scope}: скачивание не удалось", error, report)
    try:
        client.trash(file_id)
    except DriveError as error:
        # Роль — только когда корзину запретил сам Google (403). Отказ клиента
        # «файл не из папки фото» и непринятый ключ ролью не лечатся.
        role = (
            f" Сервисному аккаунту нужна роль «{place.needed}»."
            if error.kind == "forbidden" and error.status == 403
            else ""
        )
        report.warn(
            f"{scope}: корзина не удалась — {_why(error)} Пробный файл «{name}» остался "
            f"в папке: удалите его руками.{role}"
        )
        if outcome == "ok":
            outcome = _step_failed(f"{scope}: корзина не удалась", error, report)
    if outcome == "ok":
        report.ok(f"{scope}: загрузка, скачивание и корзина прошли")
    return outcome


def _step_failed(what: str, error: DriveError, report: Report) -> Outcome:
    """Шаг пробы не прошёл: из-за доступа — пробуем шире, иначе дальше не идём."""
    if error.kind in SCOPE_KINDS:
        report.note(f"{what} — {_why(error)}")
        return "narrow"
    report.fail(f"{what} — {_why(error)}{_retry(error)}")
    return "stop"


def _advise(working: DriveScope, configured: DriveScope, report: Report) -> None:
    """Сверить рабочий доступ с настройкой. Уже нужного — фото не сохранятся."""
    if working == configured:
        report.ok(f"DRIVE_SCOPE={working} — так и выставлено")
    elif working == "drive":
        report.fail(
            f"с DRIVE_SCOPE={configured} фото не сохранятся: выставьте DRIVE_SCOPE={working} "
            "и перезапустите api"
        )
    else:
        report.warn(
            f"хватает узкого доступа: выставьте DRIVE_SCOPE={working} в .env сервера "
            f"(сейчас «{configured}») и перезапустите api"
        )


# ---------------------------------------------------------------------------
# Модель распознавания (--llm)
# ---------------------------------------------------------------------------
def _check_llm(settings: Settings, report: Report) -> None:
    """Пробный вызов тем же клиентом и той же моделью, что распознают этикетки.

    Код 0 — только если вызов прошёл: без модели повар заполняет все поля
    руками, и это надо знать до приёмки, а не от повара.
    """
    report.section("МОДЕЛЬ РАСПОЗНАВАНИЯ: пробный вызов polza.ai")
    report.note(f"адрес:  {settings.polza_base_url}")
    report.note(f"модель: {settings.llm_vision_model}")
    client = polza_from_settings(settings)
    if client is None:
        report.fail("не задан POLZA_API_KEY — распознавание этикеток не заработает")
        return
    try:
        with client:
            reply = client.vision_json(
                model=settings.llm_vision_model,
                system=LLM_PROBE_SYSTEM,
                prompt=LLM_PROBE_PROMPT,
                jpeg=LLM_PROBE_IMAGE,
                max_tokens=LLM_PROBE_MAX_TOKENS,
            )
    except LlmError as error:
        report.fail(f"пробный вызов не прошёл — {_llm_why(error, settings)}")
        return
    price = (
        "polza.ai цену не сообщил"
        if reply.cost_rub is None
        else f"{format(reply.cost_rub, 'f').replace('.', ',')} ₽"
    )
    seconds = f"{Decimal(reply.duration_ms) / 1000:.1f}".replace(".", ",")
    report.ok(f"пробный вызов прошёл: цена {price}, время {seconds} с")


def _llm_why(error: LlmError, settings: Settings) -> str:
    """Что чинить администратору — и что ответил polza.ai: код и причину.

    Коды «нет денег» и «нет модели» у polza.ai не проверены, поэтому его
    собственная причина печатается всегда: по ней видно, что случилось на самом
    деле, даже если подсказка не угадала.
    """
    detail = error.detail
    if error.kind == "bad_request" and not (error.status == 400 and error.without_json_mode):
        text = f"polza.ai отклонил запрос: {detail or 'причину не назвал'}"
        detail = ""  # причина уже в тексте — в скобках только код
    elif error.kind == "bad_request":
        text = (
            "polza.ai отклонил запрос и в режиме JSON, и без него — проверьте "
            f"LLM_VISION_MODEL (сейчас «{settings.llm_vision_model}»): модель должна "
            "принимать картинки"
        )
    elif error.kind in _LLM_HINTS:
        text = _LLM_HINTS[error.kind].format(settings=settings)
    else:
        text = str(error)
    said = " ".join(part for part in (str(error.status or ""), detail) if part)
    return f"{text} [{said}]" if said else text


_LLM_HINTS = {
    "key": "ключ не принят — проверьте POLZA_API_KEY",
    "no_money": "нет денег на счёте polza.ai — пополните баланс",
    "not_found": (
        "модель не найдена — проверьте LLM_VISION_MODEL (сейчас «{settings.llm_vision_model}»)"
    ),
    "unavailable": (
        "нет связи с polza.ai или ответа нет дольше {settings.llm_vision_timeout_seconds} с "
        "— проверьте POLZA_BASE_URL и сеть сервера, повторите запуск"
    ),
    "rate": "polza.ai просит подождать — повторите запуск через минуту",
    "bad_reply": (
        "ответ не похож на API polza.ai — проверьте POLZA_BASE_URL (сейчас "
        "«{settings.polza_base_url}»): нужен адрес API, обычно https://api.polza.ai/v1"
    ),
}
"""Что чинить администратору, по виду отказа. ``{settings…}`` подставляется."""


# ---------------------------------------------------------------------------
# Общее
# ---------------------------------------------------------------------------
def _client(settings: Settings, scope: str) -> DriveClient:
    path = settings.google_credentials_path
    return DriveClient(
        lambda: authorized_session(path, scope),
        folder_id=settings.drive_cards_folder_id,
        timeout=settings.google_timeout,
    )


def _capability(resource: dict[str, object], name: str) -> bool:
    capabilities = resource.get("capabilities")
    return isinstance(capabilities, dict) and capabilities.get(name) is True


def _stop_if_disabled(error: DriveError) -> None:
    if error.reason in API_DISABLED_REASONS:
        raise _DriveOff(error)


def _retry(error: DriveError) -> str:
    return " Google не ответил — повторите запуск." if error.kind == "unavailable" else ""


def _why(error: DriveError) -> str:
    """Текст для человека и, для администратора, что именно ответил Google."""
    if error.status is None:
        return str(error)
    return f"{error} [{error.status} {error.reason}".rstrip() + "]"


if __name__ == "__main__":
    raise SystemExit(main())
