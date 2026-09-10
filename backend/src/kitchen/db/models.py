"""Таблицы базы.

Объём первой схемы — справочник и блюда, то есть ровно то, что читается из
листов сегодня. Конкуренты, дегустации и учётные записи появятся своими
миграциями: одна ревизия — одно осмысленное изменение.

Два решения, проходящие через всю схему:

* **Суррогатный ключ, а не идентификатор из листа.** В листах `id` текстовые
  (`B001`, `12`) и служат ссылками в ТТК, поэтому их нельзя терять — они
  лежат в `legacy_id` с уникальным индексом. Но связи внутри базы идут по
  собственному `id`: текстовый ключ из чужой системы в роли первичного
  однажды упрётся в смену формата.

* **Ни одного числа с плавающей точкой.** Деньги, веса и доли — `Numeric`.
  `float` в расчётном пути даёт копеечные расхождения, которые невозможно
  объяснить шефу.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kitchen.db.base import Base

# Деньги: до 12 знаков, 2 после запятой.
Money = Numeric(12, 2)
# Доли потерь и процентов: 0.2791 — это 27.91%.
Fraction = Numeric(8, 6)
# КБЖУ и веса.
Amount = Numeric(12, 3)


class SyncMixin:
    """Следы происхождения строки.

    Хранятся у каждой сущности, приезжающей из листа. Номер строки — чтобы
    диагностика указывала на место, которое шеф может открыть глазами; хеш —
    основа защиты от петли синхронизации: пришло то же, что мы записали
    сами, — применять обратно нельзя.
    """

    source_row: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Ingredient(Base, SyncMixin):
    """Позиция справочника ING — по ней считается себестоимость."""

    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    legacy_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    # Имя — первичный ключ для людей. Дубли среди активных считаются
    # дефектом данных, но базой не запрещены: они существуют прямо сейчас,
    # и импорт не должен из-за них падать.
    name: Mapped[str] = mapped_column(Text, index=True)
    full_name: Mapped[str] = mapped_column(Text, default="")
    short_name: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(Text, default="", index=True)
    manufacturer: Mapped[str] = mapped_column(Text, default="")
    composition: Mapped[str] = mapped_column(Text, default="")

    protein: Mapped[Decimal | None] = mapped_column(Amount)
    fat: Mapped[Decimal | None] = mapped_column(Amount)
    carbs: Mapped[Decimal | None] = mapped_column(Amount)
    kcal: Mapped[Decimal | None] = mapped_column(Amount)

    # Пусто и ноль — разные вещи. Нет цены → предупреждение в расчёте, а не
    # молчаливый ноль; ноль в потерях → корректное «потерь нет».
    price_per_kg: Mapped[Decimal | None] = mapped_column(Money)
    price_per_pack: Mapped[Decimal | None] = mapped_column(Money)

    unit: Mapped[str] = mapped_column(Text, default="")
    # Без него штучный ингредиент посчитать нельзя: в ТТК граммы, а цена
    # за штуку.
    weight_per_piece_g: Mapped[Decimal | None] = mapped_column(Amount)

    losses_total: Mapped[Decimal | None] = mapped_column(Fraction)
    losses_unpacking: Mapped[Decimal | None] = mapped_column(Fraction)
    losses_cutting: Mapped[Decimal | None] = mapped_column(Fraction)
    # Читаются, но нигде не применяются. Осознанный пробел, не баг.
    losses_thermal: Mapped[Decimal | None] = mapped_column(Fraction)

    status: Mapped[str] = mapped_column(Text, default="", index=True)

    card: Mapped[IngredientCard | None] = relationship(back_populates="ingredient")

    __table_args__ = (Index("ix_ingredients_status_name", "status", "name"),)


class IngredientCard(Base, SyncMixin):
    """Карточка ингредиента: то, что заполняют повара.

    Отдельной таблицей, а не колонками в :class:`Ingredient`, потому что это
    разные документы с разной судьбой. Карточку заводит повар в одной
    таблице, позицию справочника — шеф в другой, и связи между ними сегодня
    нет никакой: 16 карточек из 107 пары не имеют.

    Именно эта таблица — предмет ручной сверки в фазе 1.
    """

    __tablename__ = "ingredient_cards"

    LINK_STATUSES = ("linked", "ambiguous", "candidate", "orphan")

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(Text, index=True)
    category: Mapped[str] = mapped_column(Text, default="")
    label_name: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    manufacturer: Mapped[str] = mapped_column(Text, default="")
    supplier: Mapped[str] = mapped_column(Text, default="")
    composition: Mapped[str] = mapped_column(Text, default="")

    protein: Mapped[Decimal | None] = mapped_column(Amount)
    fat: Mapped[Decimal | None] = mapped_column(Amount)
    carbs: Mapped[Decimal | None] = mapped_column(Amount)
    kcal: Mapped[Decimal | None] = mapped_column(Amount)

    shelf_life_sealed: Mapped[str] = mapped_column(Text, default="")
    shelf_life_defrost: Mapped[str] = mapped_column(Text, default="")
    shelf_life_after: Mapped[str] = mapped_column(Text, default="")
    defrost_conditions: Mapped[str] = mapped_column(Text, default="")

    label_url: Mapped[str] = mapped_column(Text, default="")
    package_url: Mapped[str] = mapped_column(Text, default="")
    before_url: Mapped[str] = mapped_column(Text, default="")
    after_url: Mapped[str] = mapped_column(Text, default="")

    # Заполняются людьми, бот их не трогает.
    declaration: Mapped[str] = mapped_column(Text, default="")
    halal_certificate: Mapped[str] = mapped_column(Text, default="")
    approval_status: Mapped[str] = mapped_column(Text, default="")

    ingredient_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingredients.id", ondelete="SET NULL"), index=True
    )
    link_status: Mapped[str] = mapped_column(String(16), default="orphan", index=True)
    # Подтверждена ли связь человеком. Автоматически найденная пара —
    # предложение, а не решение: подставить не тот ингредиент хуже, чем не
    # подставить никакого.
    link_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ingredient: Mapped[Ingredient | None] = relationship(back_populates="card")

    __table_args__ = (
        CheckConstraint(
            "link_status in ('linked', 'ambiguous', 'candidate', 'orphan')",
            name="ck_ingredient_cards_link_status",
        ),
        CheckConstraint(
            "link_status <> 'linked' or ingredient_id is not null",
            name="ck_ingredient_cards_linked_has_ingredient",
        ),
    )


class Packaging(Base, SyncMixin):
    """Упаковка. Отдельно от ингредиентов: несъедобна, КБЖУ не имеет,
    считается всегда штуками и в выход блюда не входит."""

    __tablename__ = "packaging"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    legacy_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    name: Mapped[str] = mapped_column(Text, index=True)
    full_name: Mapped[str] = mapped_column(Text, default="")
    price_per_piece: Mapped[Decimal | None] = mapped_column(Money)
    dish_category: Mapped[str] = mapped_column(Text, default="")
    supplier: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="", index=True)
    comment: Mapped[str] = mapped_column(Text, default="")


class CookingMethod(Base, SyncMixin):
    """Способ приготовления: норма впитывания масла и рекомендация в ТТК."""

    __tablename__ = "cooking_methods"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    legacy_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    position: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(Text, default="")
    absorption_rate: Mapped[Decimal | None] = mapped_column(Fraction)
    oil_per_100g: Mapped[Decimal | None] = mapped_column(Amount)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str] = mapped_column(Text, default="")


class Dish(Base, SyncMixin):
    """Блюдо."""

    __tablename__ = "dishes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    legacy_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    name: Mapped[str] = mapped_column(Text, index=True)
    category: Mapped[str] = mapped_column(Text, default="", index=True)

    # Цену ставит шеф, и он главнее любого расчёта. Пусто — это «цены нет»,
    # и тогда маржа равна None, а не нулю: ноль шеф прочитал бы как
    # настоящую нулевую маржу.
    price_menu: Mapped[Decimal | None] = mapped_column(Money)
    uc_actual: Mapped[Decimal | None] = mapped_column(Money)

    status: Mapped[str] = mapped_column(Text, default="", index=True)
    comment: Mapped[str] = mapped_column(Text, default="")

    components: Mapped[list[DishComponent]] = relationship(
        back_populates="dish", cascade="all, delete-orphan"
    )


class DishComponent(Base, SyncMixin):
    """Строка ТТК: один ингредиент или одна упаковка в одном блюде."""

    __tablename__ = "dish_components"

    ROW_TYPES = ("main", "packaging")

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    dish_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dishes.id", ondelete="CASCADE"), index=True
    )
    ingredient_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingredients.id", ondelete="RESTRICT"), index=True
    )
    packaging_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("packaging.id", ondelete="RESTRICT"), index=True
    )
    cooking_method_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cooking_methods.id", ondelete="SET NULL"), index=True
    )

    # Шеф пишет НЕТТО — сколько должно оказаться в блюде. Потери калькулятор
    # накидывает сверху, получая брутто; в выход блюда идёт нетто.
    net_weight_g: Mapped[Decimal | None] = mapped_column(Amount)

    row_type: Mapped[str] = mapped_column(String(16), default="main")
    comment: Mapped[str] = mapped_column(Text, default="")

    dish: Mapped[Dish] = relationship(back_populates="components")

    __table_args__ = (
        CheckConstraint(
            "row_type in ('main', 'packaging')",
            name="ck_dish_components_row_type",
        ),
        # Строка обязана ссылаться ровно на одно из двух. Ссылка сразу на
        # ингредиент и упаковку — испорченные данные, которые дадут двойной
        # счёт в себестоимости.
        CheckConstraint(
            "(ingredient_id is not null) <> (packaging_id is not null)",
            name="ck_dish_components_one_target",
        ),
    )


class SyncRun(Base):
    """Один прогон чтения листов.

    Нужен, чтобы «в понедельник импорт видел 130 блюд, а во вторник 128»
    было вопросом к данным, а не к памяти. Заодно это журнал расхождений
    заголовков: сдвиг колонок обнаружится сравнением с прошлым прогоном.
    """

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column()
    note: Mapped[str] = mapped_column(Text, default="")

    sheets: Mapped[list[SyncSheet]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class SyncSheet(Base):
    """Итог по одному листу в рамках прогона."""

    __tablename__ = "sync_sheets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sync_runs.id", ondelete="CASCADE"), index=True
    )

    spreadsheet: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    # Расхождения заголовков — не отказ, а запись: остановить импорт из-за
    # переименованной колонки было бы слишком грубо, промолчать нельзя.
    header_issues: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")

    run: Mapped[SyncRun] = relationship(back_populates="sheets")

    __table_args__ = (UniqueConstraint("run_id", "spreadsheet", "title"),)


class Profile(Base):
    """Пользователь платформы.

    Учётки, пароли и сессии ведёт Supabase Auth в схеме `auth`; сюда мы не
    лезем и своей таблицы под них не заводим. Здесь только то, что знает
    о человеке наша предметная область: как его зовут и что ему можно.

    Связь с `auth.users` по идентификатору, но без внешнего ключа: схема
    `auth` принадлежит GoTrue, и вешать на неё ограничения из наших
    миграций — способ однажды не пережить его обновление.
    """

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    """Тот же идентификатор, что у записи в `auth.users`."""

    email: Mapped[str] = mapped_column(Text, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    roles: Mapped[list[UserRole]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class Role(Base):
    """Роль. Заводится с запасом: пользователей сейчас двое, но модель прав
    должна пережить появление поваров и коммерсантов без переделки."""

    __tablename__ = "roles"

    CHEF = "chef"
    COOK = "cook"
    COMMERCE = "commerce"
    DEVELOPER = "developer"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")


class UserRole(Base):
    """Кто в какой роли."""

    __tablename__ = "user_roles"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    role_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("roles.code", ondelete="RESTRICT"), primary_key=True
    )
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    profile: Mapped[Profile] = relationship(back_populates="roles")

    __table_args__ = (Index("ix_user_roles_role_code", "role_code"),)
