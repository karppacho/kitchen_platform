"""Отчёт о состоянии четырёх Google-таблиц.

Главный вопрос, на который отвечает фаза 0: **насколько расколот справочник
ингредиентов.** Карточки, которые повара заполняют через pizza_bot, лежат в
одной таблице, а лист ING, по которому считается себестоимость, — в
совершенно другой. Связи между ними нет никакой. Сколько карточек не имеет
пары в справочнике — это и есть размер работы фазы 1.

Скрипт только читает. Ни одной записи в листы он не делает и делать не
должен: пока живы Telegram-боты, они там единственные писатели.

Запуск::

    uv run python scripts/sheets_report.py
    uv run python scripts/sheets_report.py --json отчёт.json

Нужны заполненные SHEETS_ID_* и файл сервисного аккаунта — см. .env.example.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kitchen.config import load_settings
from kitchen.domain.matching import Entry, NameIndex, Summary, summarise
from kitchen.sync import specs
from kitchen.sync.client import GspreadClient
from kitchen.sync.reader import SheetsReader

if TYPE_CHECKING:
    from kitchen.sync.reader import SheetData

RULE = "─" * 78


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def read_everything(reader: SheetsReader) -> dict[str, SheetData | str]:
    """Прочитать все описанные листы одним пакетом на таблицу.

    Раньше здесь был цикл с отдельным чтением каждого листа, и два прогона
    подряд выбирали квоту Google (60 запросов в минуту на пользователя).
    Теперь на таблицу уходит два обращения независимо от числа листов.

    Отказ по одному листу по-прежнему не рушит остальные: вместо данных в
    словаре оказывается строка с объяснением.
    """
    return reader.read_many(specs.ALL_SPECS)


def report_sheets(sheets: dict[str, SheetData | str]) -> None:
    print()
    print("ЛИСТЫ")
    print(RULE)
    for label, data in sheets.items():
        if isinstance(data, str):
            print(f"  {label:42} {data}")
            continue
        note = f"{len(data):>5} строк"
        if data.title != data.spec.title:
            note += f"   (по прежнему имени «{data.title}»)"
        print(f"  {label:42} {note}")

    problems = [
        (label, data) for label, data in sheets.items() if not isinstance(data, str) and not data.ok
    ]
    if not problems:
        print()
        print("  Заголовки везде совпадают с описанием.")
        return

    print()
    print("  РАСХОЖДЕНИЯ ЗАГОЛОВКОВ")
    print("  Колонки могли переехать. Это опаснее, чем кажется: расчёт не")
    print("  упадёт, цена просто начнёт читаться из соседнего поля.")
    for label, data in problems:
        print()
        print(f"  {label}:")
        for issue in data.header_issues:
            print(f"    · {issue}")


def report_duplicates(index: NameIndex) -> None:
    duplicates = index.duplicates()
    print()
    print("АКТИВНЫЕ ТЁЗКИ В СПРАВОЧНИКЕ")
    print(RULE)
    if not duplicates:
        print("  Нет.")
        return
    print("  Поиск по имени для них неоднозначен — бот обязан переспрашивать.")
    for name, entries in sorted(duplicates.items()):
        keys = ", ".join(e.key for e in entries)
        print(f"  · «{name}» → id {keys}")


def report_matching(index: NameIndex, card_names: list[str]) -> Summary:
    """Сопоставить карточки со справочником и напечатать разбор."""
    summary = summarise(index.match(name) for name in card_names)

    print()
    print("СКЛЕЙКА СПРАВОЧНИКА")
    print(RULE)
    print(f"  Карточек всего:            {summary.total}")
    print(f"  Однозначно нашли пару:     {len(summary.resolved)}")
    print(f"  Есть кандидаты, нужен шеф: {len(summary.with_candidates)}")
    print(f"  Несколько тёзок:           {len(summary.ambiguous)}")
    print(f"  Пары в справочнике нет:    {len(summary.orphans)}")
    print()
    print(f"  Решений вручную:           {summary.needs_human}")

    if summary.ambiguous or summary.with_candidates:
        print()
        print("  ТРЕБУЮТ РЕШЕНИЯ ЧЕЛОВЕКА")
        print("  Автоматически это не решается. Подставить не тот ингредиент хуже,")
        print("  чем не подставить никакого: себестоимость посчитается по чужой цене")
        print("  и будет выглядеть правдоподобно.")
        for match in summary.ambiguous:
            keys = ", ".join(e.key for e in match.exact)
            print(f"    ⚠ «{match.query}» — несколько активных тёзок: id {keys}")
        for match in summary.with_candidates:
            best = ", ".join(
                f"«{c.entry.name}» (id {c.entry.key}, {c.score:.0%})" for c in match.similar
            )
            print(f"    ? «{match.query}» → {best}")

    if summary.orphans:
        print()
        print("  БЕЗ ПАРЫ В СПРАВОЧНИКЕ")
        print("  Эти ингредиенты оформлены поваром, но калькулятор их не видит.")
        for match in summary.orphans:
            print(f"    · {match.query}")

    return summary


def build_index(ing: SheetData) -> NameIndex:
    return NameIndex(
        Entry(key=_text(row["id"]), name=_text(row["name"]), status=_text(row["status"]))
        for row in ing.rows
        if _text(row["name"])
    )


def card_names(cards: SheetData) -> list[str]:
    return [_text(row["name"]) for row in cards.rows if _text(row["name"])]


def main() -> int:
    parser = argparse.ArgumentParser(description="Отчёт о состоянии Google-таблиц")
    parser.add_argument("--json", type=Path, help="сохранить сводку в файл")
    args = parser.parse_args()

    settings = load_settings()
    client = GspreadClient(
        settings.google_credentials_path,
        timeout=settings.google_timeout,
        refresh_timeout=settings.google_refresh_timeout,
    )
    reader = SheetsReader(
        client,
        {
            "kitchen": settings.sheets_id_kitchen,
            "competitors": settings.sheets_id_competitors,
            "ingredient_cards": settings.sheets_id_ingredient_cards,
            "tastings": settings.sheets_id_tastings,
        },
    )

    print(RULE)
    print("ОТЧЁТ О ТАБЛИЦАХ")
    print(RULE)

    sheets = read_everything(reader)
    report_sheets(sheets)

    ing = sheets.get("kitchen/ING")
    cards = sheets.get("ingredient_cards/Лист1")
    if isinstance(ing, str) or isinstance(cards, str) or ing is None or cards is None:
        print()
        print("Склейку не считаю: не прочитан справочник или карточки.")
        return 1

    index = build_index(ing)
    report_duplicates(index)
    summary = report_matching(index, card_names(cards))

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "sheets": {
                        label: (data if isinstance(data, str) else len(data))
                        for label, data in sheets.items()
                    },
                    "duplicates": {n: [e.key for e in es] for n, es in index.duplicates().items()},
                    "matching": {
                        "total": summary.total,
                        "resolved": len(summary.resolved),
                        "with_candidates": [m.query for m in summary.with_candidates],
                        "ambiguous": [m.query for m in summary.ambiguous],
                        "orphans": [m.query for m in summary.orphans],
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print()
        print(f"Сводка сохранена: {args.json}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
