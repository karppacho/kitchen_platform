"""Журнал процесса api: время, уровень и имя у каждой записи — как у воркера.

uvicorn настраивает только свои журналы, корневой остаётся пустым. Без
настройки предупреждения ``kitchen.*`` из api шли в stderr голым текстом —
без времени и уровня, и разбор отказа на сервере превращался в гадание,
когда и насколько это серьёзно.
"""

from __future__ import annotations

import ast
import io
import logging
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from kitchen.config import Settings
from kitchen.logs import configure_logging
from kitchen.web.app import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

LINE = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} WARNING kitchen\.web: проверка журнала$", re.M
)


_CHATTY = ("httpx", "httpcore")


@contextmanager
def fresh_process() -> Iterator[io.StringIO]:
    """Корневой журнал как в только что запущенном процессе — без обработчиков,
    stderr — в памяти; что было, возвращается на выходе.

    Не фикстурой: обработчики захвата pytest вешает на корневой журнал уже
    после подготовки теста — снятые в фикстуре, они вернулись бы к вызову."""
    root = logging.getLogger()
    saved_handlers, saved_level, saved_stderr = root.handlers[:], root.level, sys.stderr
    saved_levels = {name: logging.getLogger(name).level for name in _CHATTY}
    root.handlers = []
    stderr = io.StringIO()
    sys.stderr = stderr
    try:
        yield stderr
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        for name, level in saved_levels.items():
            logging.getLogger(name).setLevel(level)
        sys.stderr = saved_stderr


def test_api_start_gives_kitchen_records_time_level_and_name() -> None:
    """При старте приложения журнал настроен: запись ``kitchen.web`` — со
    временем, уровнем и именем; сведения (INFO) тоже видны, как у воркера."""
    with fresh_process() as stderr:
        with TestClient(create_app(Settings(_env_file=None, app_env="test"))):
            logging.getLogger("kitchen.web").warning("проверка журнала")
            logging.getLogger("kitchen.cards.submit").info("строка уже записана")
        out = stderr.getvalue()

    assert LINE.search(out), out
    assert " INFO kitchen.cards.submit: строка уже записана" in out


@pytest.mark.parametrize("name", _CHATTY)
def test_chatty_http_libraries_are_quiet_below_warning(name: str) -> None:
    """Строка на каждый запрос к GoTrue и polza.ai — шум: INFO от ``httpx`` и
    ``httpcore`` в журнал не попадает, предупреждение — попадает; ``kitchen.*``
    по-прежнему от INFO."""
    with fresh_process() as stderr:
        configure_logging()
        configure_logging()
        logging.getLogger(name).info("HTTP Request: POST https://example.invalid")
        logging.getLogger(name).warning("сбой соединения")
        logging.getLogger("kitchen.cards.submit").info("строка уже записана")
        out = stderr.getvalue()

    assert "HTTP Request" not in out
    assert f" WARNING {name}: сбой соединения" in out
    assert " INFO kitchen.cards.submit: строка уже записана" in out


SRC = Path(__file__).resolve().parents[2] / "src" / "kitchen"
_LEVELS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})
_SECRET_NAMES = re.compile(
    r"(?i)api_key|secret|token|cookie|password|authorization|credential|private_key|service_role"
    r"|headers"
)
_SECRET_TEXT = re.compile(r"(?i)cookie|authorization|x-api-key|api-key")


def _log_calls(tree: ast.AST) -> Iterator[ast.Call]:
    """Вызовы ``log.warning(…)``, ``logger.info(…)``, ``self._logger.error(…)``."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
        if node.func.attr in _LEVELS and "log" in name.lower():
            yield node


def _is_secret_text(value: object) -> bool:
    return isinstance(value, str) and _SECRET_TEXT.search(value) is not None


def _single_header(expression: ast.AST) -> set[int]:
    """Узлы ``….headers`` в ``….headers.get("имя")`` с постоянным именем: в запись
    уходит одно названное значение, а не заголовки целиком. Само имя проверяет
    строковое правило — ``.get("cookie")`` останется утечкой."""
    return {
        id(node.func.value)
        for node in ast.walk(expression)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
    }


def _leaks_in(expression: ast.AST) -> Iterator[str]:
    """Имена и строки внутри одного выражения — то, что уйдёт в запись."""
    single = _single_header(expression)
    for node in ast.walk(expression):
        if id(node) in single:
            continue
        named = getattr(node, "attr", None) or getattr(node, "id", None)
        if isinstance(named, str) and _SECRET_NAMES.search(named):
            yield named
        if isinstance(node, ast.Constant) and _is_secret_text(node.value):
            yield repr(node.value)


def _leaks(tree: ast.AST) -> list[str]:
    """Места, где ``log.*`` передаёт секретное: имя (``token``, ``headers``),
    строку («cookie», «authorization») в аргументах или в подстановках f-строки.

    Текст самого сообщения (``args[0]``, постоянные куски f-строки) не
    проверяется: «кука не пришла» в тексте — не утечка."""
    found: list[str] = []
    for call in _log_calls(tree):
        arguments = [*call.args[1:], *(k.value for k in call.keywords)]
        if call.args and isinstance(call.args[0], ast.JoinedStr):
            arguments += [v.value for v in call.args[0].values if isinstance(v, ast.FormattedValue)]
        for argument in arguments:
            found += [f"{call.lineno} {leak}" for leak in _leaks_in(argument)]
    return found


@pytest.mark.parametrize(
    "source",
    [
        'log.warning("x %s", request.headers)',
        'log.info("x %s", request.headers.get("cookie"))',
        'log.info("x %s", request.headers.get("Authorization"))',
        'log.warning(f"x {token}")',
        'log.warning(f"x {request.headers}")',
        'log.info("x %s", request.headers.get(name))',
        'log.warning("x", extra={"v": "x-api-key"})',
    ],
)
def test_leaks_finds_secrets_in_names_strings_and_fstrings(source: str) -> None:
    assert _leaks(ast.parse(source)) != []


@pytest.mark.parametrize(
    "source",
    [
        'log.warning(f"x {row_id}")',
        'log.warning("x %s", request.headers.get("origin"))',
        'log.warning("кука не пришла")',
        'log.warning(f"кука не пришла, authorization нет {row_id}")',
        'log.info("x %s", draft.id)',
    ],
)
def test_leaks_ignores_harmless_calls(source: str) -> None:
    assert _leaks(ast.parse(source)) == []


def test_no_log_call_passes_secrets() -> None:
    """Теперь записи api видны в журнале — значит, в них не должно быть ни
    ключей, ни кук, ни токенов, ни заголовков запроса (правило 8 infra-auditor).
    Ни один ``log.*`` в ``kitchen`` не передаёт такого; пройдены все."""
    calls = 0
    leaks: list[str] = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls += sum(1 for _ in _log_calls(tree))
        leaks += [f"{path.relative_to(SRC)}:{leak}" for leak in _leaks(tree)]

    assert calls > 30, "храповик не нашёл вызовов журнала — сломан поиск"
    assert leaks == []


def test_start_twice_does_not_double_records() -> None:
    """Повторный старт (второе приложение в процессе) не дублирует строки."""
    with fresh_process() as stderr:
        for _ in range(2):
            with TestClient(create_app(Settings(_env_file=None, app_env="test"))):
                pass
        logging.getLogger("kitchen.web").warning("проверка журнала")
        out = stderr.getvalue()

    assert len(LINE.findall(out)) == 1, out
