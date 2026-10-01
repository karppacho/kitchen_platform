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

from fastapi.testclient import TestClient

from kitchen.config import Settings
from kitchen.web.app import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator

LINE = re.compile(
    r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} WARNING kitchen\.web: проверка журнала$", re.M
)


@contextmanager
def fresh_process() -> Iterator[io.StringIO]:
    """Корневой журнал как в только что запущенном процессе — без обработчиков,
    stderr — в памяти; что было, возвращается на выходе.

    Не фикстурой: обработчики захвата pytest вешает на корневой журнал уже
    после подготовки теста — снятые в фикстуре, они вернулись бы к вызову."""
    root = logging.getLogger()
    saved_handlers, saved_level, saved_stderr = root.handlers[:], root.level, sys.stderr
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


SRC = Path(__file__).resolve().parents[2] / "src" / "kitchen"
_LEVELS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})
_SECRET_NAMES = re.compile(
    r"(?i)api_key|secret|token|cookie|password|authorization|credential|private_key|service_role"
)


def _log_calls(tree: ast.AST) -> Iterator[ast.Call]:
    """Вызовы ``log.warning(…)``, ``logger.info(…)``, ``self._logger.error(…)``."""
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
        if node.func.attr in _LEVELS and "log" in name.lower():
            yield node


def test_no_log_call_passes_secrets() -> None:
    """Теперь записи api видны в журнале — значит, в них не должно быть ни
    ключей, ни кук, ни токенов (правило 8 infra-auditor). Ни один ``log.*`` в
    ``kitchen`` не передаёт значений с такими именами; пройдены все."""
    calls = 0
    leaks: list[str] = []
    for path in SRC.rglob("*.py"):
        for call in _log_calls(ast.parse(path.read_text(encoding="utf-8"))):
            calls += 1
            for argument in [*call.args[1:], *(k.value for k in call.keywords)]:
                for node in ast.walk(argument):
                    named = getattr(node, "attr", None) or getattr(node, "id", None)
                    if isinstance(named, str) and _SECRET_NAMES.search(named):
                        leaks.append(f"{path.relative_to(SRC)}:{call.lineno} {named}")

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
