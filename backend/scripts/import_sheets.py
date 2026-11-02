"""Перенести Google-таблицы в базу — один цикл синхронизации вручную.

То же, что воркер делает раз в пять минут (kitchen/sync/cycle.py), но с
принудительным переносом: отпечаток не сравнивается. Правила те же: книга, у
которой лист не прочитан или сдвинулись колонки, НЕ переносится. Раньше
ручной импорт переносил и такую, только написав замечание, — при сдвиге
колонок это значило цену из колонки веса. Так же не переносится кухня, у
которой в листе ING строки съехали относительно id или пропал вывод формулы
QUERY.

Запуск::

    docker compose -f infra/docker-compose.yml exec api python scripts/import_sheets.py

Код выхода 1 — хотя бы одна книга не перенесена из-за сбоя.

Ручной обход сдвига строк ING — ``--accept-shift N``. Признак сдвига
сравнивает названия листа с базой и не знает, кто из них прав. Если в базе
уже лежат сдвинутые названия — восстановление из ночного дампа 04–11.10,
круговая перестановка названий, намеренный обмен названиями двух позиций, —
признак держит кухню и при верном листе. Порядок:

1. Запустить без флага. У кухни будет «НЕ перенесена», список
   «подробности:» (строка листа, id, «было» — это база, «стало» — лист) и
   строка «для обхода …: --accept-shift N».
2. Сверить список с листом ING и убедиться, что верен лист, а не база. Если
   неверен лист — выровнять его, флаг не нужен.
3. Запустить ту же команду с ``--accept-shift N`` в конце — с тем числом,
   что напечатано.

Почему число точное: лист могли изменить, пока его сверяли, — тогда
признак задевает уже другие id, и принять их значило бы принять
непроверенное. Число не совпало — отказ, как без флага, и в подробностях
сказано, сколько найдено сейчас. Принятый сдвиг — отдельная запись журнала
«сдвиг строк ING принят вручную: N id — …» и полный список на экране.

Флаг не обходит ни сдвиг колонок, ни непрочитанный лист, ни «пропал вывод
формулы QUERY»: принять пустые названия значило бы стереть их в базе.
Воркер и «Сверка» флага не получают никогда.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kitchen.config import load_settings
from kitchen.db.session import make_session_factory
from kitchen.sync.cycle import BOOK_TITLES, BOOKS, SyncCycle, reader_from
from kitchen.sync.importer import ImportResult

if TYPE_CHECKING:
    from collections.abc import Sequence

RULE = "─" * 78

_ACTIONS = {
    "imported": "перенесена",
    "unchanged": "без изменений",
    "failed": "НЕ перенесена",
    "stale": "пропущена: другой импорт уже перенёс чтение новее",
}


def _shift_count(text: str) -> int:
    """Число для ``--accept-shift``: целое от единицы — сдвига без id не бывает."""
    try:
        number = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"нужно целое число, а не «{text}»") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"нужно целое число от 1, а не {number}")
    return number


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Перенести Google-таблицы в базу — один цикл синхронизации вручную."
    )
    parser.add_argument(
        "--accept-shift",
        type=_shift_count,
        metavar="N",
        help=(
            "перенести кухню, несмотря на сдвиг строк ING, если он задел ровно N id; "
            "только когда лист ING верен, а база старше (N — из запуска без флага)"
        ),
    )
    return parser.parse_args(argv)


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


def main(argv: Sequence[str] | None = None) -> int:
    # Разбор — до настроек и базы: неверный флаг не запускает ничего.
    accept_shift: int | None = _arguments(argv).accept_shift
    settings = load_settings()
    cycle = SyncCycle(reader_from(settings), make_session_factory(settings.database_url))
    result = cycle.run(force=True, accept_shift=accept_shift)

    print(RULE)
    print("ИМПОРТ ЛИСТОВ В БАЗУ")
    print(RULE)
    for book in BOOKS:
        outcome = result.outcomes[book]
        reason = f" — {outcome.problem}" if outcome.problem else ""
        print(f"  {BOOK_TITLES[book]:24} {_ACTIONS[outcome.action]}{reason}")
        if outcome.shift_accepted:
            print(f"  {'':24} сдвиг принят вручную: {outcome.shift_ids} id")
        if outcome.details:
            # На сайт исходный текст не идёт, а ручной импорт запускают, чтобы
            # чинить: по переведённой причине закрытый доступ не отличить от
            # выключенного API. У сдвига строк ING здесь полный список
            # съехавших строк — по нему выравнивают лист или сверяют его перед
            # обходом.
            print(f"  {'':24} подробности: {outcome.details}")
        if outcome.action == "failed" and outcome.shift_ids is not None:
            # Число печатается всегда, когда сдвиг держит кухню: в тексте
            # причины чисел два, и id в обоих видах посчитан там дважды.
            print(
                f"  {'':24} для обхода (только если лист ING верен, а база старше): "
                f"--accept-shift {outcome.shift_ids}"
            )

    for book in BOOKS:
        imported = result.imported.get(book)
        if imported is not None:
            _show_import(BOOK_TITLES[book], imported)

    print()
    return 1 if any(o.action == "failed" for o in result.outcomes.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
