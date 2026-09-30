"""Правила владения колонками.

Самый дорогой сценарий в проекте — молчаливая перезапись чужой работы:
цен, которые коммерческий отдел вписывал руками, или правок шефа в
справочнике. Эти данные существуют в единственном экземпляре. Поэтому
правило проверяется тестами, а не доверием к внимательности.
"""

from __future__ import annotations

import re

import pytest

from kitchen.domain.cards import APPROVED, CardDraftData, card_row
from kitchen.sync import ownership, specs
from kitchen.sync.ownership import Column, ForbiddenWriteError, Kind, Owner

PEOPLE = "принадлежит людям"
BOOK_CLOSED = re.escape("путь записи книги не открыт (ADR-0003)")
BOTS = "Telegram-бот"
KITCHEN_OPEN = frozenset({"ingredient_cards", "kitchen"})
"""Книга кухни с открытым путём записи — такого пока нет, это проверка на будущее."""


# ---------------------------------------------------------------------------
# Адресация колонок
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("letter", "index"),
    [("A", 0), ("B", 1), ("T", 19), ("V", 21), ("Z", 25), ("AA", 26), ("AB", 27)],
)
def test_column_index(letter: str, index: int) -> None:
    column = Column(letter, "заголовок", "field", Kind.TEXT, Owner.APP)
    assert column.index == index


def test_specs_letters_match_positions() -> None:
    """Буква колонки обязана соответствовать её месту в описании.

    Храповик против опечатки: пропущенная буква сдвинула бы чтение, и цена
    поехала бы в поле веса — молча и с правдоподобным результатом.
    """
    for spec in specs.ALL_SPECS:
        for position, column in enumerate(spec.columns):
            assert column.index == position, (
                f"«{spec.title}»: колонка {column.letter} ({column.title}) "
                f"стоит на месте {position}, а её буква указывает на {column.index}"
            )


def test_specs_have_no_duplicate_fields() -> None:
    for spec in specs.ALL_SPECS:
        fields = [c.field for c in spec.columns]
        assert len(fields) == len(set(fields)), f"«{spec.title}»: повтор имени поля"


# ---------------------------------------------------------------------------
# Кто что может писать
# ---------------------------------------------------------------------------
def test_cards_writable_columns_are_a_to_p_and_s_to_v() -> None:
    """Книга карточек открыта: боты сняты, и у книги есть свой писатель.

    Открыто ровно то, что заполнял бот, — A–P и S–V.
    """
    letters = [c.letter for c in specs.INGREDIENT_CARDS.writable()]
    assert letters == [*"ABCDEFGHIJKLMNOP", *"STUV"]
    specs.INGREDIENT_CARDS.check_writable("category", "name", "label_url", "approval_status")


@pytest.mark.parametrize("field", ["declaration", "halal_certificate"])
def test_cards_q_and_r_belong_to_people(field: str) -> None:
    """Декларацию (Q) и халяль-сертификат (R) вписывают люди. Бот их не
    трогал, и открытая книга их не открывает."""
    with pytest.raises(ForbiddenWriteError, match=PEOPLE):
        specs.INGREDIENT_CARDS.check_writable("name", field)


def test_card_row_matches_writable_columns_exactly() -> None:
    """Стык домена и правила владения.

    Писатель строки проверяет право записи ключами самой строки:
    ``check_writable(*card_row(...))``. Ключи обязаны совпасть с открытыми
    колонками один в один: лишний — отказ записи, недостающий — дыра в
    строке, которую никто не заметит.
    """
    row = card_row(CardDraftData(name="Соус Барбекю", approval=APPROVED))

    assert set(row) == {c.field for c in specs.INGREDIENT_CARDS.writable()}
    specs.INGREDIENT_CARDS.check_writable(*row)


def test_kitchen_book_is_closed_even_without_bots() -> None:
    """ING — общий справочник, и боты сняты, но у книги кухни нет своего пути
    записи: ни сверки ячейки перед записью, ни журнала правок. Её держит
    второй замок, и отказ называет именно его, а не ботов."""
    assert specs.INGREDIENTS.writable() == ()
    with pytest.raises(ForbiddenWriteError, match=BOOK_CLOSED):
        specs.INGREDIENTS.check_writable("price_per_kg")


def test_only_the_cards_book_is_open() -> None:
    """Храповик: запись открыта одной книге.

    Открыть ещё одну — осознанная правка вместе с её писателем, а не
    побочный эффект: у кухни, конкурентов и дегустаций не открыто ничего.
    """
    for spec in (*specs.ALL_SPECS, specs.TASTING_RATINGS):
        if spec.spreadsheet == "ingredient_cards":
            continue
        assert spec.writable() == (), f"{spec.spreadsheet}/{spec.title}: открыта запись"


def test_human_column_is_never_writable() -> None:
    """Цена продажная в расчётке — работа коммерческого отдела."""
    with pytest.raises(ForbiddenWriteError, match=PEOPLE):
        specs.PRICING_NEW.check_writable("price_sale")


def test_app_columns_wait_for_their_book(monkeypatch: pytest.MonkeyPatch) -> None:
    """Вычисляемые колонки расчётки — наши, но лежат в книге кухни, а она
    закрыта. Откроют книгу — откроются и они, а цена коммерсов — нет."""
    with pytest.raises(ForbiddenWriteError, match=BOOK_CLOSED):
        specs.PRICING_NEW.check_writable("uc_rub")

    monkeypatch.setattr(ownership, "WRITE_OPEN", KITCHEN_OPEN)

    specs.PRICING_NEW.check_writable("uc_rub", "margin_percent", "kcal")
    assert "price_sale" not in {c.field for c in specs.PRICING_NEW.writable()}


def test_shared_columns_blocked_while_bots_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Справочник закрыт, пока в него пишут боты, — даже в открытой книге."""
    monkeypatch.setattr(ownership, "WRITE_OPEN", KITCHEN_OPEN)
    monkeypatch.setattr(ownership, "BOTS_ALIVE", True)

    with pytest.raises(ForbiddenWriteError, match=BOTS):
        specs.INGREDIENTS.check_writable("price_per_kg")


def test_bots_alive_closes_cards_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Флаг — аварийный рычаг: ожил бот — снова True, и карточки закрыты
    целиком. Критерий именно «пишет ли живой бот», а не «это справочник»."""
    monkeypatch.setattr(ownership, "BOTS_ALIVE", True)

    assert specs.INGREDIENT_CARDS.writable() == ()
    with pytest.raises(ForbiddenWriteError, match=BOTS):
        specs.INGREDIENT_CARDS.check_writable("supplier")


def test_opening_kitchen_book_unblocks_ing_but_not_menu_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Открытие книги кухни открывает справочник — и НЕ открывает человеческие
    колонки.

    Это главный тест файла. Когда у книги кухни появится свой путь записи и
    её впишут в WRITE_OPEN, это не должно заодно дать право затирать цены
    коммерсов и цену меню, которую ставит шеф. Владение человека не зависит
    ни от судьбы ботов, ни от открытых книг.
    """
    monkeypatch.setattr(ownership, "WRITE_OPEN", KITCHEN_OPEN)

    # Справочник открылся.
    specs.INGREDIENTS.check_writable("price_per_kg", "status")
    specs.INGREDIENT_CARDS.check_writable("supplier")

    # А человеческое — нет.
    with pytest.raises(ForbiddenWriteError, match=PEOPLE):
        specs.PRICING_NEW.check_writable("price_sale")
    with pytest.raises(ForbiddenWriteError, match=PEOPLE):
        specs.DISHES.check_writable("price_menu")
    with pytest.raises(ForbiddenWriteError, match=PEOPLE):
        specs.INGREDIENT_CARDS.check_writable("declaration")


def test_unknown_field_is_loud() -> None:
    """Опечатка в имени поля должна падать, а не молча ничего не проверить."""
    with pytest.raises(KeyError):
        specs.INGREDIENTS.check_writable("цена_за_кг")


# ---------------------------------------------------------------------------
# Раскладка листов
# ---------------------------------------------------------------------------
def test_pricing_data_starts_at_third_row() -> None:
    """Строка 1 — подсказки коммерсам, строка 2 — шапка."""
    assert specs.PRICING_NEW.header_rows == 2
    assert specs.PRICING_NEW.first_data_row == 3


def test_cooking_methods_remember_old_sheet_name() -> None:
    """Лист переименовывали; загрузчик обязан пережить это."""
    assert "Впитывание масла" in specs.COOKING_METHODS.fallback_titles


def test_dish_price_menu_belongs_to_chef() -> None:
    """Цену меню ставит шеф, и он главнее любого расчёта."""
    assert specs.DISHES.column("price_menu").owner is Owner.HUMAN
