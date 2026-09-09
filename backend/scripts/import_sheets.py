"""Перенести содержимое Google-таблиц в базу.

Читает и только читает: пока живы Telegram-боты, они единственные писатели
в общий справочник. Обратной записи здесь нет по построению.

Запуск::

    docker compose -f infra/docker-compose.yml exec api python scripts/import_sheets.py

Импорт идёт одной транзакцией: либо переносится всё, либо ничего.
Наполовину перенесённый справочник хуже неперенесённого — расчёт по нему
выглядит рабочим и даёт неверные числа.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kitchen.config import load_settings
from kitchen.db.session import make_session_factory
from kitchen.sync.client import GspreadClient
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetsReader

RULE = "─" * 78


def main() -> int:
    settings = load_settings()

    reader = SheetsReader(
        GspreadClient(
            settings.google_credentials_path,
            timeout=settings.google_timeout,
            refresh_timeout=settings.google_refresh_timeout,
        ),
        {
            "kitchen": settings.sheets_id_kitchen,
            "competitors": settings.sheets_id_competitors,
            "ingredient_cards": settings.sheets_id_ingredient_cards,
            "tastings": settings.sheets_id_tastings,
        },
    )

    print(RULE)
    print("ИМПОРТ ЛИСТОВ В БАЗУ")
    print(RULE)

    result = Importer(reader, make_session_factory(settings.database_url)).run()

    print()
    print("ПЕРЕНЕСЕНО")
    print(RULE)
    for entity, number in result.counts.items():
        print(f"  {entity:28} {number:>6}")

    if result.unreadable:
        print()
        print("НЕ ПРОЧИТАНО")
        print(RULE)
        for label, reason in result.unreadable.items():
            print(f"  {label}: {reason}")

    if result.warnings:
        print()
        print(f"ЗАМЕЧАНИЯ ({len(result.warnings)})")
        print(RULE)
        print("  Импорт прошёл, но эти строки требуют внимания шефа.")
        for warning in result.warnings[:40]:
            print(f"  · {warning}")
        if len(result.warnings) > 40:
            print(f"  … и ещё {len(result.warnings) - 40}")

    print()
    print(f"Прогон записан: sync_runs.id = {result.run_id}")
    print()
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
