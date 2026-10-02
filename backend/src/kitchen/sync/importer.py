"""Перенос листов в базу.

Направление одно: **лист → база**. Обратной записи здесь нет. В лист
платформа пишет одно — новую строку в «Лист1» книги карточек, через
писатель строки (``kitchen.sync.writer``, docs/adr/0003), — и в базу эта
строка попадает отсюда же, обычным переносом, как правка шефа. Книга кухни
на запись закрыта (docs/adr/0001).

Импорт идёт одной транзакцией. Наполовину перенесённый справочник хуже
непере­несённого: расчёт по нему выглядит рабочим и даёт неверные числа.

Что импорт **не** делает: не решает за человека. Связь карточки со
справочником он предлагает, но подтверждённой считает только ту, которую
подтвердили руками — `link_confirmed_at` переживает переимпорт. И не стирает
строки, исчезнувшие из листа, — скрывает их отметкой `removed_at`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from kitchen.db import models
from kitchen.db.links import LinkReference
from kitchen.domain.matching import NameIndex, normalise_name
from kitchen.sync import specs
from kitchen.sync.reader import SheetData

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from sqlalchemy.orm import Session, sessionmaker

    from kitchen.sync.reader import CellValue, Row, SheetsReader

# Как в листе ТТК называется тип строки.
_ROW_TYPE_PACKAGING = "упаковка"

# Ключ advisory-блокировки импорта — одно число на всю базу: воркер и ручной
# import_sheets.py обязаны стоять в одной очереди.
IMPORT_LOCK_KEY = 20260923


def take_import_lock(session: Session) -> None:
    """Дождаться, пока закончит другой импорт, и занять очередь.

    Два импорта разом задвоили бы состав: оба стирают строки ТТК и вставляют
    свои. Блокировка транзакционная (`xact`), а не сессионная: приложение ходит
    через пулер в transaction-режиме, и сессионная повисла бы на чужом
    соединении. Снимается сама на commit или rollback.
    """
    session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": IMPORT_LOCK_KEY})


_LOCK_BUSY = {"55P03", "57014"}
"""SQLSTATE, с которыми обрывается ожидание: `lock_timeout` и `statement_timeout`."""


def wait_for_import_lock(session: Session, wait: timedelta) -> bool:
    """Занять очередь импорта, ожидая не дольше ``wait``.

    Для тех, кто правит то же, что импорт, и ждёт ответа на экране: пару
    карточки импорт пересчитывает, пока она не подтверждена, — подтверждение
    посреди импорта он бы затёр. ``False`` — другой перенос держит очередь
    дольше: транзакция испорчена, её откатывают. Предел — `SET LOCAL`, на
    эту транзакцию (через пулер сессионный остался бы на чужом соединении).
    """
    milliseconds = max(1, int(wait / timedelta(milliseconds=1)))
    session.execute(text(f"set local lock_timeout = '{milliseconds}ms'"))
    try:
        take_import_lock(session)
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) in _LOCK_BUSY:
            return False
        raise
    return True


@dataclass
class ImportResult:
    """Итог прогона — то, что печатается и кладётся в `sync_runs`."""

    run_id: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Отдельно от warnings: строк «пустой id, пропущена» в живом ING десятки, и
    # они забивают note прогона раньше, чем до него доедет «скрыто» — а это
    # единственный след массового удаления в журнале (см. _mark_presence).
    presence: list[str] = field(default_factory=list)
    # Сколько карточек получили, потеряли или сменили пару в справочнике — по
    # той же причине отдельно от warnings: массовая потеря пар (шеф убрал из
    # ING десяток позиций) обязана дойти до журнала.
    links: list[str] = field(default_factory=list)
    unreadable: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.unreadable

    def add(self, entity: str, number: int) -> None:
        self.counts[entity] = number


def _text(value: CellValue) -> str:
    """Текст ячейки. `None` и числа приводятся к строке без выдумок."""
    if value is None:
        return ""
    return str(value).strip()


def _num(value: CellValue) -> Decimal | None:
    """Число или `None`.

    Пусто остаётся пустым: «нет цены» и «цена ноль» — разные утверждения,
    и первое обязано дойти до предупреждения в расчёте, а не раствориться
    нулём.
    """
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return None


class Importer:
    """Переносит содержимое листов в базу."""

    def __init__(self, reader: SheetsReader, sessions: sessionmaker[Session]) -> None:
        self._reader = reader
        self._sessions = sessions

    # Листы, которые импортируются сейчас. Конкуренты и дегустации приедут
    # своими миграциями и своим кодом.
    SPECS = (
        specs.INGREDIENTS,
        specs.PACKAGING,
        specs.COOKING_METHODS,
        specs.DISHES,
        specs.TTK,
        specs.INGREDIENT_CARDS,
    )

    def run(self) -> ImportResult:
        """Прочитать все листы и перенести прочитанное одной транзакцией."""
        sheets = self._reader.read_many(self.SPECS)
        with self._sessions() as session, session.begin():
            return self.apply(session, sheets)

    def apply(self, session: Session, sheets: Mapping[str, SheetData | str]) -> ImportResult:
        """Перенести уже прочитанные листы в открытой транзакции.

        Листа нет в `sheets` — его сущности не трогаются: ни обновлений, ни
        отметок удаления. Так воркер переносит одну книгу, не задевая другую.
        """
        take_import_lock(session)
        result = ImportResult()
        now = datetime.now(UTC)

        run = models.SyncRun()
        session.add(run)
        session.flush()

        for label, data in sheets.items():
            self._record_sheet(session, run, label, data, result)

        ok_sheets = {
            data.spec.title: data for data in sheets.values() if isinstance(data, SheetData)
        }
        self._import_all(session, ok_sheets, result, now)

        run.finished_at = datetime.now(UTC)
        run.ok = result.ok
        # presence и links — первыми: это самые важные замечания прогона, и
        # обрезка не имеет права их выбросить, пока в warnings болтаются
        # десятки строк «пустой id».
        run.note = "; ".join([*result.presence, *result.links, *result.warnings][:20])
        session.flush()
        result.run_id = run.id
        return result

    # ------------------------------------------------------------------
    def _record_sheet(
        self,
        session: Session,
        run: models.SyncRun,
        label: str,
        data: SheetData | str,
        result: ImportResult,
    ) -> None:
        spreadsheet, _, title = label.partition("/")
        if isinstance(data, str):
            result.unreadable[label] = data
            session.add(
                models.SyncSheet(
                    run_id=run.id, spreadsheet=spreadsheet, title=title, rows=0, error=data
                )
            )
            return

        session.add(
            models.SyncSheet(
                run_id=run.id,
                spreadsheet=spreadsheet,
                title=data.title,
                rows=len(data),
                header_issues="\n".join(data.header_issues),
            )
        )
        if data.header_issues:
            result.warnings.append(f"{label}: расхождений заголовков {len(data.header_issues)}")

    def _import_all(
        self,
        session: Session,
        sheets: dict[str, SheetData],
        result: ImportResult,
        now: datetime,
    ) -> None:
        ingredients = self._import_ingredients(
            session, sheets.get(specs.INGREDIENTS.title), result, now
        )
        packaging = self._import_packaging(session, sheets.get(specs.PACKAGING.title), result, now)
        methods = self._import_methods(
            session, sheets.get(specs.COOKING_METHODS.title), result, now
        )
        dishes = self._import_dishes(session, sheets.get(specs.DISHES.title), result, now)

        self._import_components(
            session, sheets.get(specs.TTK.title), dishes, ingredients, packaging, methods, result
        )
        cards = sheets.get(specs.INGREDIENT_CARDS.title)
        self._import_cards(session, cards, result, now)
        if cards is None and specs.INGREDIENTS.title in sheets:
            # Лист карточек в этом переносе не участвует, а справочник
            # обновился: пары карточек зависят и от него. Лист карточек
            # заполнял бот, боты лежат — ждать его правки значило бы держать
            # на сверке снятый спор тёзок и пару к удалённой позиции.
            self._suggest_links(session, result)

    # ------------------------------------------------------------------
    def _import_ingredients(
        self, session: Session, data: SheetData | None, result: ImportResult, now: datetime
    ) -> dict[str, models.Ingredient]:
        existing: dict[str, models.Ingredient] = {
            row.legacy_id: row for row in session.scalars(select(models.Ingredient)).all()
        }
        if data is None:
            return existing

        present: set[str] = set()
        for row in _identified(data.rows, "id", result, "ING"):
            key = _text(row["id"])
            present.add(key)
            item = existing.get(key) or models.Ingredient(legacy_id=key)
            item.name = _text(row["name"])
            item.full_name = _text(row["full_name"])
            item.short_name = _text(row["short_name"])
            item.category = _text(row["category"])
            item.manufacturer = _text(row["manufacturer"])
            item.composition = _text(row["composition"])
            item.protein = _num(row["protein"])
            item.fat = _num(row["fat"])
            item.carbs = _num(row["carbs"])
            item.kcal = _num(row["kcal"])
            item.price_per_kg = _num(row["price_per_kg"])
            item.price_per_pack = _num(row["price_per_pack"])
            item.unit = _text(row["unit"])
            item.weight_per_piece_g = _num(row["weight_per_piece_g"])
            item.losses_total = _num(row["losses_total"])
            item.losses_unpacking = _num(row["losses_unpacking"])
            item.losses_cutting = _num(row["losses_cutting"])
            item.losses_thermal = _num(row["losses_thermal"])
            item.status = _text(row["status"])
            _stamp(item, row)
            session.add(item)
            existing[key] = item

        _mark_presence(existing, present, now, result, "ING")
        session.flush()
        result.add("ингредиенты", len(present))
        return existing

    def _import_packaging(
        self, session: Session, data: SheetData | None, result: ImportResult, now: datetime
    ) -> dict[str, models.Packaging]:
        existing: dict[str, models.Packaging] = {
            row.legacy_id: row for row in session.scalars(select(models.Packaging)).all()
        }
        if data is None:
            return existing

        present: set[str] = set()
        for row in _identified(data.rows, "id", result, "Упаковка"):
            key = _text(row["id"])
            present.add(key)
            item = existing.get(key) or models.Packaging(legacy_id=key)
            item.name = _text(row["name"])
            item.full_name = _text(row["full_name"])
            item.price_per_piece = _num(row["price_per_piece"])
            item.dish_category = _text(row["dish_category"])
            item.supplier = _text(row["supplier"])
            item.status = _text(row["status"])
            item.comment = _text(row["comment"])
            _stamp(item, row)
            session.add(item)
            existing[key] = item

        _mark_presence(existing, present, now, result, "Упаковка")
        session.flush()
        result.add("упаковка", len(present))
        return existing

    def _import_methods(
        self, session: Session, data: SheetData | None, result: ImportResult, now: datetime
    ) -> dict[str, models.CookingMethod]:
        existing: dict[str, models.CookingMethod] = {
            row.legacy_id: row for row in session.scalars(select(models.CookingMethod)).all()
        }
        if data is None:
            return existing

        present: set[str] = set()
        for row in _identified(data.rows, "id", result, "Способы приготовления"):
            key = _text(row["id"])
            present.add(key)
            item = existing.get(key) or models.CookingMethod(legacy_id=key)
            item.position = _text(row["position"])
            item.method = _text(row["method"])
            item.absorption_rate = _num(row["absorption_rate"])
            item.oil_per_100g = _num(row["oil_per_100g"])
            item.recommendation = _text(row["recommendation"])
            item.comment = _text(row["comment"])
            _stamp(item, row)
            session.add(item)
            existing[key] = item

        _mark_presence(existing, present, now, result, "Способы приготовления")
        session.flush()
        result.add("способы приготовления", len(present))
        return existing

    def _import_dishes(
        self, session: Session, data: SheetData | None, result: ImportResult, now: datetime
    ) -> dict[str, models.Dish]:
        existing: dict[str, models.Dish] = {
            row.legacy_id: row for row in session.scalars(select(models.Dish)).all()
        }
        if data is None:
            return existing

        present: set[str] = set()
        for row in _identified(data.rows, "id", result, "Блюда"):
            key = _text(row["id"])
            present.add(key)
            item = existing.get(key) or models.Dish(legacy_id=key)
            item.name = _text(row["name"])
            item.category = _text(row["category"])
            item.price_menu = _num(row["price_menu"])
            item.uc_actual = _num(row["uc_actual"])
            item.status = _text(row["status"])
            item.comment = _text(row["comment"])
            _stamp(item, row)
            session.add(item)
            existing[key] = item

        _mark_presence(existing, present, now, result, "Блюда")
        session.flush()
        result.add("блюда", len(present))
        return existing

    def _import_components(
        self,
        session: Session,
        data: SheetData | None,
        dishes: dict[str, models.Dish],
        ingredients: dict[str, models.Ingredient],
        packaging: dict[str, models.Packaging],
        methods: dict[str, models.CookingMethod],
        result: ImportResult,
    ) -> None:
        if data is None:
            return

        # У строк ТТК нет собственного идентификатора: лист — это просто
        # перечень. Поэтому состав пересобирается целиком, а не правится
        # построчно. Это безопасно ровно потому, что источник истины здесь
        # лист, а база его зеркалит.
        for known in dishes.values():
            known.components.clear()
        session.flush()

        imported = 0
        for row in data.rows:
            dish_key = _text(row["dish_id"])
            dish: models.Dish | None = dishes.get(dish_key)
            if dish is None:
                if dish_key:
                    result.warnings.append(
                        f"ТТК строка {row.number}: блюда «{dish_key}» нет в справочнике"
                    )
                continue

            ingredient = ingredients.get(_text(row["ingredient_id"]))
            pack = packaging.get(_text(row["packaging_id"]))

            # Ограничение базы требует ровно одну ссылку. Строка с обеими
            # или без единой — испорченные данные: первая даст двойной счёт
            # в себестоимости, вторая ничего не значит.
            if (ingredient is None) == (pack is None):
                result.warnings.append(
                    f"ТТК строка {row.number} ({dish_key}): "
                    f"ожидалась ровно одна ссылка — на ингредиент или на упаковку"
                )
                continue

            session.add(
                models.DishComponent(
                    dish_id=dish.id,
                    ingredient_id=ingredient.id if ingredient else None,
                    packaging_id=pack.id if pack else None,
                    cooking_method_id=(
                        methods[_text(row["cooking_method_id"])].id
                        if _text(row["cooking_method_id"]) in methods
                        else None
                    ),
                    net_weight_g=_num(row["net_weight_g"]),
                    row_type=(
                        "packaging"
                        if pack is not None or _text(row["row_type"]).lower() == _ROW_TYPE_PACKAGING
                        else "main"
                    ),
                    comment=_text(row["comment"]),
                    source_row=row.number,
                    content_hash=row.content_hash,
                )
            )
            imported += 1

        session.flush()
        result.add("строки ТТК", imported)

    def _import_cards(
        self, session: Session, data: SheetData | None, result: ImportResult, now: datetime
    ) -> None:
        if data is None:
            return

        # У карточек нет идентификатора в листе — ключом служит имя.
        # Подтверждённые человеком связи переносятся: переимпорт не должен
        # обнулять чужую работу.
        existing = {
            normalise_name(card.name): card
            for card in session.scalars(select(models.IngredientCard)).all()
        }

        seen: set[str] = set()
        for row in data.rows:
            name = _text(row["name"])
            if not name:
                continue
            key = normalise_name(name)
            if key in seen:
                # Две карточки с одним именем — дефект данных: ключа, кроме
                # имени, у карточек нет, и вторая молча затирает первую.
                # Молчать об этом нельзя: повар заполнял обе.
                result.warnings.append(
                    f"Карточки строка {row.number}: «{name}» уже была выше — "
                    f"вторая карточка затрёт первую, ключа кроме имени у них нет"
                )
            seen.add(key)

            card = existing.get(key) or models.IngredientCard(name=name)
            card.name = name
            card.category = _text(row["category"])
            card.label_name = _text(row["label_name"])
            card.description = _text(row["description"])
            card.manufacturer = _text(row["manufacturer"])
            card.supplier = _text(row["supplier"])
            card.composition = _text(row["composition"])
            card.protein = _num(row["protein"])
            card.fat = _num(row["fat"])
            card.carbs = _num(row["carbs"])
            card.kcal = _num(row["kcal"])
            card.shelf_life_sealed = _text(row["shelf_life_sealed"])
            card.shelf_life_defrost = _text(row["shelf_life_defrost"])
            card.shelf_life_after = _text(row["shelf_life_after"])
            card.defrost_conditions = _text(row["defrost_conditions"])
            card.label_url = _text(row["label_url"])
            card.package_url = _text(row["package_url"])
            card.before_url = _text(row["before_url"])
            card.after_url = _text(row["after_url"])
            card.declaration = _text(row["declaration"])
            card.halal_certificate = _text(row["halal_certificate"])
            card.approval_status = _text(row["approval_status"])
            _stamp(card, row)

            session.add(card)
            existing[key] = card

        _mark_presence(existing, seen, now, result, "Карточки")
        self._suggest_links(session, result)
        session.flush()

        by_status: dict[str, int] = {}
        for key, card in existing.items():
            if key in seen:
                by_status[card.link_status] = by_status.get(card.link_status, 0) + 1
        for status, number in sorted(by_status.items()):
            result.add(f"карточки: {status}", number)
        result.add("карточки", len(seen))

    def _suggest_links(self, session: Session, result: ImportResult) -> None:
        """Подобрать пары карточкам, чью связь не подтвердил человек.

        Один путь на два случая: пара зависит от обеих книг — от имени
        карточки и от справочника ING. Поэтому подбор идёт и при переносе
        листа карточек, и при переносе одной кухни. Карточки берутся из базы:
        прочитался ли их лист в этом цикле, неважно.

        Подтверждённые связи не трогаем — решение за человеком. Скрытые
        карточки тоже: вернётся строка — подберём.
        """
        # Справочник — тот же, по которому «Сверка» показывает кандидатов и
        # «Это он» их проверяет; удалённое из листа в нём не предлагается.
        index = LinkReference(session).index
        cards = session.scalars(
            select(models.IngredientCard).where(
                models.IngredientCard.removed_at.is_(None),
                models.IngredientCard.link_confirmed_at.is_(None),
            )
        ).all()

        gained = lost = changed = 0
        for card in cards:
            before = card.ingredient_id
            self._suggest_link(card, index)
            if before == card.ingredient_id:
                continue
            if before is None:
                gained += 1
            elif card.ingredient_id is None:
                lost += 1
            else:
                changed += 1

        for number, what in (
            (gained, "получили пару"),
            (lost, "потеряли пару"),
            (changed, "сменили пару"),
        ):
            if number:
                result.links.append(f"Карточки: {what} в справочнике — {number}")

    def _suggest_link(self, card: models.IngredientCard, index: NameIndex) -> None:
        """Предложить пару. Именно предложить — решение за человеком."""
        match = index.match(card.name)
        if match.resolved is not None:
            card.ingredient_id = int(match.resolved.key)
            card.link_status = "linked"
        elif match.ambiguous:
            card.ingredient_id = None
            card.link_status = "ambiguous"
        elif match.similar:
            card.ingredient_id = None
            card.link_status = "candidate"
        else:
            card.ingredient_id = None
            card.link_status = "orphan"


def _mark_presence(
    rows: Mapping[str, models.RemovedMixin],
    present: set[str],
    now: datetime,
    result: ImportResult,
    sheet: str,
) -> None:
    """Скрыть строки, которых больше нет в листе, и вернуть вернувшиеся.

    Не стираем (решение Александра 23.09): шеф вернёт строку — вернутся и её
    связи, включая подтверждённые на экране сверки. Сколько скрыто и вернулось —
    в `result.presence`, а не в `warnings`: массовое исчезновение обязано быть
    видно в note прогона, а warnings на живых листах — это ещё и десятки строк
    «пустой id», которые вытеснили бы его при обрезке до двадцати записей.
    """
    hidden = restored = 0
    for key, item in rows.items():
        if key in present:
            if item.removed_at is not None:
                item.removed_at = None
                restored += 1
        elif item.removed_at is None:
            item.removed_at = now
            hidden += 1
    if hidden:
        result.presence.append(f"{sheet}: скрыто строк, которых больше нет в листе, — {hidden}")
    if restored:
        result.presence.append(f"{sheet}: вернулись в лист строки — {restored}")


def _stamp(item: object, row: Row) -> None:
    item.source_row = row.number  # type: ignore[attr-defined]
    item.content_hash = row.content_hash  # type: ignore[attr-defined]


def _identified(
    rows: Sequence[Row], key_field: str, result: ImportResult, sheet: str
) -> Iterable[Row]:
    """Строки с непустым идентификатором.

    Строка без id — не ошибка импорта, а мусор в листе: подклеенный
    комментарий, недописанная позиция. Пропускаем, но называем вслух.
    """
    for row in rows:
        if _text(row[key_field]):
            yield row
        else:
            result.warnings.append(f"{sheet} строка {row.number}: пустой id, пропущена")
