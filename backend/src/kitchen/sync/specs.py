"""Раскладка листов в четырёх Google-таблицах.

Колонки перечислены буквами и заголовками ровно так, как их видит шеф.
Заголовок хранится не для красоты: по нему проверяется, что таблица не
переехала, — молчаливый сдвиг колонок означал бы, что цена читается из
колонки веса.

Владение проставлено по одному критерию: **пишет ли в эту колонку живой
Telegram-бот или живой человек.** Всё, во что пишет бот, помечено SHARED и
до вывода бота нам недоступно на запись.
"""

from __future__ import annotations

from kitchen.sync.ownership import Column, Kind, Owner, SheetSpec

_T, _D, _P, _I = Kind.TEXT, Kind.DECIMAL, Kind.PERCENT, Kind.INT
_HUMAN, _APP, _SHARED = Owner.HUMAN, Owner.APP, Owner.SHARED


# ===========================================================================
# Рабочая таблица шефа. Пишет kitchen_bot.
# ===========================================================================

INGREDIENTS = SheetSpec(
    spreadsheet="kitchen",
    title="ING",
    key_field="id",
    columns=(
        Column("A", "id", "id", _T, _SHARED),
        Column("B", "Категория", "category", _T, _SHARED),
        # Имя — первичный ключ для людей: бот ищет ингредиенты по нему.
        # Дубли считаются только среди активных.
        Column("C", "Наименование ингредиента", "name", _T, _SHARED),
        Column("D", "Полное наименование", "full_name", _T, _SHARED),
        Column("E", "Короткое для айки", "short_name", _T, _SHARED),
        Column("F", "Изготовитель", "manufacturer", _T, _SHARED),
        Column("G", "Состав", "composition", _T, _SHARED),
        Column("H", "Белки", "protein", _D, _SHARED, header="Белки на 100г"),
        Column("I", "Жиры", "fat", _D, _SHARED, header="Жиры на 100г"),
        Column("J", "Углеводы", "carbs", _D, _SHARED, header="Углеводы на 100г"),
        Column("K", "Ккал", "kcal", _D, _SHARED, header="Ккал на 100г"),
        Column(
            "L",
            "Цена за 1 кг",
            "price_per_kg",
            _D,
            _SHARED,
            header="Цена за 1 кг (или 1 шт / 1 л), ₽",
        ),
        Column(
            "M",
            "Закупочная цена за упаковку",
            "price_per_pack",
            _D,
            _SHARED,
            header="Закупочная цена за упаковку, ₽",
        ),
        Column("N", "Единица измерения", "unit", _T, _SHARED),
        # Без этого поля штучный ингредиент посчитать нельзя: в ТТК граммы,
        # а цена за штуку.
        Column("O", "Вес 1 шт, г", "weight_per_piece_g", _D, _SHARED),
        Column("P", "Общие потери", "losses_total", _P, _SHARED, header="Общие потери, %"),
        Column(
            "Q",
            "Потери: перетарка",
            "losses_unpacking",
            _P,
            _SHARED,
            header="потери перетарка/дефрост",
        ),
        Column("R", "Потери: нарезка", "losses_cutting", _P, _SHARED, header="потери нарезка"),
        # Читается, но нигде не применяется. Осознанный пробел, не баг.
        Column(
            "S",
            "Потери: тепловая",
            "losses_thermal",
            _P,
            _SHARED,
            header="потери тепловая обработка",
        ),
        Column("T", "Статус", "status", _T, _SHARED),
    ),
)

PACKAGING = SheetSpec(
    spreadsheet="kitchen",
    title="Упаковка",
    key_field="id",
    columns=(
        Column("A", "id", "id", _T, _SHARED),
        Column("B", "Название", "name", _T, _SHARED),
        Column("C", "Полное наименование", "full_name", _T, _SHARED),
        Column(
            "D",
            "Цена за 1 шт",
            "price_per_piece",
            _D,
            _SHARED,
            header="Цена за 1 кг (или 1 шт / 1 л), ₽",
        ),
        Column("E", "Категория блюд", "dish_category", _T, _SHARED),
        Column("F", "Поставщик", "supplier", _T, _SHARED),
        Column("G", "Статус", "status", _T, _SHARED),
        Column("H", "Комментарий", "comment", _T, _SHARED),
    ),
)

DISHES = SheetSpec(
    spreadsheet="kitchen",
    title="Блюда",
    key_field="id",
    columns=(
        Column("A", "id", "id", _T, _SHARED),
        Column("B", "Название", "name", _T, _SHARED),
        Column("C", "Категория блюд", "category", _T, _SHARED),
        # Цену меню ставит шеф, и он главнее любого расчёта: resolve_price
        # в kitchen_bot берёт её первой. Мы её только читаем.
        Column("D", "Цена меню", "price_menu", _D, _HUMAN, header="Цена меню, ₽"),
        Column("E", "UC фактический", "uc_actual", _D, _SHARED, header="UC фактический iiko"),
        Column("F", "Статус", "status", _T, _SHARED),
        Column("G", "Дата создания", "created_at", _T, _SHARED),
        Column(
            "H", "Дата изменения", "updated_at", _T, _SHARED, header="Дата последнего изменения"
        ),
        Column("I", "Комментарий", "comment", _T, _SHARED),
    ),
)

TTK = SheetSpec(
    spreadsheet="kitchen",
    title="ТТК",
    columns=(
        Column("A", "id_блюда", "dish_id", _T, _SHARED),
        Column("B", "id_ингредиента", "ingredient_id", _T, _SHARED),
        Column("C", "id_упаковки", "packaging_id", _T, _SHARED),
        # Шеф пишет НЕТТО — сколько должно оказаться в блюде. Потери
        # калькулятор накидывает сверху, получая брутто.
        Column("D", "Вес нетто, г", "net_weight_g", _D, _SHARED, header="Веснетто,г"),
        Column("E", "Способ_приготовления_id", "cooking_method_id", _T, _SHARED),
        Column("F", "Тип строки", "row_type", _T, _SHARED),
        Column("G", "Комментарий", "comment", _T, _SHARED),
    ),
)

COOKING_METHODS = SheetSpec(
    spreadsheet="kitchen",
    title="Способы приготовления",
    # Лист переименовывали; загрузчик обязан пережить это, а не упасть.
    fallback_titles=("Впитывание масла",),
    key_field="id",
    columns=(
        Column("A", "id", "id", _T, _SHARED),
        Column("B", "Позиция", "position", _T, _SHARED),
        Column("C", "Способ", "method", _T, _SHARED, header="Способ приготовления"),
        Column(
            "D",
            "Норма впитывания",
            "absorption_rate",
            _P,
            _SHARED,
            header="Норма впитывания масла %",
        ),
        Column("E", "Масло на 100 г", "oil_per_100g", _D, _SHARED, header="Масло на 100 г сырья"),
        Column("F", "Рекомендация", "recommendation", _T, _SHARED, header="Рекомендация для ТТК"),
        Column("G", "Комментарий", "comment", _T, _SHARED),
    ),
)

# Расчётка. Лист принадлежит программе, но лежит рядом с данными, которые
# правят руками, — и колонка цены заполняется коммерческим отделом.
_PRICING_COLUMNS = (
    Column("A", "Название", "name", _T, _APP),
    # Ради этой колонки перезапись делается не «в лоб»: без переноса
    # введённых значений первый же пересчёт стёр бы работу коммерсов.
    Column("B", "Цена продажная", "price_sale", _D, _HUMAN),
    Column("C", "Unit cost, РУБ", "uc_rub", _D, _APP),
    Column("D", "Unit cost, %", "uc_percent", _P, _APP),
    Column("E", "МАРЖА %", "margin_percent", _P, _APP),
    Column("F", "Вес продукта", "output_grams", _D, _APP),
    Column("G", "Белки", "protein", _D, _APP),
    Column("H", "Жиры", "fat", _D, _APP),
    Column("I", "Углеводы", "carbs", _D, _APP),
    Column("J", "Каллории", "kcal", _D, _APP),
)

# Строка 1 — подсказки коммерсам, строка 2 — шапка, данные с третьей.
PRICING_NEW = SheetSpec(
    spreadsheet="kitchen",
    title="Расчётка новинки",
    header_rows=2,
    key_field="name",
    columns=_PRICING_COLUMNS,
)

PRICING_MENU = SheetSpec(
    spreadsheet="kitchen",
    title="Расчётка меню",
    header_rows=2,
    key_field="name",
    columns=_PRICING_COLUMNS,
)


# ===========================================================================
# Карточки ингредиентов. Пишет pizza_bot_new.
# ===========================================================================

# Шапка занимает две строки с объединёнными ячейками: в первой групповые
# названия («Пищевая и энергетическая ценность ингредиента»), во второй —
# подзаголовки под ними. Для колонок вне групп вторая строка пуста, и
# значащий заголовок остаётся в первой; читатель ищет снизу вверх.
#
# В этой таблице есть и другие листы — «Фритюрные продукты», «Топпинги»,
# «Овощи», «Соусы», «Заявки», — но бот пишет только в «Лист1».
INGREDIENT_CARDS = SheetSpec(
    spreadsheet="ingredient_cards",
    title="Лист1",
    header_rows=2,
    key_field="name",
    columns=(
        Column("A", "Категория", "category", _T, _SHARED),
        Column("B", "Наименование ингредиента", "name", _T, _SHARED),
        Column("C", "Наименование по маркировочному ярлыку", "label_name", _T, _SHARED),
        Column("D", "Описание", "description", _T, _SHARED),
        Column("E", "Изготовитель", "manufacturer", _T, _SHARED),
        Column("F", "Поставщик", "supplier", _T, _SHARED),
        Column("G", "Состав", "composition", _T, _SHARED),
        Column("H", "Белки", "protein", _D, _SHARED),
        Column("I", "Жиры", "fat", _D, _SHARED),
        Column("J", "Углеводы", "carbs", _D, _SHARED),
        Column("K", "Ккал", "kcal", _D, _SHARED),
        Column(
            "L",
            "Срок в закрытой упаковке",
            "shelf_life_sealed",
            _T,
            _SHARED,
            header="В закрытой упаковке и условиях хранения производителя. Для необработанных овощей - до проведения обработки и нарезки.",
        ),
        Column(
            "M",
            "Срок после дефростации",
            "shelf_life_defrost",
            _T,
            _SHARED,
            header="Срок годности после дефростации в закрытой упаковке производителя. Срок годности после дефростации в таре пиццерии, если упаковка производителя негерметична.",
        ),
        Column(
            "N",
            "Срок после нарезки / фасовки",
            "shelf_life_after",
            _T,
            _SHARED,
            header="Сроки годности после: - нарезки; - фасовки из упаковки производителя; - изменения температурного режима хранения.",
        ),
        Column(
            "O",
            "Условия дефростации",
            "defrost_conditions",
            _T,
            _SHARED,
            header="Условия и срок дефростации, если требуется разморозка",
        ),
        Column("P", "Ссылка на этикетку", "label_url", _T, _SHARED, header="Этикетка ссылкой"),
        # Q и R бот не трогает намеренно: их заполняют люди.
        Column("Q", "Декларация о соответствии", "declaration", _T, _HUMAN),
        Column("R", "Сертификат халяль", "halal_certificate", _T, _HUMAN),
        Column("S", "Фото в упаковке", "package_url", _T, _SHARED, header="В упаковке"),
        Column("T", "Фото до обработки", "before_url", _T, _SHARED, header="До обработки"),
        Column("U", "Фото после обработки", "after_url", _T, _SHARED, header="После обработки"),
        Column("V", "Статус согласования", "approval_status", _T, _SHARED, header="Согласован"),
    ),
)


# ===========================================================================
# Конкуренты. Пишет kitchen_bot.
# ===========================================================================

COMPETITOR_ITEMS = SheetSpec(
    spreadsheet="competitors",
    title="Конкуренты",
    columns=(
        Column("A", "Сайт", "site", _T, _SHARED),
        Column("B", "Категория", "category", _T, _SHARED),
        Column("C", "Позиция", "item", _T, _SHARED),
        Column("D", "Вес", "weight", _T, _SHARED),
        Column("E", "Цена", "price_rub", _D, _SHARED),
        Column("F", "Состав", "composition", _T, _SHARED),
        Column("G", "Дата среза", "taken_at", _T, _SHARED),
    ),
)

COMPETITOR_CHANGES = SheetSpec(
    spreadsheet="competitors",
    title="История изменений",
    columns=(
        Column("A", "Сайт", "site", _T, _SHARED),
        Column("B", "Позиция", "item", _T, _SHARED),
        Column("C", "Вес", "weight", _T, _SHARED),
        Column("D", "Было", "old_price", _D, _SHARED),
        Column("E", "Стало", "new_price", _D, _SHARED),
        Column("F", "Изменение, ₽", "delta_rub", _D, _SHARED, header="Изменение ₽"),
        Column("G", "Изменение %", "delta_percent", _P, _SHARED),
        Column("H", "Тип", "change_type", _T, _SHARED),
        Column("I", "Дата обнаружения", "detected_at", _T, _SHARED),
    ),
)


# ===========================================================================
# Дегустации. Пишет tasting_bot. Лист на каждую дату, имя = дата.
# ===========================================================================

TASTING_RATINGS = SheetSpec(
    spreadsheet="tastings",
    title="",  # подставляется именем листа конкретной дегустации
    columns=(
        Column("A", "№", "number", _I, _SHARED),
        Column("B", "кто заполняет?", "jury_name", _T, _SHARED),
        Column("C", "Что за позиция?", "position", _T, _SHARED),
        Column("D", "как тебе визуально?( оценку от 1 до 10)", "visual", _I, _SHARED),
        Column("E", "вкус продукта( оценка от 1 до 10)", "taste", _I, _SHARED),
        Column("F", "Что понравилось?", "liked", _T, _SHARED),
        Column("G", "Что не понравилось?", "disliked", _T, _SHARED),
        Column("H", "какой в целом комментарий по продукту?", "comment", _T, _SHARED),
    ),
)


ALL_SPECS: tuple[SheetSpec, ...] = (
    INGREDIENTS,
    PACKAGING,
    DISHES,
    TTK,
    COOKING_METHODS,
    PRICING_NEW,
    PRICING_MENU,
    INGREDIENT_CARDS,
    COMPETITOR_ITEMS,
    COMPETITOR_CHANGES,
)
