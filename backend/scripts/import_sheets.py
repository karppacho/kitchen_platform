"""Перенести Google-таблицы в базу — один цикл синхронизации вручную.

То же, что воркер делает раз в пять минут (kitchen/sync/cycle.py), но с
принудительным переносом: отпечаток не сравнивается. Правила те же: книга, у
которой лист не прочитан или сдвинулись колонки, НЕ переносится. Раньше
ручной импорт переносил и такую, только написав замечание, — при сдвиге
колонок это значило цену из колонки веса.

Запуск::

    docker compose -f infra/docker-compose.yml exec api python scripts/import_sheets.py

Код выхода 1 — хотя бы одна книга не перенесена из-за сбоя.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kitchen.config import load_settings
from kitchen.db.session import make_session_factory
from kitchen.sync.cycle import BOOK_TITLES, BOOKS, SyncCycle, reader_from
from kitchen.sync.importer import ImportResult

RULE = "─" * 78

_ACTIONS = {
    "imported": "перенесена",
    "unchanged": "без изменений",
    "failed": "НЕ перенесена",
    "stale": "пропущена: другой импорт уже перенёс чтение новее",
}


def _show_import(title: str, imported: ImportResult) -> None:
    """Итог переноса одной книги: у каждой свой перенос и своя запись журнала."""
    print()
    print(f"ПЕРЕНЕСЕНО: {title}")
    print(RULE)
    for entity, number in imported.counts.items():
        print(f"  {entity:28} {number:>6}")
    # Раньше замечаний и своими разделами: это единственный след массового
    # удаления и массовой потери пар, а замечаний «пустой id» в живом ING
    # десятки.
    if imported.presence:
        print()
        print(f"СКРЫТО И ВОЗВРАЩЕНО ({len(imported.presence)})")
        print(RULE)
        for line in imported.presence:
            print(f"  · {line}")
    if imported.links:
        print()
        print(f"ПАРЫ КАРТОЧЕК ({len(imported.links)})")
        print(RULE)
        for line in imported.links:
            print(f"  · {line}")
    if imported.warnings:
        print()
        print(f"ЗАМЕЧАНИЯ ({len(imported.warnings)})")
        print(RULE)
        print("  Перенос прошёл, но эти строки требуют внимания шефа.")
        for warning in imported.warnings[:40]:
            print(f"  · {warning}")
        if len(imported.warnings) > 40:
            print(f"  … и ещё {len(imported.warnings) - 40}")
    print()
    print(f"Прогон записан: sync_runs.id = {imported.run_id}")


def main() -> int:
    settings = load_settings()
    cycle = SyncCycle(reader_from(settings), make_session_factory(settings.database_url))
    result = cycle.run(force=True)

    print(RULE)
    print("ИМПОРТ ЛИСТОВ В БАЗУ")
    print(RULE)
    for book in BOOKS:
        outcome = result.outcomes[book]
        reason = f" — {outcome.problem}" if outcome.problem else ""
        print(f"  {BOOK_TITLES[book]:24} {_ACTIONS[outcome.action]}{reason}")
        if outcome.details:
            # На сайт исходный текст не идёт, а ручной импорт запускают, чтобы
            # чинить: по переведённой причине закрытый доступ не отличить от
            # выключенного API.
            print(f"  {'':24} исходная ошибка: {outcome.details}")

    for book in BOOKS:
        imported = result.imported.get(book)
        if imported is not None:
            _show_import(BOOK_TITLES[book], imported)

    print()
    return 1 if any(o.action == "failed" for o in result.outcomes.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
