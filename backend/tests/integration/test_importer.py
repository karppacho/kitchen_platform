"""Импорт листов в базу — на настоящем Postgres, но без сети.

Настоящая база здесь принципиальна: половина смысла импорта в ограничениях,
а они живут в схеме. Строка ТТК со ссылкой сразу на ингредиент и упаковку
должна отвергаться базой, а не надеждой на аккуратность кода.

Читатель при этом фальшивый: ходить в Google ради проверки склейки незачем.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from kitchen.db import models
from kitchen.sync import specs
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetsReader
from tests.fake_sheets import IDS, header, row, sheets_client

pytestmark = pytest.mark.integration


def test_import_fills_reference_tables(sessions) -> None:
    result = Importer(SheetsReader(sheets_client(), IDS), sessions).run()

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
        header(specs.TTK),
        row(specs.TTK, dish_id="B001", ingredient_id="1", packaging_id="u1", net_weight_g="50"),
        row(specs.TTK, dish_id="B001", net_weight_g="50"),
        row(specs.TTK, dish_id="B001", ingredient_id="1", net_weight_g="100"),
    ]
    result = Importer(SheetsReader(sheets_client(kitchen={"ТТК": broken}), IDS), sessions).run()

    assert result.counts["строки ТТК"] == 1, "приняться должна только корректная строка"
    assert sum("ровно одна ссылка" in w for w in result.warnings) == 2


def test_unknown_dish_in_ttk_is_reported(sessions) -> None:
    rows = [
        header(specs.TTK),
        row(specs.TTK, dish_id="B999", ingredient_id="1", net_weight_g="100"),
    ]
    result = Importer(SheetsReader(sheets_client(kitchen={"ТТК": rows}), IDS), sessions).run()

    assert result.counts["строки ТТК"] == 0
    assert any("B999" in w for w in result.warnings)


def test_card_linking_follows_domain_rules(sessions) -> None:
    """Однозначное склеивается, спорное — нет.

    «Томаты» находят пару. «Сахар» — два активных тёзки, и подставлять
    первого попавшегося нельзя: цены различаются в сто раз. «Пастрами»
    пары не имеет вовсе.
    """
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()

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
    importer = Importer(SheetsReader(sheets_client(), IDS), sessions)
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
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()

    with sessions() as session:
        run = session.scalars(select(models.SyncRun)).one()
        assert run.finished_at is not None
        assert run.ok is True

        sheets = {s.title: s for s in session.scalars(select(models.SyncSheet)).all()}
        assert sheets["ING"].rows == 3
        assert sheets["ING"].header_issues == ""


def test_duplicate_card_names_are_reported(sessions) -> None:
    """Две карточки с одним именем — дефект данных, а не мелочь.

    Ключа кроме имени у карточек нет, поэтому вторая затирает первую.
    В живой таблице таких пар шесть, и повар заполнял обе. Молчать об
    этом значит потерять чужую работу без следа.
    """
    cards = [
        header(specs.INGREDIENT_CARDS),
        [""] * len(specs.INGREDIENT_CARDS.columns),
        row(specs.INGREDIENT_CARDS, name="Томаты", supplier="Первый"),
        row(specs.INGREDIENT_CARDS, name="томаты ", supplier="Второй"),
    ]
    result = Importer(SheetsReader(sheets_client(cards=cards), IDS), sessions).run()

    assert result.counts["карточки"] == 1, "по имени они одно и то же"
    assert any("уже была выше" in w for w in result.warnings)
