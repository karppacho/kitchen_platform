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
from kitchen.sync.ownership import Column, ForbiddenWriteError, Kind, Owner, SheetSpec

PEOPLE = "принадлежит людям"
BOOK_CLOSED = re.escape("путь записи книги не открыт (ADR-0003)")
SHEET_CLOSED = re.escape("путь записи листа не открыт (ADR-0003)")
FORMULA = "заполняет формула таблицы"
BOTS = "Telegram-бот"

ING_MANUAL = [*"AELMNOQRST"]
"""Ручные колонки ING — их дописывают шеф и коммерция (ADR-0003, вторая ступень)."""
ING_FORMULA = [*"BCDFGHIJK", "P"]
"""Вывод QUERY (B–D, F–K) и формула общих потерь (P)."""

KITCHEN_SHEETS_CLOSED = [
    specs.PACKAGING,
    specs.DISHES,
    specs.TTK,
    specs.COOKING_METHODS,
    specs.PRICING_NEW,
    specs.PRICING_MENU,
]
"""Листы книги кухни, кроме ING: у них нет своего писателя."""


def _open_whole(*sheets: SheetSpec) -> dict[str, dict[str, ownership.OpenColumns]]:
    """Нынешние ворота плюс эти листы целиком — такого пока нет, это
    проверка на будущее: чего не откроет даже открытый лист."""
    gates: dict[str, dict[str, ownership.OpenColumns]] = {
        book: dict(sheets_open) for book, sheets_open in ownership.WRITE_OPEN.items()
    }
    for spec in sheets:
        gates.setdefault(spec.spreadsheet, {})[spec.title] = ownership.WHOLE_SHEET
    return gates


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


def test_ing_writable_columns_are_the_manual_ones() -> None:
    """Вторая ступень ADR-0003: в книге кухни открыт один лист — ING, и в нём
    только ручные колонки, которые дописывают шеф и коммерция.

    L открыта, хотя в части строк это формула от M: какая строка с
    формулой, видно только по свежему чтению, и пропускает её писатель
    строки, а не ворота.
    """
    letters = [c.letter for c in specs.INGREDIENTS.writable()]
    assert letters == ING_MANUAL
    specs.INGREDIENTS.check_writable(
        "id",
        "short_name",
        "price_per_kg",
        "price_per_pack",
        "unit",
        "weight_per_piece_g",
        "losses_unpacking",
        "losses_cutting",
        "losses_thermal",
        "status",
    )


@pytest.mark.parametrize("letter", ING_FORMULA)
def test_ing_formula_columns_are_refused(letter: str) -> None:
    """B–D и F–K выводит формула QUERY, P — формула шефа. Запись в зону
    QUERY ломает вывод формулы во всех строках зоны, поэтому отказ
    называет формулу, а не владельца или ботов."""
    [column] = [c for c in specs.INGREDIENTS.columns if c.letter == letter]

    with pytest.raises(ForbiddenWriteError, match=FORMULA):
        specs.INGREDIENTS.check_writable(column.field)
    with pytest.raises(ForbiddenWriteError, match=FORMULA):
        specs.INGREDIENTS.check_writable("short_name", column.field)


@pytest.mark.parametrize("spec", KITCHEN_SHEETS_CLOSED, ids=lambda spec: spec.title)
def test_other_kitchen_sheets_stay_closed(spec: SheetSpec) -> None:
    """Ворота — по листу, а не по книге: ING открыт, а Упаковка, Блюда, ТТК,
    способы приготовления и обе расчётки — нет, даже их вычисляемые
    колонки. Отказ называет закрытый лист, а не ботов."""
    assert spec.writable() == ()
    for column in spec.columns:
        if column.owner is Owner.HUMAN:
            continue
        with pytest.raises(ForbiddenWriteError, match=SHEET_CLOSED):
            spec.check_writable(column.field)


@pytest.mark.parametrize(
    "spec",
    [specs.COMPETITOR_ITEMS, specs.COMPETITOR_CHANGES, specs.TASTING_RATINGS],
    ids=["конкуренты", "история изменений", "дегустации"],
)
def test_books_without_writer_stay_closed(spec: SheetSpec) -> None:
    """Книги конкурентов и дегустаций — общие, боты сняты, но своего пути
    записи у них нет. Их держит второй замок, и отказ называет именно его."""
    assert spec.writable() == ()
    with pytest.raises(ForbiddenWriteError, match=BOOK_CLOSED):
        spec.check_writable(spec.columns[0].field)


def test_only_cards_and_ing_sheets_are_open() -> None:
    """Храповик: из всех описанных листов запись открыта двум — «Лист1»
    книги карточек и ING книги кухни.

    Открыть ещё что-то — осознанная правка вместе с писателем, а не
    побочный эффект. Проверяются все листы, а не «все книги, кроме
    открытых»: новое описание листа в книге карточек или кухни (там есть и
    листы, которые ведут люди) тоже обязано прийти закрытым или покраснить
    тест. Описания собираются из модуля целиком, а не из списков: лист,
    который забыли вписать в ALL_SPECS, проверку не обойдёт.
    """
    every_spec = [value for value in vars(specs).values() if isinstance(value, SheetSpec)]
    assert specs.TASTING_RATINGS in every_spec, "описания собраны не все"

    open_sheets = [spec for spec in every_spec if spec.writable()]
    assert open_sheets == [specs.INGREDIENTS, specs.INGREDIENT_CARDS]


def test_human_column_is_never_writable() -> None:
    """Цена продажная в расчётке — работа коммерческого отдела."""
    with pytest.raises(ForbiddenWriteError, match=PEOPLE):
        specs.PRICING_NEW.check_writable("price_sale")


def test_app_columns_wait_for_their_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Вычисляемые колонки расчётки — наши, но книга кухни открыта только
    листу ING. Откроют лист расчётки — откроются и они, а цена коммерсов —
    нет."""
    with pytest.raises(ForbiddenWriteError, match=SHEET_CLOSED):
        specs.PRICING_NEW.check_writable("uc_rub")

    monkeypatch.setattr(ownership, "WRITE_OPEN", _open_whole(specs.PRICING_NEW))

    specs.PRICING_NEW.check_writable("uc_rub", "margin_percent", "kcal")
    assert "price_sale" not in {c.field for c in specs.PRICING_NEW.writable()}


def test_shared_columns_blocked_while_bots_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Справочник закрыт, пока в него пишут боты, — даже открытый лист и
    даже ручные колонки ING."""
    monkeypatch.setattr(ownership, "BOTS_ALIVE", True)

    assert specs.INGREDIENTS.writable() == ()
    with pytest.raises(ForbiddenWriteError, match=BOTS):
        specs.INGREDIENTS.check_writable("price_per_kg")


def test_bots_alive_closes_cards_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Флаг — аварийный рычаг: ожил бот — снова True, и карточки закрыты
    целиком. Критерий именно «пишет ли живой бот», а не «это справочник»."""
    monkeypatch.setattr(ownership, "BOTS_ALIVE", True)

    assert specs.INGREDIENT_CARDS.writable() == ()
    with pytest.raises(ForbiddenWriteError, match=BOTS):
        specs.INGREDIENT_CARDS.check_writable("supplier")


def test_bots_alive_closes_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ожил бот — закрыто всё: ни одного описанного листа с открытой
    колонкой. Вычисляемых колонок в открытых листах нет, поэтому рычаг
    закрывает запись платформы целиком."""
    monkeypatch.setattr(ownership, "BOTS_ALIVE", True)

    every_spec = [value for value in vars(specs).values() if isinstance(value, SheetSpec)]
    assert [spec.title for spec in every_spec if spec.writable()] == []


def test_opening_kitchen_sheets_never_unblocks_human_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Открытый лист открывает общие колонки — и НЕ открывает человеческие.

    Это главный тест файла. Когда у Блюд или расчётки появится свой путь
    записи и их впишут в WRITE_OPEN, это не должно заодно дать право
    затирать цены коммерсов и цену меню, которую ставит шеф. Владение
    человека не зависит ни от судьбы ботов, ни от открытых книг и листов.
    """
    # Сейчас: ING открыт, человеческое — нет.
    specs.INGREDIENTS.check_writable("price_per_kg", "status")
    with pytest.raises(ForbiddenWriteError, match=PEOPLE):
        specs.DISHES.check_writable("price_menu")

    monkeypatch.setattr(
        ownership, "WRITE_OPEN", _open_whole(specs.DISHES, specs.PRICING_NEW, specs.PRICING_MENU)
    )

    # Листы открылись.
    specs.DISHES.check_writable("uc_actual", "status")
    specs.PRICING_MENU.check_writable("uc_rub")
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
