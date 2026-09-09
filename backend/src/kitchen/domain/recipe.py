"""Типы, на которых считается себестоимость.

Чистые данные без единой связи с хранилищем: калькулятор не должен знать
ни про SQLAlchemy, ни про Google Sheets. Сборка `Recipe` из моделей базы —
дело слоя выше, и контракт import-linter это стережёт.

Единицы измерения оставлены строками ровно в том виде, в каком их пишет
шеф («кг», «шт», «л», «мл»). Приводить их к перечислению нельзя: любое
новое значение в таблице тогда роняло бы расчёт вместо того, чтобы
превратиться в предупреждение.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

UNIT_PIECE = "шт"
UNIT_WEIGHT = ("кг", "л")
UNIT_MILLILITRE = "мл"

ROW_MAIN = "main"
ROW_PACKAGING = "packaging"


@dataclass(frozen=True, slots=True)
class IngredientSpec:
    """Позиция справочника в том объёме, в каком нужна расчёту."""

    key: str
    name: str
    unit: str
    short_name: str = ""
    """«Короткое для айки». Технологи работают в iiko и по обычному имени
    не всегда понимают, какой полуфабрикат брать."""

    price_per_unit: Decimal | None = None
    """Цена за единицу. `None` и ровно `0` означают одно и то же —
    «данных нет», — и оба обязаны дойти до предупреждения."""

    weight_per_piece_g: Decimal | None = None
    """Вес одной штуки. Без него штучный ингредиент посчитать нельзя:
    в ТТК граммы, а цена за штуку."""

    losses_unpacking: Decimal | None = None
    losses_cutting: Decimal | None = None
    losses_thermal: Decimal | None = None
    """Читаются, но в расчёте НЕ применяются. Осознанный пробел,
    унаследованный от бота: менять поведение здесь — отдельное решение."""

    protein_100g: Decimal | None = None
    fat_100g: Decimal | None = None
    carbs_100g: Decimal | None = None
    kcal_100g: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PackagingSpec:
    """Упаковка: несъедобна, КБЖУ не имеет, потерь не знает."""

    key: str
    name: str
    price_per_piece: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Component:
    """Строка ТТК."""

    row_type: str
    net_weight_g: Decimal
    """Шеф пишет НЕТТО — сколько должно оказаться в блюде. Потери
    калькулятор накидывает сверху. Для строки упаковки это количество штук."""

    ingredient: IngredientSpec | None = None
    packaging: PackagingSpec | None = None


@dataclass(frozen=True, slots=True)
class Recipe:
    """Блюдо с составом — вход калькулятора."""

    key: str
    name: str
    price_menu: Decimal | None = None
    components: tuple[Component, ...] = ()


@dataclass(frozen=True, slots=True)
class ComponentCost:
    """Стоимость одной строки состава."""

    name: str
    short_name: str
    row_type: str
    unit: str
    net_weight_g: Decimal
    gross_weight_g: Decimal | None
    price_per_unit: Decimal | None
    weight_per_piece_g: Decimal | None
    cost_rub: Decimal
    share_percent: Decimal | None = None


@dataclass(slots=True)
class DishCost:
    """Результат расчёта.

    Всё, что зависит от цены меню, равно `None`, когда цены нет.
    Себестоимость и выход считаются всегда: они от цены продажи не зависят.
    Ноль вместо `None` шеф прочитал бы как настоящую нулевую маржу.
    """

    key: str
    name: str
    price_menu: Decimal | None
    uc_rub: Decimal
    uc_percent: Decimal | None
    margin_rub: Decimal | None
    margin_percent: Decimal | None
    output_grams: Decimal
    """Выход съедобной части: сумма НЕТТО основных строк. Упаковка сюда
    не входит, брутто — тоже."""

    protein_g: Decimal
    fat_g: Decimal
    carbs_g: Decimal
    kcal: Decimal
    kbju_coverage: Decimal
    """Доля веса состава, у которой заполнено КБЖУ. Ниже половины цифрам
    доверять нельзя."""

    components: list[ComponentCost] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
