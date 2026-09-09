"""Сквозная проверка отчёта фазы 0 — без сети и без кредов.

Отчёт отвечает на главный вопрос фазы: насколько расколот справочник
ингредиентов. Проверяется вся цепочка целиком — лист → читатель → индекс →
разбор по исходам, — потому что ошибка на любом стыке даёт правдоподобное
и неверное число, а сверять его будет не с чем.
"""

from __future__ import annotations

import sheets_report

from kitchen.sync import specs
from kitchen.sync.reader import SheetsReader

IDS = {"kitchen": "kitchen-id", "ingredient_cards": "cards-id"}

ING_HEADER = [c.expected_header for c in specs.INGREDIENTS.columns]
CARDS_HEADER = [c.expected_header for c in specs.INGREDIENT_CARDS.columns]


def _ing(id_: str, name: str, status: str = "активное") -> list[str]:
    row = dict.fromkeys((c.field for c in specs.INGREDIENTS.columns), "")
    row.update(id=id_, name=name, status=status)
    return [row[c.field] for c in specs.INGREDIENTS.columns]


def _card(name: str) -> list[str]:
    row = dict.fromkeys((c.field for c in specs.INGREDIENT_CARDS.columns), "")
    row["name"] = name
    return [row[c.field] for c in specs.INGREDIENT_CARDS.columns]


def test_full_report_pipeline(make_client) -> None:
    client = make_client(
        {
            "kitchen-id": {
                "ING": [
                    ING_HEADER,
                    _ing("1", "Томаты"),
                    _ing("12", "Сахар"),
                    _ing("123", "Сахар"),  # активный тёзка — неоднозначность
                    _ing("40", "Огурцы маринованные, не резанные", status="архив"),
                    _ing("41", "Огурцы маринованные, не резанные"),
                    _ing("50", "Сыр моцарелла"),
                ],
            },
            "cards-id": {
                "Лист1": [
                    ["", "", ""],  # строка подсказок
                    CARDS_HEADER,
                    _card("Томаты"),  # точное совпадение
                    _card("Сахар"),  # два активных тёзки
                    _card("Огурцы маринованные не резаные"),  # похожее
                    _card("Пастрами из индейки"),  # пары нет
                ]
            },
        }
    )
    reader = SheetsReader(client, IDS)

    ing = reader.read(specs.INGREDIENTS)
    cards = reader.read(specs.INGREDIENT_CARDS)
    assert ing.ok and cards.ok, "заголовки собраны из описания, расхождений быть не может"

    index = sheets_report.build_index(ing)
    summary = sheets_report.report_matching(index, sheets_report.card_names(cards))

    assert summary.total == 4
    assert [m.query for m in summary.resolved] == ["томаты"]
    assert [m.query for m in summary.ambiguous] == ["сахар"]
    assert [m.query for m in summary.with_candidates] == ["огурцы маринованные не резаные"]
    assert [m.query for m in summary.orphans] == ["пастрами из индейки"]

    assert summary.needs_human == 3, "три решения из четырёх придётся принять шефу"


def test_archived_namesake_did_not_create_false_ambiguity(make_client) -> None:
    """Архивная копия не должна превращать однозначный случай в спорный."""
    client = make_client(
        {
            "kitchen-id": {
                "ING": [ING_HEADER, _ing("40", "Огурцы", status="архив"), _ing("41", "Огурцы")]
            }
        }
    )
    index = sheets_report.build_index(SheetsReader(client, IDS).read(specs.INGREDIENTS))
    match = index.match("Огурцы")

    assert match.resolved is not None
    assert match.resolved.key == "41"


def test_report_survives_unreadable_sheet(make_client) -> None:
    """Отказ по одному листу не должен рушить весь отчёт.

    Иначе одна переименованная вкладка лишает нас картины целиком — а
    именно за картиной мы и пришли.
    """
    client = make_client({"kitchen-id": {"ING": [ING_HEADER, _ing("1", "Томаты")]}})
    reader = SheetsReader(client, {"kitchen": "kitchen-id"})

    sheets = sheets_report.read_everything(reader)

    assert not isinstance(sheets["kitchen/ING"], str), "этот лист прочитан"
    unreadable = [label for label, data in sheets.items() if isinstance(data, str)]
    assert unreadable, "остальные не прочитаны, и это записано, а не проглочено"
    assert all("не прочитан" in str(sheets[label]) for label in unreadable)
