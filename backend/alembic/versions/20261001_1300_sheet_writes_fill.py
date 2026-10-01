"""Журнал записей: действие fill

Ревизия: см. ниже
Создана: 2026-10-01

В журнале `sheet_writes` появляется второе действие — `fill`: платформа
заполняет ручные ячейки строки ING книги кухни, которую формула QUERY уже
вывела для согласованной карточки (ADR-0003, вторая ступень). CHECK
`ck_sheet_writes_action` расширяется: `action in ('append', 'fill')`.

Чек-лист (его же проверяет агент migration-guard):

  * **downgrade работает.** Откат возвращает прежний CHECK
    `action in ('append')`.
  * **Данные.** Подъём данных не трогает. Откат удаляет записи журнала с
    действием `fill` — намеренно: прежний CHECK их не допускает, и без
    удаления откат не вернул бы прежнюю схему. Пропадает только след
    заполнений строк ING; сам лист не меняется, записи `append` (карточки)
    откат не трогает. Откатывать имеет смысл только вместе с кодом писателя
    справочника: без него следы `fill` никому не нужны, а повтор переноса
    той же карточки после повторного подъёма увидит id в строке ING и
    второй записи не сделает.
  * **Совместимо со старым кодом.** Старый код пишет только `append` — новый
    CHECK его пропускает; записи `fill` он ищет по своему ключу запроса и
    не встречает (ключи `ing-fill:…` у него не бывают).
  * **Блокировки.** Замена CHECK держит `sheet_writes` под ACCESS EXCLUSIVE
    на время проверки строк; в журнале единицы и сотни строк — это
    мгновение. Перенос листов в базу журнал не трогает.
  * **Индексы и внешние ключи не меняются.**
  * **Ревизия не импортирует модели приложения.**
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "de79870a0f0e"
down_revision: str | None = "ff5c8d28b0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "sheet_writes"
_CHECK = "ck_sheet_writes_action"


def upgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, "action in ('append', 'fill')")


def downgrade() -> None:
    op.execute("delete from sheet_writes where action = 'fill'")
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, "action in ('append')")
