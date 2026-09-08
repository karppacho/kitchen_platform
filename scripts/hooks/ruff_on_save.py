"""Хук PostToolUse: причёсывает Python сразу после правки.

Дрейф стиля ловится за секунды и без «не забыл ли я запустить линтер».
mypy сюда сознательно не добавлен: на одном файле он медленный и врёт без
полного контекста — его место в pre-commit и CI.

Читает JSON события со stdin, берёт путь файла, молча выходит, если правка
не про Python в backend/.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    raw = (event.get("tool_input") or {}).get("file_path")
    if not raw:
        return 0

    path = Path(raw)
    if path.suffix != ".py":
        return 0
    try:
        path.relative_to(BACKEND)
    except ValueError:
        return 0
    if not path.exists():
        return 0

    for args in (["ruff", "check", "--fix", "-q"], ["ruff", "format", "-q"]):
        subprocess.run(  # noqa: S603
            ["uv", "run", *args, str(path)],
            cwd=BACKEND,
            capture_output=True,
            timeout=30,
            check=False,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
