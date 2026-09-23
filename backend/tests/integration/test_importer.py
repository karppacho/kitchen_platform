"""Импорт листов в базу — на настоящем Postgres, но без сети.

Настоящая база здесь принципиальна: половина смысла импорта в ограничениях,
а они живут в схеме. Строка ТТК со ссылкой сразу на ингредиент и упаковку
должна отвергаться базой, а не надеждой на аккуратность кода.

Читатель при этом фальшивый: ходить в Google ради проверки склейки незачем.
"""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from kitchen.db import models
from kitchen.db.recipes import load_recipes
from kitchen.domain.costs import calculate
from kitchen.sync import specs
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetsReader
from tests.fake_sheets import IDS, cards_sheet, header, kitchen_sheets, row, sheets_client

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


def test_row_removed_from_sheet_is_hidden_not_deleted(sessions) -> None:
    """Решение 23.09: удалённое скрываем, но помним — не стираем."""
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()
    ing = kitchen_sheets()["ING"]
    without_sugar = [ing[0], ing[1], ing[3]]  # нет id=2

    result = Importer(
        SheetsReader(sheets_client(kitchen={"ING": without_sugar}), IDS), sessions
    ).run()

    with sessions() as session:
        sugar = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "2"))
        tomato = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "1"))
    assert sugar is not None, "строка не стёрта"
    assert sugar.removed_at is not None, "а скрыта"
    assert tomato.removed_at is None
    assert result.counts["ингредиенты"] == 2, "считаем строки листа, а не базы"
    assert any(w.startswith("ING: скрыто") for w in result.presence)


def test_row_returned_to_sheet_is_restored(sessions) -> None:
    ing = kitchen_sheets()["ING"]
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()
    Importer(
        SheetsReader(sheets_client(kitchen={"ING": [ing[0], ing[1], ing[3]]}), IDS), sessions
    ).run()

    result = Importer(SheetsReader(sheets_client(), IDS), sessions).run()

    with sessions() as session:
        sugar = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "2"))
    assert sugar.removed_at is None
    assert any(w.startswith("ING: вернулись") for w in result.presence)


def test_removal_note_is_not_lost_among_other_warnings(sessions) -> None:
    """Замечание об удалении обязано попасть в sync_runs.note даже среди другого шума.

    В живом ING около 40 строк без id — заготовки, недописанные позиции.
    `_identified` пишет по замечанию на каждую такую строку; если «скрыто» лежит
    в том же списке, что и они, обрезка note до двадцати записей выбрасывает
    единственный след массового удаления из журнала.
    """
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()
    ing = kitchen_sheets()["ING"]
    blanks = [row(specs.INGREDIENTS, name="Заготовка") for _ in range(25)]
    without_sugar = [ing[0], *blanks, ing[1], ing[3]]  # шапка, шум, id=1, id=3 — нет id=2

    result = Importer(
        SheetsReader(sheets_client(kitchen={"ING": without_sugar}), IDS), sessions
    ).run()

    with sessions() as session:
        run = session.get(models.SyncRun, result.run_id)
    assert "ING: скрыто" in run.note


def test_removed_card_keeps_confirmed_link(sessions) -> None:
    """Связь, подтверждённую на сверке, удаление и возврат карточки не стирают."""
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
    without_sugar = [line for line in cards_sheet() if "Сахар" not in line]

    Importer(SheetsReader(sheets_client(cards=without_sugar), IDS), sessions).run()
    with sessions() as session:
        card = session.scalar(
            select(models.IngredientCard).where(models.IngredientCard.name == "Сахар")
        )
        assert card.removed_at is not None
        assert card.ingredient_id == sugar_id, "связь не тронута"

    importer.run()
    with sessions() as session:
        card = session.scalar(
            select(models.IngredientCard).where(models.IngredientCard.name == "Сахар")
        )
        assert card.removed_at is None
        assert (card.link_status, card.ingredient_id) == ("linked", sugar_id)


def test_unread_sheet_hides_nothing(sessions) -> None:
    """Лист не прочитался — это не «все строки удалены»."""
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()

    Importer(SheetsReader(sheets_client(missing=("ING",)), IDS), sessions).run()

    with sessions() as session:
        hidden = session.scalar(
            select(func.count())
            .select_from(models.Ingredient)
            .where(models.Ingredient.removed_at.is_not(None))
        )
    assert hidden == 0


def test_card_is_not_linked_to_removed_ingredient(sessions) -> None:
    """Удалённое из справочника не предлагаем в пару карточке."""
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()
    ing = kitchen_sheets()["ING"]

    result = Importer(
        SheetsReader(sheets_client(kitchen={"ING": [ing[0], ing[2], ing[3]]}), IDS), sessions
    ).run()

    assert result.counts["строки ТТК"] == 2, (
        "строка ТТК на удалённый ингредиент переносится, иначе себестоимость тихо занизится"
    )
    with sessions() as session:
        card = session.scalar(
            select(models.IngredientCard).where(models.IngredientCard.name == "Томаты")
        )
    assert card.link_status != "linked"
    assert card.ingredient_id is None


def test_removed_ingredient_still_counted_in_dish(sessions) -> None:
    """Сквозь базу: ТТК ссылается на удалённый ингредиент — строка состава на
    месте, счёт по последним данным, замечание есть."""
    ing = [
        header(specs.INGREDIENTS),
        row(
            specs.INGREDIENTS,
            id="1",
            name="Томаты",
            unit="кг",
            price_per_kg="177",
            status="активное",
        ),
        row(
            specs.INGREDIENTS,
            id="2",
            name="Сахар",
            unit="кг",
            price_per_kg="100",
            status="активное",
        ),
    ]
    Importer(SheetsReader(sheets_client(kitchen={"ING": ing}), IDS), sessions).run()
    Importer(SheetsReader(sheets_client(kitchen={"ING": [ing[0], ing[2]]}), IDS), sessions).run()

    with sessions() as session:
        [recipe] = load_recipes(session)
    cost = calculate(recipe)

    assert cost.uc_rub == Decimal("17.70")
    assert any("«Томаты» удалён из справочника" in w for w in cost.warnings)


def test_parallel_imports_do_not_duplicate_ttk(sessions) -> None:
    """Два импорта разом задвоили бы состав: оба стирают строки ТТК и вставляют свои.

    Первый держит транзакцию открытой; второй обязан дождаться его и увидеть
    уже новые строки — иначе в базе окажутся обе пачки.
    """
    importer = Importer(SheetsReader(sheets_client(), IDS), sessions)
    importer.run()
    sheets = SheetsReader(sheets_client(), IDS).read_many(Importer.SPECS)
    errors: list[Exception] = []

    def second() -> None:
        try:
            with sessions() as session, session.begin():
                importer.apply(session, sheets)
        except Exception as error:  # ошибку потока показываем в утверждении
            errors.append(error)

    first = sessions()
    thread = threading.Thread(target=second, daemon=True)
    try:
        first.begin()
        importer.apply(first, sheets)
        thread.start()
        thread.join(timeout=1.0)  # второй успевает дойти до места, где ждёт
        first.commit()
    finally:
        # Даже если apply(first, ...) упадёт, транзакция обязана закрыться —
        # иначе она держит блокировки, и downgrade в фикстуре повиснет.
        first.close()
    thread.join(timeout=30)
    assert not thread.is_alive(), "второй импорт не дождался блокировки за 30 с"

    assert not errors, errors
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(models.DishComponent)) == 2
