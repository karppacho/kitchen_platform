"""Сверить новый расчёт себестоимости с эталоном старого бота.

Приёмка переноса калькулятора: 130 блюд обязаны совпасть **до копейки**.
Не «примерно», не «в пределах округления» — точное равенство.

Сравниваются и предупреждения: шеф читает именно их. Расхождение в тексте
означает, что он получит другое объяснение тех же цифр.

Запуск (нужна база с импортированными данными)::

    docker compose -f infra/docker-compose.yml exec api python scripts/verify_golden.py

Расхождения печатаются с указанием блюда, поля и обеих величин, а при
разнице в себестоимости — с построчным разбором состава: без него понятно
только «не сошлось», но не «где».
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kitchen.config import load_settings
from kitchen.db.recipes import load_recipes
from kitchen.db.session import make_session_factory
from kitchen.domain.costs import calculate

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "golden" / "dishes_uc.json"
RULE = "─" * 78

# Поля результата. Сравниваются как Decimal — по ВЕЛИЧИНЕ, а не по записи.
#
# Первая версия сверяла строки, и все 130 блюд «разошлись» на записи вида
# «369.00 ≠ 369»: Postgres возвращает Numeric(12,2) с двумя знаками, а бот
# брал число таким, как разобрал из листа. Величина при этом одна и та же.
#
# Это НЕ послабление. Decimal сравнивается точно, без допуска:
# Decimal("84.66") != Decimal("84.67"). Убрана сверка начертания, а не
# строгость до копейки.
FIELDS = (
    "price_menu",
    "uc_rub",
    "uc_percent",
    "margin_rub",
    "margin_percent",
    "output_grams",
    "proteins_g",
    "fats_g",
    "carbs_g",
    "kcal",
)

# Как поле называется у нас и как оно называлось у бота.
OURS = {
    "price_menu": "price_menu",
    "uc_rub": "uc_rub",
    "uc_percent": "uc_percent",
    "margin_rub": "margin_rub",
    "margin_percent": "margin_percent",
    "output_grams": "output_grams",
    "proteins_g": "protein_g",
    "fats_g": "fat_g",
    "carbs_g": "carbs_g",
    "kcal": "kcal",
}


# Расхождения, признанные и объяснённые. Ключ — блюдо, значение — почему.
#
# Это храповик, а не список исключений «чтобы прошло»: он не должен расти.
# Появилось новое расхождение — разбираемся, а не дописываем сюда строку.
KNOWN_DIVERGENCES = {
    "B129": (
        "строка ТТК 697 не ссылается ни на ингредиент, ни на упаковку. "
        "Бот держал такую строку и предупреждал при каждом расчёте; наш "
        "импорт её отвергает — ограничение базы требует ровно одну ссылку. "
        "Сообщение не потерялось, а переехало в отчёт импорта, где оно "
        "точнее: там назван номер строки в листе. Чинится правкой листа."
    ),
}


def text(value: object) -> str | None:
    return None if value is None else str(value)


def same_number(ours: object, theirs: object) -> bool:
    """Равны ли величины. Пусто равно пусто, число равно числу."""
    if ours is None or theirs is None:
        return ours is None and theirs is None
    try:
        return Decimal(str(ours)) == Decimal(str(theirs))
    except (InvalidOperation, ValueError):
        return str(ours) == str(theirs)


def compare_components(ours: object, theirs: list[dict[str, object]]) -> list[str]:
    """Построчный разбор состава — чтобы расхождение было локализовано."""
    lines: list[str] = []
    our_items = list(ours)  # type: ignore[call-overload]
    if len(our_items) != len(theirs):
        lines.append(f"      строк состава: у нас {len(our_items)}, в эталоне {len(theirs)}")
    for index in range(max(len(our_items), len(theirs))):
        mine = our_items[index] if index < len(our_items) else None
        gold = theirs[index] if index < len(theirs) else None
        if mine is None:
            lines.append(f"      [{index}] лишняя в эталоне: {gold.get('name') if gold else ''}")
            continue
        if gold is None:
            lines.append(f"      [{index}] лишняя у нас: {mine.name}")
            continue
        if mine.name != gold.get("name"):
            lines.append(f"      [{index}] имя: «{mine.name}» ≠ «{gold.get('name')}»")
        if not same_number(mine.cost_rub, gold.get("cost_rub")):
            lines.append(
                f"      [{index}] «{mine.name}»: стоимость {mine.cost_rub} ≠ {gold.get('cost_rub')}"
            )
        if not same_number(mine.gross_weight_g, gold.get("weight_brutto_g")):
            lines.append(
                f"      [{index}] «{mine.name}»: брутто {mine.gross_weight_g} "
                f"≠ {gold.get('weight_brutto_g')}"
            )
    return lines


def main() -> int:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    expected = golden["dishes"]

    settings = load_settings()
    sessions = make_session_factory(settings.database_url)
    with sessions() as session:
        recipes = load_recipes(session)

    print(RULE)
    print("СВЕРКА С ЭТАЛОНОМ СТАРОГО БОТА")
    print(RULE)
    print(f"  эталон: {len(expected)} блюд")
    print(f"  у нас:  {len(recipes)} блюд")

    ours = {recipe.key: calculate(recipe) for recipe in recipes}

    missing = sorted(set(expected) - set(ours))
    extra = sorted(set(ours) - set(expected))
    problems: list[str] = []

    for key in missing:
        problems.append(f"  ✗ {key} «{expected[key]['name']}»: есть в эталоне, нет у нас")
    for key in extra:
        problems.append(f"  ✗ {key} «{ours[key].name}»: есть у нас, нет в эталоне")

    identical = 0
    known: list[str] = []
    for key in sorted(set(expected) & set(ours)):
        gold = expected[key]
        mine = ours[key]
        diffs: list[str] = []

        for field in FIELDS:
            if not same_number(getattr(mine, OURS[field]), gold[field]):
                diffs.append(f"      {field}: {getattr(mine, OURS[field])} ≠ {gold[field]}")

        if list(mine.warnings) != list(gold["warnings"]):
            diffs.append(f"      предупреждений: {len(mine.warnings)} ≠ {len(gold['warnings'])}")
            for line in set(mine.warnings) ^ set(gold["warnings"]):
                diffs.append(f"        · {line[:100]}")

        if not diffs:
            identical += 1
        elif key in KNOWN_DIVERGENCES:
            known.append(f"  ! {key} «{mine.name}»: {KNOWN_DIVERGENCES[key]}")
        else:
            problems.append(f"  ✗ {key} «{mine.name}»")
            problems.extend(diffs)
            problems.extend(compare_components(mine.components, gold["ingredients"]))

    print()
    print(f"  СОВПАЛО ПОЛНОСТЬЮ: {identical} из {len(expected)}")

    if known:
        print()
        print("ПРИЗНАННЫЕ РАСХОЖДЕНИЯ")
        print(RULE)
        for line in known:
            print(line)

    unexpected_known = sorted(set(KNOWN_DIVERGENCES) - {line.split()[1] for line in known})
    if unexpected_known:
        print()
        print("  Эти блюда числятся в признанных расхождениях, но сошлись:")
        for key in unexpected_known:
            print(f"    · {key} — строку из KNOWN_DIVERGENCES пора убрать")

    if problems:
        print()
        print(f"РАСХОЖДЕНИЯ ({len([p for p in problems if p.startswith('  ✗')])} блюд)")
        print(RULE)
        for line in problems[:200]:
            print(line)
        if len(problems) > 200:
            print(f"  … и ещё {len(problems) - 200} строк")
        print()
        return 1

    print()
    if known:
        print(f"  Необъяснённых расхождений нет ({len(known)} признанных).")
    else:
        print("  Расхождений нет.")
    print("  Перенос калькулятора принят.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
