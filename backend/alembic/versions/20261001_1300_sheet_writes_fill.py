"""Журнал записей: действие fill

Ревизия: см. ниже
Создана: 2026-10-01

В журнале `sheet_writes` появляется второе действие — `fill`: платформа
заполняет ручные ячейки строки ING книги кухни, которую формула QUERY уже
вывела для согласованной карточки (ADR-0003, вторая ступень). CHECK
`ck_sheet_writes_action` расширяется: `action in ('append', 'fill')`.

Чек-лист (его же проверяет агент migration-guard):

  * **downgrade работает.** Откат возвращает прежний CHECK
    `action in ('append')`; пока в журнале есть записи `fill` со статусом
    `failed` или `pending`, он отказывает понятной ошибкой (см. «Данные») —
    миграции идут одной транзакцией, база остаётся на этой ревизии.
  * **Данные.** Подъём данных не трогает. Откат удаляет завершённые записи
    журнала с действием `fill` (`verified`, `rolled_back`) — намеренно:
    прежний CHECK их не допускает, и без удаления откат не вернул бы прежнюю
    схему. Пропадает только след удачных и отменённых заполнений строк ING;
    сам лист не меняется, записи `append` (карточки) откат не трогает.
    Записи `fill` со статусом `failed` или `pending` откат **не удаляет** —
    он отказывает: в них снимок листа ING (значения и формулы), единственный
    материал, чтобы вернуть руками затёртое в окне записи. Их сначала
    разбирают и удаляют осознанно, потом откатывают. Откатывать имеет смысл
    только вместе с кодом писателя справочника: повтор переноса той же
    карточки после повторного подъёма увидит id в строке ING и второй записи
    не сделает.
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

import sqlalchemy as sa
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
    kept = (
        op.get_bind()
        .execute(
            sa.text(
                "select id from sheet_writes where action = 'fill' "
                "and status in ('failed', 'pending') order by id"
            )
        )
        .scalars()
        .all()
    )
    if kept:
        numbers = ", ".join(f"№{write_id}" for write_id in kept)
        raise RuntimeError(
            f"Откат невозможен: в журнале sheet_writes записи fill со статусом failed или "
            f"pending ({numbers}). В них снимок листа ING — материал, чтобы вернуть затёртое "
            "руками. Разберите их и удалите осознанно, потом откатывайте."
        )
    op.execute("delete from sheet_writes where action = 'fill'")
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, "action in ('append')")
