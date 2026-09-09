"""Импорт листов в базу — на настоящем Postgres, но без сети.

Настоящая база здесь принципиальна: половина смысла импорта в ограничениях,
а они живут в схеме. Строка ТТК со ссылкой сразу на ингредиент и упаковку
должна отвергаться базой, а не надеждой на аккуратность кода.

Читатель при этом фальшивый: ходить в Google ради проверки склейки незачем.
"""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from kitchen.db import models
from kitchen.db.session import make_session_factory
from kitchen.sync import specs
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetsReader
from tests.conftest import FakeSheetsClient, FakeSpreadsheet, FakeWorksheet
from tests.integration.test_database import BACKEND, _url

pytestmark = pytest.mark.integration

IDS = {"kitchen": "kitchen-id", "ingredient_cards": "cards-id"}


def _header(spec) -> list[str]:
    return [c.expected_header for c in spec.columns]


def _row(spec, **values: str) -> list[str]:
    row = dict.fromkeys((c.field for c in spec.columns), "")
    row.update(values)
    return [row[c.field] for c in spec.columns]


def _client(**overrides: list[list[str]]) -> FakeSheetsClient:
    """Фальшивые таблицы с разумным содержимым по умолчанию."""
    kitchen = {
        "ING": [
            _header(specs.INGREDIENTS),
            _row(specs.INGREDIENTS, id="1", name="Томаты", price_per_kg="177", status="активное"),
            _row(specs.INGREDIENTS, id="2", name="Сахар", price_per_kg="100", status="активное"),
            _row(specs.INGREDIENTS, id="3", name="Сахар", price_per_kg="0", status="активное"),
        ],
        "Упаковка": [
            _header(specs.PACKAGING),
            _row(specs.PACKAGING, id="u1", name="Коробка", price_per_piece="12"),
        ],
        "Способы приготовления": [
            _header(specs.COOKING_METHODS),
            _row(specs.COOKING_METHODS, id="m1", method="Фритюр"),
        ],
        "Блюда": [
            _header(specs.DISHES),
            _row(specs.DISHES, id="B001", name="Ролл", price_menu="280", status="активное"),
        ],
        "ТТК": [
            _header(specs.TTK),
            _row(specs.TTK, dish_id="B001", ingredient_id="1", net_weight_g="100"),
            _row(specs.TTK, dish_id="B001", packaging_id="u1", net_weight_g="0"),
        ],
    }
    cards = {
        "Лист1": [
            _header(specs.INGREDIENT_CARDS),
            [""] * len(specs.INGREDIENT_CARDS.columns),
            _row(specs.INGREDIENT_CARDS, name="Томаты", supplier="Поставщик"),
            _row(specs.INGREDIENT_CARDS, name="Сахар"),
            _row(specs.INGREDIENT_CARDS, name="Пастрами из индейки"),
        ]
    }
    kitchen.update({k: v for k, v in overrides.items() if k in kitchen or k == "ТТК"})
    return FakeSheetsClient(
        {
            "kitchen-id": FakeSpreadsheet({t: FakeWorksheet(v, t) for t, v in kitchen.items()}),
            "cards-id": FakeSpreadsheet({t: FakeWorksheet(v, t) for t, v in cards.items()}),
        }
    )


@pytest.fixture
def sessions():
    url = _url()
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    # Чистая база на каждый тест: импорт идёт одной транзакцией, и остатки
    # прошлого прогона сделали бы результат зависящим от порядка тестов.
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    factory = make_session_factory(url)
    yield factory

    command.downgrade(config, "base")


def test_import_fills_reference_tables(sessions) -> None:
    result = Importer(SheetsReader(_client(), IDS), sessions).run()

    assert result.ok, result.unreadable
    assert result.run_id is not None
    assert result.counts["ингредиенты"] == 3
    assert result.counts["блюда"] == 1
    assert result.counts["строки ТТК"] == 2

    with sessions() as session:
        tomato = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "1"))
        assert tomato is not None
        assert tomato.name == "Томаты"
        # Деньги обязаны доехать как Decimal, а не как строка или float.
        assert str(tomato.price_per_kg) == "177.00"
        assert tomato.source_row == 2, "номер строки листа сохранён для диагностики"


def test_ttk_row_needs_exactly_one_reference(sessions) -> None:
    """Ссылка сразу на ингредиент и упаковку — двойной счёт в себестоимости.

    Импорт обязан такую строку отвергнуть и сказать об этом, а не переложить
    решение на базу в виде падения посреди транзакции.
    """
    broken = [
        _header(specs.TTK),
        _row(specs.TTK, dish_id="B001", ingredient_id="1", packaging_id="u1", net_weight_g="50"),
        _row(specs.TTK, dish_id="B001", net_weight_g="50"),
        _row(specs.TTK, dish_id="B001", ingredient_id="1", net_weight_g="100"),
    ]
    result = Importer(SheetsReader(_client(**{"ТТК": broken}), IDS), sessions).run()

    assert result.counts["строки ТТК"] == 1, "приняться должна только корректная строка"
    assert sum("ровно одна ссылка" in w for w in result.warnings) == 2


def test_unknown_dish_in_ttk_is_reported(sessions) -> None:
    rows = [
        _header(specs.TTK),
        _row(specs.TTK, dish_id="B999", ingredient_id="1", net_weight_g="100"),
    ]
    result = Importer(SheetsReader(_client(**{"ТТК": rows}), IDS), sessions).run()

    assert result.counts["строки ТТК"] == 0
    assert any("B999" in w for w in result.warnings)


def test_card_linking_follows_domain_rules(sessions) -> None:
    """Однозначное склеивается, спорное — нет.

    «Томаты» находят пару. «Сахар» — два активных тёзки, и подставлять
    первого попавшегося нельзя: цены различаются в сто раз. «Пастрами»
    пары не имеет вовсе.
    """
    Importer(SheetsReader(_client(), IDS), sessions).run()

    with sessions() as session:
        cards = {c.name: c for c in session.scalars(select(models.IngredientCard)).all()}

    assert cards["Томаты"].link_status == "linked"
    assert cards["Томаты"].ingredient_id is not None

    assert cards["Сахар"].link_status == "ambiguous"
    assert cards["Сахар"].ingredient_id is None, "код обязан отказаться выбирать"

    assert cards["Пастрами из индейки"].link_status == "orphan"


def test_reimport_preserves_confirmed_link(sessions) -> None:
    """Переимпорт не должен обнулять работу человека.

    Шеф разрешил спорный случай руками — повторное чтение листа обязано
    это решение сохранить, иначе сверку придётся проходить каждый раз
    заново.
    """
    importer = Importer(SheetsReader(_client(), IDS), sessions)
    importer.run()

    with sessions() as session, session.begin():
        sugar_id = session.scalar(
            select(models.Ingredient.id).where(models.Ingredient.legacy_id == "2")
        )
        card = session.scalar(
            select(models.IngredientCard).where(models.IngredientCard.name == "Сахар")
        )
        card.ingredient_id = sugar_id
        card.link_status = "linked"
        card.link_confirmed_at = text("now()")

    importer.run()

    with sessions() as session:
        card = session.scalar(
            select(models.IngredientCard).where(models.IngredientCard.name == "Сахар")
        )
        assert card.link_status == "linked", "подтверждённая связь пережила переимпорт"
        assert card.ingredient_id == sugar_id


def test_run_is_recorded(sessions) -> None:
    """Прогон записывается: «в понедельник было 130 блюд, во вторник 128»
    должно быть вопросом к данным, а не к памяти."""
    Importer(SheetsReader(_client(), IDS), sessions).run()

    with sessions() as session:
        run = session.scalars(select(models.SyncRun)).one()
        assert run.finished_at is not None
        assert run.ok is True

        sheets = {s.title: s for s in session.scalars(select(models.SyncSheet)).all()}
        assert sheets["ING"].rows == 3
        assert sheets["ING"].header_issues == ""
