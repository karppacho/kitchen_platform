# Синхронизация «лист → БД» раз в 5 минут — план реализации

## Контекст

Этап 2 плана после фазы 2, пункт фазы 1 роадмапа «Поллинг раз в 5 минут». Данные на сайте
сейчас — ручной импорт 23.09.2026 15:29: правки шефа в Google-таблице сами на сайт не попадают.
Спека согласована с Александром по частям и целиком:
`docs/superpowers/specs/2026-09-23-sinhronizatsiya-design.md` (ветка `sinhronizatsiya`, коммит
7664017). Его решения: отдельный воркер; удалённое из листа — скрыть, но помнить; удалённый
ингредиент в ТТК — считать по последним данным с замечанием; причину сбоя видят все.

Итог работы: контейнер `worker` раз в 5 минут читает обе книги, переносит только изменения,
книгу со сбоем чтения или сдвинутыми колонками не трогает; удалённое из листа скрыто с сайта;
над каждым экраном строка «Данные из таблицы на ЧЧ:ММ» или полоса с причиной, открытые экраны
обновляются сами.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** воркер синхронизации «лист → БД» раз в 5 минут со скрытием удалённого и строкой
свежести на сайте.

**Architecture:** логика цикла — в `kitchen.sync.cycle` (чистые решения по книгам +
транзакция с advisory-блокировкой поверх существующего `Importer`), точка входа —
`kitchen.worker`. Состояние книг — таблица `sync_state`, отметка удаления — `removed_at`.
Сайт читает состояние через `GET /api/sync`; фронтенд рисует строку свежести в оболочке и
инвалидирует запросы при смене `changed_at`.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Alembic, FastAPI, pydantic, psycopg3 через
Supavisor (transaction-режим), pytest; React 18 + TypeScript + TanStack Query 5 + msw + vitest.

**Spec:** `docs/superpowers/specs/2026-09-23-sinhronizatsiya-design.md`

## Global Constraints

- Направление одно: **лист → БД**. Ни одной записи в Google-таблицы (ADR-0001, CLAUDE.md правило 1).
- Интервал `SYNC_INTERVAL_SECONDS` = 300; порог «устарело» `SYNC_STALE_AFTER_SECONDS` = 900.
- Книги: `kitchen` = ING, Упаковка, Способы приготовления, Блюда, ТТК (только вместе);
  `ingredient_cards` = лист карточек (отдельно).
- Книга с непрочитанным листом или расхождением заголовков не переносится — и воркером, и
  вручную.
- Advisory-блокировка только транзакционная (`pg_advisory_xact_lock`): пулер в
  transaction-режиме; `prepare_threshold=None` уже стоит в `db/session.py`.
- Деньги и веса — `Decimal`, ни одного `float` в расчёте. mypy strict с
  `disallow_any_explicit` для `kitchen.domain.*` и `kitchen.sync.*`.
- Слои (import-linter): `kitchen.web | kitchen.worker` → `kitchen.sync` → `kitchen.db` →
  `kitchen.domain`; `kitchen.config` доступен всем.
- ruff: DTZ включён — только `datetime.now(UTC)` и `datetime(..., tzinfo=UTC)`, и в тестах тоже;
  T20 — `print` только в `scripts/`.
- Имена в бэкенде английские, во фронтенде — транслит (как в соседних файлах). Комментарии,
  документы, тексты для шефа — по-русски.
- Эталон `backend/tests/golden/dishes_uc.json` не трогается; `verify_golden.py` — 129 из 130.
- Блокирующий CI — до пяти минут. Коммиты заканчиваются строкой
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Машина — Windows: перед `uv` сбрасывать `VIRTUAL_ENV=`; файлы писать только инструментами
  Write/Edit (Python `write_text` на Windows пишет CRLF); после правок `git ls-files --eol` —
  в рабочей копии `w/lf`.

## Как исполнять

- **SDD, как в фазе 2:** по задаче — свежий исполнитель, затем отдельный ревьюер; задачи 1, 2,
  3 (схема, импорт, расчёт) дополнительно смотрит `domain-guard` (инструкция
  `.claude/agents/domain-guard.md`); перед слиянием — итоговое ревью всей ветки на самой
  способной модели.
- **Интеграционные тесты локально не запускаются**: на машине нет Postgres и Docker. Они
  идут в CI (`uv run pytest -m integration` с service-контейнером Postgres 16). Красная фаза
  таких тестов — отдельным пушем в черновой PR: CI обязан покраснеть **ровно на новых тестах**,
  затем пуш реализации — зелёный. Офлайн-тесты и фронтенд — красная фаза локально, как обычно.
- Локальные проверки бэкенда (из `backend/`): `VIRTUAL_ENV= uv run ruff check . && VIRTUAL_ENV= uv run ruff format --check . && VIRTUAL_ENV= uv run mypy src && VIRTUAL_ENV= uv run lint-imports && VIRTUAL_ENV= uv run pytest`.
  Фронтенда (из `frontend/`): `npm run types && npm run lint && npm run test && npm run build`.
- Слияние PR — только по слову Александра (классификатор auto mode без него запрещает).

## Карта файлов

| Файл | Что |
|---|---|
| `backend/alembic/versions/20260923_1800_sinhronizatsiya.py` | новая миграция: `removed_at` ×5, `sync_state` |
| `backend/src/kitchen/db/models.py` | `RemovedMixin`, `SyncState` |
| `backend/src/kitchen/sync/importer.py` | `take_import_lock`, `Importer.apply`, отметки удаления |
| `backend/src/kitchen/sync/reader.py` | `_label` → публичная `sheet_label` |
| `backend/src/kitchen/sync/cycle.py` | новый: книги, `judge`, `explain`, `SyncCycle`, `reader_from` |
| `backend/src/kitchen/domain/recipe.py`, `domain/costs.py`, `db/recipes.py` | признак `removed`, замечание |
| `backend/src/kitchen/worker/run.py`, `worker/__main__.py` | новые: цикл воркера |
| `backend/src/kitchen/config.py` | `sync_interval_seconds`, `sync_stale_after_seconds` |
| `backend/src/kitchen/web/sync_status.py` | новый: `build_sync_status` и схемы ответа |
| `backend/src/kitchen/web/api.py` | `GET /api/sync`, скрытие удалённого, 410 |
| `backend/scripts/import_sheets.py` | один цикл с `force=True` |
| `infra/docker-compose.yml`, `scripts/deploy.sh` | воркер без профиля; смоук воркера |
| `backend/tests/fake_sheets.py` | новый: общие фальшивые таблицы |
| `backend/tests/integration/conftest.py` | новый: фикстура `sessions` |
| тесты: `unit/test_cycle.py`, `unit/test_worker.py`, `unit/test_sync_status.py`, `integration/test_cycle.py`, `integration/test_api.py` (новые); `integration/test_importer.py`, `integration/test_database.py`, `unit/test_costs.py`, `unit/test_infra.py` | |
| `frontend/src/api/types.ts`, `api/queries.ts`, `domain/vremya.ts`, `shell/Svezhest.tsx`, `shell/Layout.tsx`, `shell/shell.css`, `pages/DishDetail.tsx` | строка свежести, 410 |
| `frontend/tests/svezhest.test.tsx`, `tests/vremya.test.ts` (новые); `tests/obolochka.test.tsx`, `tests/vhod.test.tsx`, `tests/kartochka.test.tsx` | |
| `.claude/agents/domain-guard.md`, `docs/FRONTEND.md`, `docs/ROADMAP.md`, спека | документы |

---

## Задача 0: Подготовка ветки

**Files:** `docs/superpowers/plans/2026-09-23-sinhronizatsiya.md` (новый), спека (дописать).

- [ ] **Шаг 1.** На ветке `sinhronizatsiya` сохранить этот план в
  `docs/superpowers/plans/2026-09-23-sinhronizatsiya.md` (Write, без изменений).
- [ ] **Шаг 2.** Дописать в конец спеки раздел:

```markdown
## Уточнения при планировании (23.09.2026)

1. У каждой книги в ответе `/api/sync` свой признак `stale`. Полоса называет отставшие книги
   поимённо: «Данные не обновляются с 14:05 — карточки ингредиентов: <причина>»; книга без
   единой проверки — «Данные не синхронизировались — <книга>».
2. Названия книг — со строчной («таблица кухни», «карточки ингредиентов»): стоят посреди фразы.
3. Время в ответе — с часовым поясом, как его отдаёт база; по Москве его переводит экран.
4. Подбор пары для карточки не предлагает удалённый из справочника ингредиент.
5. `scripts/import_sheets.py` — это один цикл синхронизации с принудительным переносом.
```

- [ ] **Шаг 3.** Коммит «План синхронизации и уточнения к спеке», пуш, открыть **черновой** PR
  `sinhronizatsiya` → `main` через GitHub API (`"draft": true`), чтобы CI гонял интеграционные
  тесты с первого пуша.

---

## Задача 1: Схема — `removed_at` и `sync_state`

**Files:**
- Create: `backend/alembic/versions/20260923_1800_sinhronizatsiya.py`
- Modify: `backend/src/kitchen/db/models.py`
- Test: `backend/tests/integration/test_database.py`

**Interfaces — Produces:**
- `models.RemovedMixin` с `removed_at: Mapped[datetime | None]`; его наследуют `Ingredient`,
  `IngredientCard`, `Packaging`, `CookingMethod`, `Dish` (не `DishComponent`).
- `models.SyncState`: `book` (PK, `String(32)`), `checked_at`, `changed_at`, `fingerprint`
  (`String(64)`), `read_started_at`, `problem` (`Text`), `problem_since` — всё nullable,
  время `DateTime(timezone=True)`.

- [ ] **Шаг 1: падающий тест** — в `test_database.py` (импорт `inspect` из `sqlalchemy`):

```python
def test_sync_schema_after_upgrade(alembic_config: Config) -> None:
    """После upgrade head есть отметка удаления и состояние книг.

    Отдельно от обратимости: тот тест прошёл бы и с пустой ревизией.
    """
    command.upgrade(alembic_config, "head")
    inspector = inspect(create_engine(_url()))
    for table in ("ingredients", "ingredient_cards", "packaging", "cooking_methods", "dishes"):
        removed = {c["name"]: c for c in inspector.get_columns(table)}.get("removed_at")
        assert removed is not None, f"у {table} нет removed_at"
        assert removed["nullable"], "пусто — строка есть в листе"
    assert inspector.get_pk_constraint("sync_state")["constrained_columns"] == ["book"]
```

- [ ] **Шаг 2:** закоммитить тест, пуш — CI краснеет на `test_sync_schema_after_upgrade`
  (остальное зелёное).
- [ ] **Шаг 3: миграция** `20260923_1800_sinhronizatsiya.py`:

```python
"""Синхронизация: отметка удаления и состояние книг

Ревизия: см. ниже
Создана: 2026-09-23

Колонка `removed_at` у пяти таблиц, приезжающих из листов, и таблица
`sync_state` — строка на книгу (Google-таблицу).

Чек-лист (его же проверяет агент migration-guard):

  * **downgrade работает.** Колонки и таблица удаляются целиком.
  * **Данные не теряются.** Новые колонки пустые; при откате теряются только
    отметки удаления и состояние синхронизации — их восстановит первый же
    цикл воркера.
  * **Совместимо со старым кодом.** Старый код колонку и таблицу не читает;
    выкладка применяет миграцию раньше, чем перезапускает код.
  * **CREATE INDEX CONCURRENTLY не нужен.** Индексов нет: таблицы по полторы
    сотни строк, фильтр `removed_at is null` дешевле индекса.
  * **Внешних ключей нет.**
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7f3c2a9e5d41"
down_revision: str | None = "0d2ff156b9bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("ingredients", "ingredient_cards", "packaging", "cooking_methods", "dishes")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "sync_state",
        sa.Column("book", sa.String(length=32), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("read_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("problem", sa.Text(), nullable=True),
        sa.Column("problem_since", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("book"),
    )


def downgrade() -> None:
    op.drop_table("sync_state")
    for table in reversed(_TABLES):
        op.drop_column(table, "removed_at")
```

- [ ] **Шаг 4: модели** — в `models.py` после `SyncMixin`:

```python
class RemovedMixin:
    """Отметка «строки больше нет в листе».

    Удалённую шефом строку не стираем: скрываем с сайта и помним. Вернёт
    строку — отметка снимается, и все связи, включая подтверждённые на экране
    сверки, на месте (решение Александра 23.09.2026).
    """

    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

  Добавить `RemovedMixin` третьим предком: `class Ingredient(Base, SyncMixin, RemovedMixin)`,
  так же `IngredientCard`, `Packaging`, `CookingMethod`, `Dish`. После `SyncSheet`:

```python
class SyncState(Base):
    """Состояние синхронизации одной книги (Google-таблицы).

    Строка на книгу, а не на прогон: сайт спрашивает «насколько свежи данные»
    раз в минуту, и ответ должен быть одним чтением, а не разбором журнала.
    Журнал событий — `sync_runs`.
    """

    __tablename__ = "sync_state"

    book: Mapped[str] = mapped_column(String(32), primary_key=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    read_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    problem: Mapped[str | None] = mapped_column(Text)
    problem_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Шаг 5:** локальные проверки бэкенда зелёные; пуш — CI зелёный (новый тест,
  `test_migrations_are_reversible`, `test_alembic_has_single_head`).
- [ ] **Шаг 6:** коммит «Схема синхронизации: removed_at и sync_state».

---

## Задача 2: Импорт помечает удалённое и не пускает второй импорт

**Files:**
- Create: `backend/tests/fake_sheets.py`, `backend/tests/integration/conftest.py`
- Modify: `backend/src/kitchen/sync/importer.py`, `backend/tests/integration/test_importer.py`

**Interfaces:**
- Consumes: `models.RemovedMixin` (задача 1).
- Produces:
  - `importer.IMPORT_LOCK_KEY: int`, `importer.take_import_lock(session: Session) -> None`;
  - `Importer.apply(session: Session, sheets: Mapping[str, SheetData | str]) -> ImportResult` —
    перенос уже прочитанного в открытой транзакции; `Importer.run()` = чтение + `apply`;
  - `ImportResult.counts["ингредиенты"|"упаковка"|"способы приготовления"|"блюда"]` = число
    строк **в листе** (раньше — всех строк базы);
  - замечания `"<лист>: скрыто строк, которых больше нет в листе, — N"` и
    `"<лист>: вернулись в лист строки — N"`;
  - `tests/fake_sheets.py`: `IDS`, `header(spec)`, `row(spec, **values)`, `kitchen_sheets()`,
    `cards_sheet()`, `sheets_client(*, kitchen=None, cards=None, missing=())`;
    `tests/integration/conftest.py`: фикстура `sessions`.

- [ ] **Шаг 1: вынести общее из `test_importer.py`** (чистый перенос, поведение тестов то же).
  `backend/tests/fake_sheets.py`:

```python
"""Фальшивые таблицы для тестов импорта и синхронизации.

Одни и те же листы нужны офлайн-тестам цикла и интеграционным тестам
импорта; держать две копии значило бы однажды проверять разное.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kitchen.sync import specs
from tests.conftest import FakeSheetsClient, FakeSpreadsheet, FakeWorksheet

if TYPE_CHECKING:
    from kitchen.sync.ownership import SheetSpec

IDS = {"kitchen": "kitchen-id", "ingredient_cards": "cards-id"}


def header(spec: SheetSpec) -> list[str]:
    return [c.expected_header for c in spec.columns]


def row(spec: SheetSpec, **values: str) -> list[str]:
    cells = dict.fromkeys((c.field for c in spec.columns), "")
    cells.update(values)
    return [cells[c.field] for c in spec.columns]


def kitchen_sheets() -> dict[str, list[list[str]]]:
    """Книга кухни с разумным содержимым; каждый вызов — свежая копия."""
    return {
        "ING": [
            header(specs.INGREDIENTS),
            row(specs.INGREDIENTS, id="1", name="Томаты", price_per_kg="177", status="активное"),
            row(specs.INGREDIENTS, id="2", name="Сахар", price_per_kg="100", status="активное"),
            row(specs.INGREDIENTS, id="3", name="Сахар", price_per_kg="0", status="активное"),
        ],
        "Упаковка": [
            header(specs.PACKAGING),
            row(specs.PACKAGING, id="u1", name="Коробка", price_per_piece="12"),
        ],
        "Способы приготовления": [
            header(specs.COOKING_METHODS),
            row(specs.COOKING_METHODS, id="m1", method="Фритюр"),
        ],
        "Блюда": [
            header(specs.DISHES),
            row(specs.DISHES, id="B001", name="Ролл", price_menu="280", status="активное"),
        ],
        "ТТК": [
            header(specs.TTK),
            row(specs.TTK, dish_id="B001", ingredient_id="1", net_weight_g="100"),
            row(specs.TTK, dish_id="B001", packaging_id="u1", net_weight_g="0"),
        ],
    }


def cards_sheet() -> list[list[str]]:
    return [
        header(specs.INGREDIENT_CARDS),
        [""] * len(specs.INGREDIENT_CARDS.columns),
        row(specs.INGREDIENT_CARDS, name="Томаты", supplier="Поставщик"),
        row(specs.INGREDIENT_CARDS, name="Сахар"),
        row(specs.INGREDIENT_CARDS, name="Пастрами из индейки"),
    ]


def sheets_client(
    *,
    kitchen: dict[str, list[list[str]]] | None = None,
    cards: list[list[str]] | None = None,
    missing: tuple[str, ...] = (),
) -> FakeSheetsClient:
    """Фальшивые таблицы: `kitchen` заменяет листы кухни по имени, `cards` —
    лист карточек, `missing` — листы кухни, которых в таблице нет вовсе."""
    book = kitchen_sheets()
    book.update(kitchen or {})
    for title in missing:
        del book[title]
    return FakeSheetsClient(
        {
            "kitchen-id": FakeSpreadsheet({t: FakeWorksheet(v, t) for t, v in book.items()}),
            "cards-id": FakeSpreadsheet(
                {"Лист1": FakeWorksheet(cards if cards is not None else cards_sheet(), "Лист1")}
            ),
        }
    )
```

  `backend/tests/integration/conftest.py`:

```python
"""Общее для интеграционных тестов: база с чистой схемой на каждый тест."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config

from kitchen.db.session import make_session_factory
from tests.integration.test_database import BACKEND, _url

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def sessions() -> Iterator[sessionmaker[Session]]:
    url = _url()
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    # Чистая база на каждый тест: импорт идёт одной транзакцией, и остатки
    # прошлого прогона сделали бы результат зависящим от порядка тестов.
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield make_session_factory(url)
    command.downgrade(config, "base")
```

  В `test_importer.py` удалить `IDS`, `_header`, `_row`, `_client`, фикстуру `sessions` и
  импорты, ставшие лишними; добавить `from tests.fake_sheets import IDS, cards_sheet, header,
  kitchen_sheets, row, sheets_client`; заменить вызовы: `_client()` → `sheets_client()`,
  `_client(**{"ТТК": broken})` / `_client(**{"ТТК": rows})` → `sheets_client(kitchen={"ТТК": …})`,
  `_header(…)` → `header(…)`, `_row(…)` → `row(…)`; в `test_duplicate_card_names_are_reported`
  вместо подмены `client._spreadsheets["cards-id"]` — `sheets_client(cards=cards)`.
  Пуш — CI зелёный без единого изменения в утверждениях.

- [ ] **Шаг 2: падающие тесты** — в `test_importer.py` (импорт `threading`,
  `from sqlalchemy import func, select, text`). Строки листа убираются явными списками:
  `ing = kitchen_sheets()["ING"]` — это `[шапка, id=1 Томаты, id=2 Сахар, id=3 Сахар]`.

```python
def test_row_removed_from_sheet_is_hidden_not_deleted(sessions) -> None:
    """Решение 23.09: удалённое скрываем, но помним — не стираем."""
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()
    ing = kitchen_sheets()["ING"]
    without_sugar = [ing[0], ing[1], ing[3]]  # нет id=2

    result = Importer(SheetsReader(sheets_client(kitchen={"ING": without_sugar}), IDS), sessions).run()

    with sessions() as session:
        sugar = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "2"))
        tomato = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "1"))
    assert sugar is not None, "строка не стёрта"
    assert sugar.removed_at is not None, "а скрыта"
    assert tomato.removed_at is None
    assert result.counts["ингредиенты"] == 2, "считаем строки листа, а не базы"
    assert any(w.startswith("ING: скрыто") for w in result.warnings)


def test_row_returned_to_sheet_is_restored(sessions) -> None:
    ing = kitchen_sheets()["ING"]
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()
    Importer(SheetsReader(sheets_client(kitchen={"ING": [ing[0], ing[1], ing[3]]}), IDS), sessions).run()

    result = Importer(SheetsReader(sheets_client(), IDS), sessions).run()

    with sessions() as session:
        sugar = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "2"))
    assert sugar.removed_at is None
    assert any(w.startswith("ING: вернулись") for w in result.warnings)


def test_removed_card_keeps_confirmed_link(sessions) -> None:
    """Связь, подтверждённую на сверке, удаление и возврат карточки не стирают."""
    importer = Importer(SheetsReader(sheets_client(), IDS), sessions)
    importer.run()
    with sessions() as session, session.begin():
        sugar_id = session.scalar(select(models.Ingredient.id).where(models.Ingredient.legacy_id == "2"))
        card = session.scalar(select(models.IngredientCard).where(models.IngredientCard.name == "Сахар"))
        card.ingredient_id = sugar_id
        card.link_status = "linked"
        card.link_confirmed_at = text("now()")
    without_sugar = [line for line in cards_sheet() if "Сахар" not in line]

    Importer(SheetsReader(sheets_client(cards=without_sugar), IDS), sessions).run()
    with sessions() as session:
        card = session.scalar(select(models.IngredientCard).where(models.IngredientCard.name == "Сахар"))
        assert card.removed_at is not None
        assert card.ingredient_id == sugar_id, "связь не тронута"

    importer.run()
    with sessions() as session:
        card = session.scalar(select(models.IngredientCard).where(models.IngredientCard.name == "Сахар"))
        assert card.removed_at is None
        assert (card.link_status, card.ingredient_id) == ("linked", sugar_id)


def test_unread_sheet_hides_nothing(sessions) -> None:
    """Лист не прочитался — это не «все строки удалены»."""
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()

    Importer(SheetsReader(sheets_client(missing=("ING",)), IDS), sessions).run()

    with sessions() as session:
        hidden = session.scalar(
            select(func.count()).select_from(models.Ingredient).where(models.Ingredient.removed_at.is_not(None))
        )
    assert hidden == 0


def test_card_is_not_linked_to_removed_ingredient(sessions) -> None:
    """Удалённое из справочника не предлагаем в пару карточке."""
    Importer(SheetsReader(sheets_client(), IDS), sessions).run()
    ing = kitchen_sheets()["ING"]

    Importer(SheetsReader(sheets_client(kitchen={"ING": [ing[0], ing[2], ing[3]]}), IDS), sessions).run()

    with sessions() as session:
        card = session.scalar(select(models.IngredientCard).where(models.IngredientCard.name == "Томаты"))
    assert card.link_status != "linked"
    assert card.ingredient_id is None


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
        except Exception as error:  # noqa: BLE001 — ошибку потока показываем в утверждении
            errors.append(error)

    first = sessions()
    first.begin()
    importer.apply(first, sheets)
    thread = threading.Thread(target=second)
    thread.start()
    thread.join(timeout=1.0)  # второй успевает дойти до места, где ждёт
    first.commit()
    first.close()
    thread.join(timeout=30)

    assert not errors, errors
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(models.DishComponent)) == 2
```

  Пуш — CI краснеет ровно на пяти новых тестах (`removed_at` не ставится, пары с удалённым
  подбираются, `apply` нет). `test_unread_sheet_hides_nothing` — охранный, зелёный с самого
  начала; после реализации проверить подменой: вызов `_mark_presence` до раннего `return` при
  `data is None` обязан его покраснить.

- [ ] **Шаг 3: реализация** в `importer.py`. Импорты: `from sqlalchemy import select, text`;
  под `TYPE_CHECKING` добавить `Mapping` в `from collections.abc import Iterable, Mapping, Sequence`.

```python
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
```

  `Importer.run` и новый `apply` (тело прежнего `run` переезжает в `apply`):

```python
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
        run.note = "; ".join(result.warnings[:20])
        session.flush()
        result.run_id = run.id
        return result
```

  `_import_all(self, session, sheets, result, now: datetime)` передаёт `now` в каждый
  `_import_*`. В `_import_ingredients`, `_import_packaging`, `_import_methods`,
  `_import_dishes` — одинаковая правка: перед циклом `present: set[str] = set()`, в цикле после
  `key = _text(row["id"])` — `present.add(key)`, после цикла (до `session.flush()`) —
  `_mark_presence(existing, present, now, result, "<имя листа>")` с именами `"ING"`,
  `"Упаковка"`, `"Способы приготовления"`, `"Блюда"`; `result.add(…, len(existing))` →
  `result.add(…, len(present))`. В `_import_cards`: индекс строится только из неудалённых —

```python
        index = NameIndex(
            Entry(key=str(item.id), name=item.name, status=item.status)
            for item in session.scalars(
                select(models.Ingredient).where(models.Ingredient.removed_at.is_(None))
            ).all()
        )
```

  и после цикла по строкам, до `session.flush()`, — `_mark_presence(existing, seen, now, result,
  "Карточки")`. Новая функция модуля:

```python
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
    в замечания прогона: массовое исчезновение должно быть видно в журнале.
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
        result.warnings.append(f"{sheet}: скрыто строк, которых больше нет в листе, — {hidden}")
    if restored:
        result.warnings.append(f"{sheet}: вернулись в лист строки — {restored}")
```

  Обновить docstring модуля: абзац «Что импорт не делает» дополнить «…и не стирает строки,
  исчезнувшие из листа, — скрывает их отметкой `removed_at`».

- [ ] **Шаг 4:** локальные проверки (mypy strict на `kitchen.sync`); пуш — CI зелёный.
  Подменой убедиться, что `test_parallel_imports_do_not_duplicate_ttk` краснеет без
  `take_import_lock(session)` в `apply` (закомментировать строку, пуш, вернуть).
- [ ] **Шаг 5:** коммит «Импорт скрывает удалённое из листа и держит очередь».

---

## Задача 3: Расчёт — удалённый ингредиент и удалённая упаковка

**Files:**
- Modify: `backend/src/kitchen/domain/recipe.py`, `backend/src/kitchen/domain/costs.py`,
  `backend/src/kitchen/db/recipes.py`, `.claude/agents/domain-guard.md`
- Test: `backend/tests/unit/test_costs.py`, `backend/tests/integration/test_importer.py`

**Interfaces — Produces:** `IngredientSpec.removed: bool = False`,
`PackagingSpec.removed: bool = False` (последними полями); замечания
`«Ингредиент «X» удалён из справочника — посчитан по последним известным данным, поправьте ТТК»`
и `«Упаковка «X» удалена из справочника — посчитана по последним известным данным, поправьте ТТК»`.

- [ ] **Шаг 1: падающие офлайн-тесты** — в `test_costs.py` новый раздел после упаковки:

```python
# ---------------------------------------------------------------------------
# Удалено из справочника, а ТТК ссылается
# ---------------------------------------------------------------------------
REMOVED = "удалён из справочника"


def test_removed_ingredient_counts_by_last_known_data_and_warns() -> None:
    """Шеф удалил строку из ING, а ТТК на неё ещё ссылается.

    Решение 23.09: считать по последним известным данным — себестоимость не
    проваливается — и сказать об этом, чтобы шеф поправил ТТК.
    """
    result = calculate(dish(main(ing(removed=True), "100")))

    assert result.uc_rub == Decimal("17.70"), "100 г × 177 ₽/кг — как у неудалённого"
    assert any(f"«Томаты» {REMOVED}" in w for w in result.warnings)


def test_present_ingredient_gets_no_removed_warning() -> None:
    result = calculate(dish(main(ing(), "100")))

    assert not any(REMOVED in w for w in result.warnings)


def test_removed_packaging_counts_and_warns() -> None:
    box = PackagingSpec(key="u1", name="Коробка", price_per_piece=Decimal("12.50"), removed=True)
    result = calculate(
        dish(
            main(ing(), "100"),
            Component(row_type=ROW_PACKAGING, net_weight_g=Decimal("2"), packaging=box),
        )
    )

    assert result.uc_rub == Decimal("42.70"), "17.70 за томаты + 25.00 за две коробки"
    assert any("«Коробка» удалена из справочника" in w for w in result.warnings)
```

  Запуск: `cd backend && VIRTUAL_ENV= uv run pytest tests/unit/test_costs.py -q` — падают
  первый и третий (нет поля `removed`); второй зелёный — он охранный, проверяется подменой в
  шаге 4.

- [ ] **Шаг 2: падающий интеграционный тест** — в `test_importer.py` (импорты
  `from decimal import Decimal`, `from kitchen.db.recipes import load_recipes`,
  `from kitchen.domain.costs import calculate`):

```python
def test_removed_ingredient_still_counted_in_dish(sessions) -> None:
    """Сквозь базу: ТТК ссылается на удалённый ингредиент — строка состава на
    месте, счёт по последним данным, замечание есть."""
    ing = [
        header(specs.INGREDIENTS),
        row(specs.INGREDIENTS, id="1", name="Томаты", unit="кг", price_per_kg="177", status="активное"),
        row(specs.INGREDIENTS, id="2", name="Сахар", unit="кг", price_per_kg="100", status="активное"),
    ]
    Importer(SheetsReader(sheets_client(kitchen={"ING": ing}), IDS), sessions).run()
    Importer(SheetsReader(sheets_client(kitchen={"ING": [ing[0], ing[2]]}), IDS), sessions).run()

    with sessions() as session:
        [recipe] = load_recipes(session)
    cost = calculate(recipe)

    assert cost.uc_rub == Decimal("17.70")
    assert any("«Томаты» удалён из справочника" in w for w in cost.warnings)
```

  Пуш — CI краснеет на этом тесте (замечания нет: `load_recipes` не передаёт признак).

- [ ] **Шаг 3: реализация.** `recipe.py` — в конец `IngredientSpec` и `PackagingSpec`:

```python
    removed: bool = False
    """Строки больше нет в листе, а ТТК на неё ссылается. Считается по
    последним известным данным и с замечанием (решение 23.09.2026)."""
```

  `costs.py` — в `add_main` после проверки `ingredient is None`:

```python
        if ingredient.removed:
            self.warnings.append(
                f"Ингредиент «{ingredient.name}» удалён из справочника — посчитан по "
                f"последним известным данным, поправьте ТТК"
            )
```

  в `add_packaging` после проверки `packaging is None`:

```python
        if packaging.removed:
            self.warnings.append(
                f"Упаковка «{packaging.name}» удалена из справочника — посчитана по "
                f"последним известным данным, поправьте ТТК"
            )
```

  и строку в список правил docstring модуля: «* Ингредиент или упаковка удалены из
  справочника, а ТТК на них ссылается, — счёт по последним известным данным и
  предупреждение (решение 23.09.2026).» `db/recipes.py`: в `_ingredient` и `_packaging`
  добавить `removed=row.removed_at is not None`.

- [ ] **Шаг 4:** офлайн-тесты зелёные; подмена — замечание «всегда» (убрать `if
  ingredient.removed:`) краснит `test_present_ingredient_gets_no_removed_warning`; вернуть.
  Пуш — CI зелёный.
- [ ] **Шаг 5: инвариант** — в `domain-guard.md` после правила 9 новое правило 10, прежние
  10–15 → 11–16:

```markdown
10. **Ингредиент или упаковка удалены из справочника, а ТТК на них ссылается →
    счёт по последним известным данным и предупреждение.** Себестоимость не
    проваливается в ноль, шеф видит «удалён из справочника — поправьте ТТК»
    (решение Александра 23.09.2026). Удалённое скрывается с сайта, но из базы
    не стирается — отметка `removed_at`.
```

- [ ] **Шаг 6:** коммит «Расчёт: удалённое из справочника считается по последним данным».

---

## Задача 4: Цикл — решения по книгам (офлайн)

**Files:**
- Create: `backend/src/kitchen/sync/cycle.py` (первая часть), `backend/tests/unit/test_cycle.py`
- Modify: `backend/src/kitchen/sync/reader.py` (`_label` → `sheet_label`)

**Interfaces — Produces:**
- `reader.sheet_label(spec: SheetSpec) -> str` — `"<книга>/<лист>"` (бывшая `_label`);
- `cycle.BOOKS: dict[str, tuple[SheetSpec, ...]]`, `cycle.BOOK_TITLES: dict[str, str]`;
- `cycle.Verdict(problem: str | None, fingerprint: str | None)`;
- `cycle.judge(book: str, sheets: Mapping[str, SheetData | str]) -> Verdict`;
- `cycle.explain(title: str, error: str) -> str`.

- [ ] **Шаг 1: переименование.** В `reader.py` `def _label` → `def sheet_label` и все вызовы
  (`grep -rn "_label(" backend/src backend/tests`). Тесты зелёные.
- [ ] **Шаг 2: падающие тесты** `backend/tests/unit/test_cycle.py`:

```python
"""Решения цикла синхронизации по книгам — без базы и без сети."""

from __future__ import annotations

import pytest

from kitchen.sync import specs
from kitchen.sync.cycle import BOOKS, explain, judge
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetData, SheetsReader
from tests.conftest import FakeSheetsClient
from tests.fake_sheets import IDS, kitchen_sheets, row, sheets_client


def _read(client: FakeSheetsClient) -> dict[str, SheetData | str]:
    return SheetsReader(client, IDS).read_many(Importer.SPECS)


def test_books_cover_every_imported_sheet() -> None:
    """Лист, которого нет ни в одной книге, воркер не перенёс бы никогда."""
    in_books = sorted(spec.title for book in BOOKS.values() for spec in book)
    assert in_books == sorted(spec.title for spec in Importer.SPECS)


def test_readable_book_gets_fingerprint() -> None:
    verdict = judge("kitchen", _read(sheets_client()))

    assert verdict.problem is None
    assert verdict.fingerprint is not None and len(verdict.fingerprint) == 64


def test_fingerprint_follows_content() -> None:
    ing = kitchen_sheets()["ING"]
    ing[1] = row(specs.INGREDIENTS, id="1", name="Томаты", price_per_kg="170", status="активное")

    first = judge("kitchen", _read(sheets_client())).fingerprint
    again = judge("kitchen", _read(sheets_client())).fingerprint
    changed = judge("kitchen", _read(sheets_client(kitchen={"ING": ing}))).fingerprint

    assert first == again
    assert changed != first


def test_unread_column_does_not_wake_import() -> None:
    """Заметка в колонке, которую мы не импортируем, — не повод переносить книгу."""
    ing = kitchen_sheets()["ING"]
    ing[1] = [*ing[1], "заметка на полях"]

    assert (
        judge("kitchen", _read(sheets_client(kitchen={"ING": ing}))).fingerprint
        == judge("kitchen", _read(sheets_client())).fingerprint
    )


def test_shifted_columns_block_the_book() -> None:
    ing = kitchen_sheets()["ING"]
    ing[0] = list(ing[0])
    ing[0][1] = "Совсем другая колонка"

    verdict = judge("kitchen", _read(sheets_client(kitchen={"ING": ing})))

    assert verdict.fingerprint is None
    assert verdict.problem is not None
    assert "в листе «ING» сдвинулись колонки" in verdict.problem
    assert "Совсем другая колонка" in verdict.problem


def test_empty_sheet_is_named() -> None:
    verdict = judge("kitchen", _read(sheets_client(kitchen={"ТТК": []})))

    assert verdict.problem == "лист «ТТК» пуст"


def test_missing_sheet_blocks_the_book() -> None:
    verdict = judge("kitchen", _read(sheets_client(missing=("ТТК",))))

    assert verdict.problem is not None
    assert "листа «ТТК» нет в таблице" in verdict.problem


class QuotaSpreadsheet:
    """Таблица, на которую Google ответил отказом по квоте."""

    def worksheets(self) -> list[object]:
        raise RuntimeError("APIError: [429]: Quota exceeded for quota metric 'Read requests'")


def test_book_failure_is_explained_once_and_spares_other_book() -> None:
    """Отказ открытия приходит на каждый лист книги — причину говорим один раз.
    Карточки при этом читаются: книги независимы."""
    client = sheets_client()
    client._spreadsheets["kitchen-id"] = QuotaSpreadsheet()
    sheets = _read(client)

    kitchen = judge("kitchen", sheets)
    assert kitchen.problem == "Google временно ограничил число запросов — следующая попытка через 5 минут"
    assert judge("ingredient_cards", sheets).problem is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("APIError: [403]: The caller does not have permission", "доступ платформы к таблице закрыт"),
        ("ReadTimeout: HTTPSConnectionPool(host='sheets.googleapis.com'): Read timed out", "Google не ответил"),
        ("листа «ТТК» нет в таблице", "листа «ТТК» нет в таблице"),
        ("что-то совсем новое", "не удалось прочитать лист «ING»: что-то совсем новое"),
    ],
)
def test_explain_speaks_plainly(error: str, expected: str) -> None:
    """Причину видят все, в том числе шеф: код исключения — не объяснение."""
    assert expected in explain("ING", error)
```

  Запуск: `VIRTUAL_ENV= uv run pytest tests/unit/test_cycle.py -q` — падает импорт `cycle`.

- [ ] **Шаг 3: реализация** — `backend/src/kitchen/sync/cycle.py` (первая часть; остальное —
  в задаче 5):

```python
"""Цикл синхронизации «лист → база».

Воркер запускает его раз в пять минут, `scripts/import_sheets.py` — вручную.
Логика одна, чтобы ручной перенос не обходил правил автоматического
(спека docs/superpowers/specs/2026-09-23-sinhronizatsiya-design.md):

* **Книга переносится целиком или не переносится.** Лист не прочитан или у
  него сдвинулись колонки — книга в этом цикле не трогается, старые данные
  остаются, причина уходит на сайт. Колонки читаются по позиции: сдвиг дал бы
  цену из колонки веса — тихо и каждые пять минут.
* **Только изменения.** Отпечаток книги совпал с перенесённым — в базу
  пишется одна отметка «проверено».
* **Сбой в журнал — один раз**, когда причина появилась или сменилась.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kitchen.sync import specs
from kitchen.sync.reader import sheet_label

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from kitchen.sync.ownership import SheetSpec
    from kitchen.sync.reader import SheetData

BOOKS: dict[str, tuple[SheetSpec, ...]] = {
    "kitchen": (specs.INGREDIENTS, specs.PACKAGING, specs.COOKING_METHODS, specs.DISHES, specs.TTK),
    "ingredient_cards": (specs.INGREDIENT_CARDS,),
}
"""Книги (Google-таблицы) и их листы. Листы кухни ссылаются друг на друга и
переносятся только вместе; карточки от кухни не зависят."""

BOOK_TITLES: dict[str, str] = {
    "kitchen": "таблица кухни",
    "ingredient_cards": "карточки ингредиентов",
}
"""Как книга называется на сайте. Со строчной: стоит посреди фразы."""


@dataclass(frozen=True, slots=True)
class Verdict:
    """Что показало чтение книги: причина не переносить — или отпечаток."""

    problem: str | None
    fingerprint: str | None


def explain(title: str, error: str) -> str:
    """Причина сбоя чтения словами, которые поймёт шеф.

    Текст исключения gspread написан для разработчика, а полосу на сайте видят
    все (решение Александра 23.09). Известные случаи переводим, остальное —
    как есть, но с именем листа.
    """
    lowered = error.lower()
    if "[429]" in error or "quota" in lowered:
        return "Google временно ограничил число запросов — следующая попытка через 5 минут"
    if "[403]" in error or "permission" in lowered:
        return (
            "доступ платформы к таблице закрыт — проверьте, что сервисному "
            "аккаунту открыт доступ"
        )
    if any(word in lowered for word in ("timeout", "timed out", "connection", "max retries")):
        return "Google не ответил — следующая попытка через 5 минут"
    if "нет в таблице" in error or "не задан идентификатор" in error:
        return error
    return f"не удалось прочитать лист «{title}»: {error[:200]}"


def _header_problem(title: str, issues: Sequence[str]) -> str:
    if tuple(issues) == ("лист пуст",):
        return f"лист «{title}» пуст"
    if all(issue.startswith("колонка ") for issue in issues):
        return f"в листе «{title}» сдвинулись колонки — " + "; ".join(issues)
    return f"лист «{title}»: " + "; ".join(issues)


def judge(book: str, sheets: Mapping[str, SheetData | str]) -> Verdict:
    """Можно ли переносить книгу и что в ней.

    Отпечаток — SHA-256 от «лист | номер строки | хеш строки» по всем листам в
    порядке описаний. Хеш строки считает читатель ровно по импортируемым
    колонкам, поэтому заметка на полях перенос не будит.
    """
    problems: list[str] = []
    lines: list[str] = []
    for spec in BOOKS[book]:
        data = sheets.get(sheet_label(spec))
        if data is None:
            problems.append(f"лист «{spec.title}» не прочитан")
        elif isinstance(data, str):
            problems.append(explain(spec.title, data))
        elif data.header_issues:
            problems.append(_header_problem(data.title, data.header_issues))
        else:
            lines.extend(f"{spec.title}|{row.number}|{row.content_hash}" for row in data.rows)
    if problems:
        # Отказ открытия таблицы приходит одинаковым на каждый её лист —
        # повторять одну причину пять раз незачем.
        return Verdict(problem="; ".join(dict.fromkeys(problems)), fingerprint=None)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return Verdict(problem=None, fingerprint=digest)
```

- [ ] **Шаг 4:** тесты зелёные, mypy strict и ruff чисты. Подмена: убрать `dict.fromkeys` —
  краснеет тест про квоту. В отпечатке — `spec.title`, а не `data.title`: запасное имя листа
  («Впитывание масла») не должно менять отпечаток.
- [ ] **Шаг 5:** коммит «Цикл синхронизации: решения по книгам».

---

## Задача 5: Цикл — транзакция, состояние книг, журнал

**Files:**
- Modify: `backend/src/kitchen/sync/cycle.py`
- Create: `backend/tests/integration/test_cycle.py`

**Interfaces:**
- Consumes: `judge`, `BOOKS` (задача 4); `Importer.apply`, `take_import_lock` (задача 2);
  `models.SyncState` (задача 1).
- Produces:
  - `cycle.BookOutcome(action: Literal["imported", "unchanged", "failed", "stale"], problem: str | None = None)`;
  - `cycle.CycleResult(outcomes: dict[str, BookOutcome], imported: ImportResult | None)`;
  - `cycle.SyncCycle(reader: SheetsReader, sessions: sessionmaker[Session], clock: Callable[[], datetime] = _utcnow)`,
    метод `run(*, force: bool = False) -> CycleResult`;
  - `cycle.reader_from(settings: Settings) -> SheetsReader`.

- [ ] **Шаг 1: падающие тесты** `backend/tests/integration/test_cycle.py`:

```python
"""Цикл синхронизации на настоящем Postgres, без сети."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from kitchen.db import models
from kitchen.sync import specs
from kitchen.sync.cycle import SyncCycle
from kitchen.sync.reader import SheetsReader
from tests.fake_sheets import IDS, cards_sheet, kitchen_sheets, row, sheets_client

pytestmark = pytest.mark.integration


class Clock:
    """Часы, которые идут только по команде."""

    def __init__(self) -> None:
        self.now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def tick(self, minutes: int = 5) -> None:
        self.now += timedelta(minutes=minutes)


def _cycle(sessions, clock, **sheets) -> SyncCycle:
    return SyncCycle(SheetsReader(sheets_client(**sheets), IDS), sessions, clock)


def _state(sessions, book: str) -> models.SyncState:
    with sessions() as session:
        return session.get(models.SyncState, book)


def _runs(sessions) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(models.SyncRun))


def _tomato_price(sessions) -> str:
    with sessions() as session:
        tomato = session.scalar(select(models.Ingredient).where(models.Ingredient.legacy_id == "1"))
        return str(tomato.price_per_kg)


def _cheaper_tomato() -> list[list[str]]:
    ing = kitchen_sheets()["ING"]
    ing[1] = row(specs.INGREDIENTS, id="1", name="Томаты", price_per_kg="170", status="активное")
    return ing


def _shifted(ing: list[list[str]]) -> list[list[str]]:
    ing[0] = list(ing[0])
    ing[0][1] = "Совсем другая колонка"
    return ing


def test_first_cycle_imports_both_books(sessions) -> None:
    clock = Clock()

    result = _cycle(sessions, clock).run()

    assert {b: o.action for b, o in result.outcomes.items()} == {
        "kitchen": "imported",
        "ingredient_cards": "imported",
    }
    kitchen = _state(sessions, "kitchen")
    assert (kitchen.checked_at, kitchen.changed_at) == (clock.now, clock.now)
    assert kitchen.fingerprint is not None and len(kitchen.fingerprint) == 64
    assert kitchen.problem is None
    assert _runs(sessions) == 1, "перенос — событие журнала"


def test_unchanged_sheets_write_only_checked_at(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    result = _cycle(sessions, clock).run()

    assert {o.action for o in result.outcomes.values()} == {"unchanged"}
    kitchen = _state(sessions, "kitchen")
    assert kitchen.checked_at == clock.now
    assert kitchen.changed_at == clock.now - timedelta(minutes=5)
    assert _runs(sessions) == 1, "«ничего не изменилось» — не событие"


def test_changed_cell_is_imported(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    result = _cycle(sessions, clock, kitchen={"ING": _cheaper_tomato()}).run()

    assert result.outcomes["kitchen"].action == "imported"
    assert result.outcomes["ingredient_cards"].action == "unchanged"
    assert _state(sessions, "kitchen").changed_at == clock.now
    assert _tomato_price(sessions) == "170.00"


def test_shifted_columns_block_book_but_not_the_other(sessions) -> None:
    """Сдвиг колонок в кухне не мешает перенести карточки: книги независимы."""
    clock = Clock()
    _cycle(sessions, clock).run()
    checked = clock.now
    clock.tick()
    cards = cards_sheet()
    cards[2] = row(specs.INGREDIENT_CARDS, name="Томаты", supplier="Новый поставщик")

    result = _cycle(sessions, clock, kitchen={"ING": _shifted(_cheaper_tomato())}, cards=cards).run()

    kitchen = result.outcomes["kitchen"]
    assert kitchen.action == "failed"
    assert "в листе «ING» сдвинулись колонки" in kitchen.problem
    assert result.outcomes["ingredient_cards"].action == "imported"
    state = _state(sessions, "kitchen")
    assert state.checked_at == checked, "непроверенное не считается свежим"
    assert state.problem_since == clock.now
    assert _tomato_price(sessions) == "177.00", "книга со сдвигом не переносится"


def test_same_problem_is_journaled_once(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()
    _cycle(sessions, clock, kitchen={"ING": _shifted(kitchen_sheets()["ING"])}).run()
    since = clock.now
    clock.tick()

    _cycle(sessions, clock, kitchen={"ING": _shifted(kitchen_sheets()["ING"])}).run()

    assert _runs(sessions) == 2, "перенос и один сбой; повтор той же причины — не событие"
    assert _state(sessions, "kitchen").problem_since == since


def test_new_problem_is_a_new_event(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()
    _cycle(sessions, clock, kitchen={"ING": _shifted(kitchen_sheets()["ING"])}).run()
    clock.tick()

    result = _cycle(sessions, clock, missing=("ТТК",)).run()

    assert "листа «ТТК» нет в таблице" in result.outcomes["kitchen"].problem
    assert _runs(sessions) == 3
    assert _state(sessions, "kitchen").problem_since == clock.now


def test_recovery_clears_problem(sessions) -> None:
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()
    _cycle(sessions, clock, kitchen={"ING": _shifted(kitchen_sheets()["ING"])}).run()
    clock.tick()

    result = _cycle(sessions, clock).run()

    assert result.outcomes["kitchen"].action == "unchanged"
    state = _state(sessions, "kitchen")
    assert (state.problem, state.problem_since) == (None, None)
    assert state.checked_at == clock.now


def test_older_read_does_not_overwrite_newer(sessions) -> None:
    """Пока мы читали, другой импорт перенёс чтение новее нашего."""
    clock = Clock()
    _cycle(sessions, clock).run()
    with sessions() as session, session.begin():
        session.get(models.SyncState, "kitchen").read_started_at = clock.now + timedelta(minutes=1)

    result = _cycle(sessions, clock, kitchen={"ING": _cheaper_tomato()}).run()

    assert result.outcomes["kitchen"].action == "stale"
    assert _tomato_price(sessions) == "177.00"


def test_force_imports_even_unchanged(sessions) -> None:
    """Ручной импорт переносит всегда, но время изменения двигает только изменение."""
    clock = Clock()
    _cycle(sessions, clock).run()
    clock.tick()

    result = _cycle(sessions, clock).run(force=True)

    assert {o.action for o in result.outcomes.values()} == {"imported"}
    assert _runs(sessions) == 2
    assert _state(sessions, "kitchen").changed_at == clock.now - timedelta(minutes=5)
```

  Пуш — CI краснеет на этих тестах (`SyncCycle` нет).

- [ ] **Шаг 2: реализация** — дописать в `cycle.py`. Импорты модуля становятся такими:

```python
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from kitchen.db import models
from kitchen.sync import specs
from kitchen.sync.client import GspreadClient
from kitchen.sync.importer import Importer, ImportResult, take_import_lock
from kitchen.sync.reader import SheetsReader, sheet_label

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sqlalchemy.orm import Session, sessionmaker

    from kitchen.config import Settings
    from kitchen.sync.ownership import SheetSpec
    from kitchen.sync.reader import SheetData
```

  После `judge`:

```python
Action = Literal["imported", "unchanged", "failed", "stale"]


@dataclass(frozen=True, slots=True)
class BookOutcome:
    """Чем кончился цикл для книги."""

    action: Action
    problem: str | None = None


@dataclass(slots=True)
class CycleResult:
    outcomes: dict[str, BookOutcome] = field(default_factory=dict)
    imported: ImportResult | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reader_from(settings: Settings) -> SheetsReader:
    """Читатель боевых таблиц: воркер и ручной импорт собирают его одинаково."""
    return SheetsReader(
        GspreadClient(
            settings.google_credentials_path,
            timeout=settings.google_timeout,
            refresh_timeout=settings.google_refresh_timeout,
        ),
        {
            "kitchen": settings.sheets_id_kitchen,
            "competitors": settings.sheets_id_competitors,
            "ingredient_cards": settings.sheets_id_ingredient_cards,
            "tastings": settings.sheets_id_tastings,
        },
    )


def _mark_checked(state: models.SyncState, now: datetime) -> None:
    state.checked_at = now
    state.problem = None
    state.problem_since = None


def _record_failure(
    session: Session, state: models.SyncState, problem: str, now: datetime
) -> None:
    """Причина держится в состоянии книги; в журнал — только новая."""
    if state.problem == problem:
        return
    state.problem = problem
    state.problem_since = now
    session.add(models.SyncRun(finished_at=now, ok=False, note=f"{state.book}: {problem}"))


class SyncCycle:
    """Один цикл: прочитать книги, решить по каждой, перенести нужное."""

    def __init__(
        self,
        reader: SheetsReader,
        sessions: sessionmaker[Session],
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._reader = reader
        self._sessions = sessions
        self._clock = clock

    def run(self, *, force: bool = False) -> CycleResult:
        """`force` — переносить и без изменений (ручной запуск)."""
        read_started_at = self._clock()
        sheets = self._reader.read_many(Importer.SPECS)
        verdicts = {book: judge(book, sheets) for book in BOOKS}
        result = CycleResult()

        with self._sessions() as session, session.begin():
            take_import_lock(session)
            now = self._clock()
            states = {state.book: state for state in session.scalars(select(models.SyncState))}
            to_import: list[str] = []

            for book, verdict in verdicts.items():
                state = states.get(book)
                if state is None:
                    state = models.SyncState(book=book)
                    session.add(state)
                    states[book] = state

                if verdict.problem is not None:
                    _record_failure(session, state, verdict.problem, now)
                    result.outcomes[book] = BookOutcome("failed", verdict.problem)
                elif state.read_started_at is not None and state.read_started_at > read_started_at:
                    # Пока мы читали, другой импорт перенёс более свежее чтение:
                    # наше старше, перезаписывать им нельзя.
                    result.outcomes[book] = BookOutcome("stale")
                elif force or state.fingerprint != verdict.fingerprint:
                    to_import.append(book)
                else:
                    _mark_checked(state, now)
                    result.outcomes[book] = BookOutcome("unchanged")

            if to_import:
                wanted = {sheet_label(spec) for book in to_import for spec in BOOKS[book]}
                result.imported = Importer(self._reader, self._sessions).apply(
                    session, {label: data for label, data in sheets.items() if label in wanted}
                )
                for book in to_import:
                    state = states[book]
                    fingerprint = verdicts[book].fingerprint
                    if state.fingerprint != fingerprint:
                        state.changed_at = now
                    state.fingerprint = fingerprint
                    state.read_started_at = read_started_at
                    _mark_checked(state, now)
                    result.outcomes[book] = BookOutcome("imported")

        return result
```

- [ ] **Шаг 3:** локальные проверки (mypy strict: `session.scalars(...)` даёт `SyncState`,
  `Literal` в `BookOutcome`); пуш — CI зелёный, порог покрытия `kitchen.sync` ≥ 85 % держится.
- [ ] **Шаг 4:** коммит «Цикл синхронизации: состояние книг и журнал событий».

---

## Задача 6: Воркер и ручной импорт

**Files:**
- Create: `backend/src/kitchen/worker/run.py`, `backend/src/kitchen/worker/__main__.py`,
  `backend/tests/unit/test_worker.py`
- Modify: `backend/src/kitchen/config.py`, `backend/scripts/import_sheets.py`, `.env.example`

**Interfaces:**
- Consumes: `SyncCycle`, `reader_from`, `BookOutcome`, `CycleResult`, `BOOKS`, `BOOK_TITLES`.
- Produces: `Settings.sync_interval_seconds: int = 300`,
  `Settings.sync_stale_after_seconds: int = 900`;
  `run.run_periodically(job: Callable[[], None], interval: float, stop: threading.Event, clock: Callable[[], float] = time.monotonic) -> None`;
  `run.ChangeLog(logger: logging.Logger = log)` с `report(result: CycleResult) -> None`;
  `run.main() -> int`.

- [ ] **Шаг 1: падающие тесты** `backend/tests/unit/test_worker.py`:

```python
"""Цикл воркера: расписание, остановка и журнал — без базы и без сети."""

from __future__ import annotations

import logging
import threading

import pytest

from kitchen.sync.cycle import BookOutcome, CycleResult
from kitchen.worker.run import ChangeLog, run_periodically


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class StopAfter(threading.Event):
    """Остановка, которая запоминает паузы и срабатывает после n-й."""

    def __init__(self, waits: int) -> None:
        super().__init__()
        self.pauses: list[float | None] = []
        self._waits = waits

    def wait(self, timeout: float | None = None) -> bool:
        self.pauses.append(timeout)
        if len(self.pauses) >= self._waits:
            self.set()
        return self.is_set()


def test_pause_counts_from_cycle_start() -> None:
    """Цикл шёл 40 с — до следующего 260 с, а не 300: расписание не уплывает."""
    clock = FakeClock()
    stop = StopAfter(waits=1)

    def job() -> None:
        clock.now += 40

    run_periodically(job, 300, stop, clock)

    assert stop.pauses == [260]


def test_overlong_cycle_starts_next_at_once() -> None:
    clock = FakeClock()
    stop = StopAfter(waits=1)

    def job() -> None:
        clock.now += 400

    run_periodically(job, 300, stop, clock)

    assert stop.pauses == [0.0]


def test_stop_lets_current_cycle_finish() -> None:
    """SIGTERM посреди цикла: цикл доделывается, нового не начинается."""
    stop = threading.Event()
    finished: list[int] = []

    def job() -> None:
        stop.set()  # сигнал пришёл посреди переноса
        finished.append(1)

    run_periodically(job, 300, stop, FakeClock())

    assert finished == [1]


def test_failed_cycle_does_not_stop_worker(caplog: pytest.LogCaptureFixture) -> None:
    stop = StopAfter(waits=2)
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("база недоступна")

    with caplog.at_level(logging.ERROR, logger="kitchen.worker"):
        run_periodically(job, 300, stop, FakeClock())

    assert len(calls) == 2
    assert "база недоступна" in caplog.text


def _result(**outcomes: BookOutcome) -> CycleResult:
    return CycleResult(outcomes=dict(outcomes))


def test_changelog_writes_only_changes(caplog: pytest.LogCaptureFixture) -> None:
    """Одно и то же каждые пять минут в журнале прячет настоящие события."""
    logger = logging.getLogger("kitchen.worker.test")
    log = ChangeLog(logger)

    with caplog.at_level(logging.INFO, logger="kitchen.worker.test"):
        log.report(_result(kitchen=BookOutcome("imported")))
        log.report(_result(kitchen=BookOutcome("unchanged")))
        log.report(_result(kitchen=BookOutcome("failed", "Google не ответил")))
        log.report(_result(kitchen=BookOutcome("failed", "Google не ответил")))
        log.report(_result(kitchen=BookOutcome("failed", "листа «ТТК» нет в таблице")))
        log.report(_result(kitchen=BookOutcome("unchanged")))
        log.report(_result(kitchen=BookOutcome("unchanged")))

    assert [r.levelname for r in caplog.records] == ["INFO", "WARNING", "WARNING", "INFO"]
    assert "Google не ответил" in caplog.records[1].getMessage()
    assert "листа «ТТК» нет в таблице" in caplog.records[2].getMessage()
```

  Запуск: `VIRTUAL_ENV= uv run pytest tests/unit/test_worker.py -q` — падает импорт.

- [ ] **Шаг 2: настройки** — в `config.py` рядом с настройками Google:

```python
    # Синхронизация «лист → база»: воркер раз в столько секунд читает книги.
    sync_interval_seconds: int = 300
    # Данные старше этого — полоса «не обновляются» на сайте: три пропущенных цикла.
    sync_stale_after_seconds: int = 900
```

  В `.env.example` — закомментированные образцы `# SYNC_INTERVAL_SECONDS=300` и
  `# SYNC_STALE_AFTER_SECONDS=900` с той же пояснительной строкой.

- [ ] **Шаг 3: реализация** `backend/src/kitchen/worker/run.py`:

```python
"""Воркер: синхронизация «лист → база» раз в SYNC_INTERVAL_SECONDS.

Отдельным процессом, а не внутри сайта: в kitchen_bot планировщик жил в
процессе бота, и падение одного уносило другое.

* Пауза считается от начала цикла — расписание не уплывает на длительность
  переноса.
* Сигнал остановки не рвёт цикл посередине: текущий доделывается, нового нет.
* Упавший цикл не останавливает воркер: база или Google вернутся к
  следующему.
* В журнал — только смена состояния книги, а не одно и то же каждые пять
  минут.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import TYPE_CHECKING

from kitchen.config import load_settings
from kitchen.db.session import make_session_factory
from kitchen.sync.cycle import SyncCycle, reader_from

if TYPE_CHECKING:
    from collections.abc import Callable

    from kitchen.sync.cycle import BookOutcome, CycleResult

log = logging.getLogger("kitchen.worker")


def run_periodically(
    job: Callable[[], None],
    interval: float,
    stop: threading.Event,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    while not stop.is_set():
        started = clock()
        try:
            job()
        except Exception:
            log.exception("цикл синхронизации упал — следующая попытка по расписанию")
        stop.wait(max(0.0, interval - (clock() - started)))


class ChangeLog:
    """Пишет в журнал смену состояния книги, а не каждый цикл."""

    def __init__(self, logger: logging.Logger = log) -> None:
        self._logger = logger
        self._last: dict[str, BookOutcome] = {}

    def report(self, result: CycleResult) -> None:
        for book, outcome in result.outcomes.items():
            previous = self._last.get(book)
            if outcome.action == "imported":
                self._logger.info("%s: перенесены изменения", book)
            elif outcome.action == "failed" and (
                previous is None or previous.problem != outcome.problem
            ):
                self._logger.warning("%s: не переносится — %s", book, outcome.problem)
            elif (
                outcome.action == "unchanged"
                and previous is not None
                and previous.action == "failed"
            ):
                self._logger.info("%s: снова переносится", book)
            self._last[book] = outcome


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    cycle = SyncCycle(reader_from(settings), make_session_factory(settings.database_url))
    changes = ChangeLog()
    stop = threading.Event()
    # docker stop шлёт SIGTERM: даём текущему циклу закончиться, а не рвём его.
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    log.info("воркер запущен, цикл раз в %s с", settings.sync_interval_seconds)
    run_periodically(lambda: changes.report(cycle.run()), settings.sync_interval_seconds, stop)
    log.info("воркер остановлен")
    return 0
```

  `backend/src/kitchen/worker/__main__.py`:

```python
"""Точка входа воркера: `python -m kitchen.worker`."""

from kitchen.worker.run import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Шаг 4: ручной импорт** — `backend/scripts/import_sheets.py` целиком:

```python
"""Перенести Google-таблицы в базу — один цикл синхронизации вручную.

То же, что воркер делает раз в пять минут (kitchen/sync/cycle.py), но с
принудительным переносом: отпечаток не сравнивается. Правила те же: книга, у
которой лист не прочитан или сдвинулись колонки, НЕ переносится. Раньше
ручной импорт переносил и такую, только написав замечание, — при сдвиге
колонок это значило цену из колонки веса.

Запуск::

    docker compose -f infra/docker-compose.yml exec api python scripts/import_sheets.py

Код выхода 1 — хотя бы одна книга не перенесена.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kitchen.config import load_settings
from kitchen.db.session import make_session_factory
from kitchen.sync.cycle import BOOK_TITLES, BOOKS, SyncCycle, reader_from

RULE = "─" * 78

_ACTIONS = {
    "imported": "перенесена",
    "unchanged": "без изменений",
    "failed": "НЕ перенесена",
    "stale": "пропущена: другой импорт уже перенёс чтение новее",
}


def main() -> int:
    settings = load_settings()
    cycle = SyncCycle(reader_from(settings), make_session_factory(settings.database_url))
    result = cycle.run(force=True)

    print(RULE)
    print("ИМПОРТ ЛИСТОВ В БАЗУ")
    print(RULE)
    for book in BOOKS:
        outcome = result.outcomes[book]
        reason = f" — {outcome.problem}" if outcome.problem else ""
        print(f"  {BOOK_TITLES[book]:24} {_ACTIONS[outcome.action]}{reason}")

    imported = result.imported
    if imported is not None:
        print()
        print("ПЕРЕНЕСЕНО")
        print(RULE)
        for entity, number in imported.counts.items():
            print(f"  {entity:28} {number:>6}")
        if imported.warnings:
            print()
            print(f"ЗАМЕЧАНИЯ ({len(imported.warnings)})")
            print(RULE)
            print("  Перенос прошёл, но эти строки требуют внимания шефа.")
            for warning in imported.warnings[:40]:
                print(f"  · {warning}")
            if len(imported.warnings) > 40:
                print(f"  … и ещё {len(imported.warnings) - 40}")
        print()
        print(f"Прогон записан: sync_runs.id = {imported.run_id}")

    print()
    return 1 if any(o.action == "failed" for o in result.outcomes.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Шаг 5:** тесты зелёные, `lint-imports` — 2 контракта целы (worker → sync, db, config).
  Подмена: пауза `interval` вместо `interval - (clock() - started)` краснит первый тест;
  `while True` вместо `while not stop.is_set()` — зависание третьего (его и ловит).
- [ ] **Шаг 6:** коммит «Воркер синхронизации и ручной импорт одним циклом».

---

## Задача 7: Воркер в выкладке

**Files:** Modify `infra/docker-compose.yml`, `scripts/deploy.sh`; Test
`backend/tests/unit/test_infra.py`.

- [ ] **Шаг 1: падающие тесты** — в `test_infra.py` рядом с тестом воркера и в разделе
  «Выкладка»:

```python
def test_worker_podnimaetsya_vykladkoy() -> None:
    """С первой задачей — синхронизацией — воркер поднимается вместе с сайтом.

    Под профилем `up -d` из deploy.sh его не запустил бы, и данные на сайте
    молча перестали бы обновляться.
    """
    worker = _load(COMPOSE)["services"]["worker"]
    assert not worker.get("profiles"), "у воркера профиль — выкладка его не поднимет"


def test_worker_dokanchivaet_tsikl_pri_ostanovke() -> None:
    """docker по умолчанию ждёт 10 с и убивает; перенос должен успеть закончиться."""
    grace = str(_load(COMPOSE)["services"]["worker"].get("stop_grace_period", "10s"))
    assert grace.endswith("s") and int(grace[:-1]) >= 20, f"stop_grace_period={grace}"


def test_deploy_proveryaet_vorker() -> None:
    """Смоук смотрел только сайт и nginx: падающий воркер прошёл бы незамеченным."""
    text = DEPLOY.read_text(encoding="utf-8")
    check = text.find("$COMPOSE ps -q worker")
    done = text.find("Готово. Версия")

    assert check != -1, "смоук не находит контейнер воркера"
    assert "RestartCount" in text, "перезапуск по кругу обязан считаться провалом"
    assert check < done, "проверка воркера — до «Готово»"
```

  Запуск — падают три новых теста.

- [ ] **Шаг 2: compose** — у сервиса `worker` удалить `profiles: ["worker"]` и комментарий про
  «задач у воркера пока нет»; добавить:

```yaml
    # Цикл синхронизации длится секунды. docker по умолчанию ждёт 10 с и
    # убивает; даём переносу закончиться самому.
    stop_grace_period: 30s
```

- [ ] **Шаг 3: deploy.sh** — перед строкой `printf '\n\033[32mГотово. Версия…`:

```bash
# Воркер синхронизации. Проверки выше смотрят только сайт: воркер, падающий на
# старте, прошёл бы незамеченным — `restart: unless-stopped` поднимал бы его по
# кругу, а данные на сайте молча перестали бы обновляться. Даём ему поработать
# и требуем: запущен и ни разу не перезапускался.
proverit_worker() {
  local id sostoyanie
  id="$($COMPOSE ps -q worker)"
  if [[ -z "$id" ]]; then
    PRICHINA="контейнер воркера не создан"
    return 1
  fi
  sostoyanie="$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$id")"
  if [[ "$sostoyanie" != "running 0" ]]; then
    PRICHINA="воркер: состояние и число перезапусков — $sostoyanie"
    return 1
  fi
}

sleep 15
proverit_worker || { rollback; fail "$PRICHINA"; }
```

- [ ] **Шаг 4:** тесты зелёные; `bash -n scripts/deploy.sh` без ошибок; если есть shellcheck —
  чист. Проверить режим скрипта в индексе (`git ls-files -s scripts/deploy.sh` → `100755`).
- [ ] **Шаг 5:** коммит «Воркер поднимается выкладкой; смоук проверяет, что он жив».

---

## Задача 8: API — `/api/sync`, скрытие удалённого, 410

**Files:**
- Create: `backend/src/kitchen/web/sync_status.py`, `backend/tests/unit/test_sync_status.py`,
  `backend/tests/integration/test_api.py`
- Modify: `backend/src/kitchen/web/api.py`, `docs/FRONTEND.md`

**Interfaces:**
- Consumes: `models.SyncState`, `removed_at`, `BOOK_TITLES`, `SyncCycle`,
  `Settings.sync_stale_after_seconds`.
- Produces: `sync_status.BookRow` (dataclass: `book`, `checked_at`, `changed_at`, `problem`,
  `problem_since`), pydantic `SyncBook` (`book`, `title`, `checked_at`, `changed_at`, `stale`,
  `problem`, `problem_since`) и `SyncStatus` (`data_as_of`, `changed_at`, `stale`, `books`);
  `build_sync_status(rows: Sequence[BookRow], now: datetime, stale_after: timedelta) -> SyncStatus`;
  ручка `GET /api/sync`; `GET /api/dishes/{id}` → 410 для удалённого.

- [ ] **Шаг 1: падающие офлайн-тесты** `backend/tests/unit/test_sync_status.py`:

```python
"""Свежесть данных для сайта: правила без базы и без часов."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from kitchen.config import Settings
from kitchen.web.app import create_app
from kitchen.web.sync_status import BookRow, build_sync_status

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
STALE_AFTER = timedelta(minutes=15)


def _book(
    book: str,
    checked_ago: timedelta | None,
    *,
    changed_ago: timedelta | None = None,
    problem: str | None = None,
) -> BookRow:
    return BookRow(
        book=book,
        checked_at=None if checked_ago is None else NOW - checked_ago,
        changed_at=None if changed_ago is None else NOW - changed_ago,
        problem=problem,
        problem_since=None if problem is None else NOW - timedelta(minutes=30),
    )


def test_fresh_books() -> None:
    status = build_sync_status(
        [
            _book("kitchen", timedelta(minutes=2), changed_ago=timedelta(hours=1)),
            _book("ingredient_cards", timedelta(minutes=1), changed_ago=timedelta(hours=3)),
        ],
        NOW,
        STALE_AFTER,
    )

    assert status.stale is False
    assert status.data_as_of == NOW - timedelta(minutes=2), "самая ранняя проверка — не приукрашиваем"
    assert status.changed_at == NOW - timedelta(hours=1), "самое позднее изменение — повод перезапросить"
    assert [b.title for b in status.books] == ["таблица кухни", "карточки ингредиентов"]


def test_stale_means_older_than_threshold() -> None:
    """«Старше 15 минут»: ровно 15 — ещё свежо, на секунду больше — уже нет."""
    fresh = _book("ingredient_cards", timedelta(0))
    at_limit = build_sync_status([_book("kitchen", STALE_AFTER), fresh], NOW, STALE_AFTER)
    past = build_sync_status(
        [_book("kitchen", STALE_AFTER + timedelta(seconds=1)), fresh], NOW, STALE_AFTER
    )

    assert at_limit.stale is False
    assert past.stale is True


def test_one_book_behind_with_reason() -> None:
    status = build_sync_status(
        [
            _book("kitchen", timedelta(minutes=2)),
            _book("ingredient_cards", timedelta(minutes=55), problem="доступ платформы к таблице закрыт"),
        ],
        NOW,
        STALE_AFTER,
    )
    books = {b.book: b for b in status.books}

    assert status.stale is True
    assert books["ingredient_cards"].stale is True
    assert books["ingredient_cards"].problem == "доступ платформы к таблице закрыт"
    assert books["kitchen"].stale is False, "свежая книга в полосу не попадает"


def test_never_synced() -> None:
    status = build_sync_status([], NOW, STALE_AFTER)

    assert status.data_as_of is None
    assert status.stale is True
    assert all(b.stale and b.checked_at is None for b in status.books)


def test_sync_requires_login() -> None:
    reply = TestClient(create_app(Settings(app_env="test"))).get("/api/sync")

    assert reply.status_code == 401
```

- [ ] **Шаг 2: реализация** `backend/src/kitchen/web/sync_status.py`:

```python
"""Ответ `GET /api/sync`: насколько свежи данные на сайте.

Чистая функция отдельно от ручки: правила «когда данные устарели» и «что
показывать» проверяются тестом без базы и без часов. Считает сервер, а не
браузер: часы телефона могут врать.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel

from kitchen.sync.cycle import BOOK_TITLES

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class BookRow:
    """Строка `sync_state` в том объёме, что нужен ответу."""

    book: str
    checked_at: datetime | None
    changed_at: datetime | None
    problem: str | None
    problem_since: datetime | None


class SyncBook(BaseModel):
    book: str
    title: str
    checked_at: datetime | None
    changed_at: datetime | None
    stale: bool
    problem: str | None
    problem_since: datetime | None


class SyncStatus(BaseModel):
    data_as_of: datetime | None
    """Самая ранняя проверка из книг: строка на сайте не обещает свежести,
    которой нет."""

    changed_at: datetime | None
    """Самое позднее изменение: по нему экраны понимают, что пора
    перезапросить данные."""

    stale: bool
    books: list[SyncBook]


def build_sync_status(
    rows: Sequence[BookRow], now: datetime, stale_after: timedelta
) -> SyncStatus:
    by_book = {row.book: row for row in rows}
    books: list[SyncBook] = []
    for book, title in BOOK_TITLES.items():
        row = by_book.get(book)
        checked = row.checked_at if row else None
        books.append(
            SyncBook(
                book=book,
                title=title,
                checked_at=checked,
                changed_at=row.changed_at if row else None,
                stale=checked is None or now - checked > stale_after,
                problem=row.problem if row else None,
                problem_since=row.problem_since if row else None,
            )
        )

    checked_all = [b.checked_at for b in books]
    changed = [b.changed_at for b in books if b.changed_at is not None]
    return SyncStatus(
        data_as_of=None if None in checked_all else min(c for c in checked_all if c is not None),
        changed_at=max(changed) if changed else None,
        stale=any(b.stale for b in books),
        books=books,
    )
```

  В `api.py`: импорты `from datetime import UTC, datetime, timedelta`, `Depends` из `fastapi`,
  `from kitchen.config import Settings`, `get_settings` из `kitchen.web.auth`,
  `from kitchen.web.sync_status import BookRow, SyncStatus, build_sync_status` (всё — обычными
  импортами: FastAPI читает аннотации во время выполнения). Ручка:

```python
@router.get("/sync", response_model=SyncStatus)
def sync(
    session: SessionDep,
    user: CurrentUserDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SyncStatus:
    """Насколько свежи данные — для строки над каждым экраном."""
    rows = [
        BookRow(
            book=state.book,
            checked_at=state.checked_at,
            changed_at=state.changed_at,
            problem=state.problem,
            problem_since=state.problem_since,
        )
        for state in session.scalars(select(models.SyncState)).all()
    ]
    return build_sync_status(
        rows, datetime.now(UTC), timedelta(seconds=settings.sync_stale_after_seconds)
    )
```

  Офлайн-тесты зелёные.

- [ ] **Шаг 3: падающие интеграционные тесты** `backend/tests/integration/test_api.py`:

```python
"""Ручки чтения на настоящей базе: удалённое скрыто, /api/sync отвечает."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from kitchen.config import Settings
from kitchen.sync import specs
from kitchen.sync.cycle import SyncCycle
from kitchen.sync.importer import Importer
from kitchen.sync.reader import SheetsReader
from kitchen.web import auth
from kitchen.web.app import create_app
from tests.fake_sheets import IDS, cards_sheet, header, kitchen_sheets, sheets_client
from tests.integration.test_database import _url

pytestmark = pytest.mark.integration


@pytest.fixture
def client(sessions) -> TestClient:
    app = create_app(Settings(app_env="test", database_url=_url()))
    app.dependency_overrides[auth.current_user] = lambda: auth.CurrentUser(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        email="chef@example.com",
        display_name="Шеф",
        roles=frozenset({"chef"}),
    )
    return TestClient(app)


def _import(sessions, **sheets) -> None:
    Importer(SheetsReader(sheets_client(**sheets), IDS), sessions).run()


def test_removed_dish_hidden_from_list_and_gone_on_card(sessions, client) -> None:
    _import(sessions)
    _import(sessions, kitchen={"Блюда": [header(specs.DISHES)]})

    assert client.get("/api/dishes").json() == []
    reply = client.get("/api/dishes/B001")
    assert reply.status_code == 410
    assert "удалено из таблицы" in reply.json()["detail"]


def test_unknown_dish_is_still_404(sessions, client) -> None:
    _import(sessions)

    assert client.get("/api/dishes/B999").status_code == 404


def test_removed_ingredient_hidden_from_reference(sessions, client) -> None:
    _import(sessions)
    ing = kitchen_sheets()["ING"]
    _import(sessions, kitchen={"ING": [ing[0], ing[1], ing[3]]})

    assert {i["legacy_id"] for i in client.get("/api/ingredients").json()} == {"1", "3"}


def test_removed_card_leaves_reconciliation_and_has_card(sessions, client) -> None:
    _import(sessions)
    assert {i["legacy_id"]: i["has_card"] for i in client.get("/api/ingredients").json()}["1"]

    cards = [line for line in cards_sheet() if "Томаты" not in line and "Пастрами из индейки" not in line]
    _import(sessions, cards=cards)

    summary = client.get("/api/reconciliation").json()
    assert summary["total"] == 1, "осталась одна карточка — «Сахар»"
    assert [r["name"] for r in summary["rows"]] == ["Сахар"]
    assert not {i["legacy_id"]: i["has_card"] for i in client.get("/api/ingredients").json()}["1"]


def test_sync_reports_both_books(sessions, client) -> None:
    SyncCycle(SheetsReader(sheets_client(), IDS), sessions).run()

    body = client.get("/api/sync").json()

    assert body["stale"] is False
    assert body["data_as_of"] is not None
    assert [(b["book"], b["problem"]) for b in body["books"]] == [
        ("kitchen", None),
        ("ingredient_cards", None),
    ]


def test_sync_before_first_cycle_is_stale(sessions, client) -> None:
    body = client.get("/api/sync").json()

    assert (body["data_as_of"], body["stale"]) == (None, True)
```

  Пуш — CI краснеет на фильтрах и 410 (ручка `/api/sync` уже есть).

- [ ] **Шаг 4: реализация фильтров** в `api.py`:
  - `ingredients`: `select(models.Ingredient).where(models.Ingredient.removed_at.is_(None)).order_by(...)`;
    в наборе `linked` — `.where(models.IngredientCard.ingredient_id.is_not(None), models.IngredientCard.removed_at.is_(None))`;
  - `dishes`: `select(models.Dish).where(models.Dish.removed_at.is_(None)).order_by(...)`;
  - `dish_detail`: сразу после проверки `dish is None` —

```python
    if dish.removed_at is not None:
        # Не 404: блюдо было, шеф убрал строку из листа. Вернёт — появится снова.
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="блюдо удалено из таблицы")
```

  - `reconciliation`: к подсчёту по статусам и к выборке карточек — условие
    `models.IngredientCard.removed_at.is_(None)`; к тёзкам —
    `models.Ingredient.removed_at.is_(None)`.
  - docstring модуля: «Строки, удалённые из листа, ручки не показывают (`removed_at`).»

- [ ] **Шаг 5: контракт** — в `docs/FRONTEND.md`, раздел 4, после `/api/reconciliation` —
  подраздел `### GET /api/sync` (назначение, пример ответа из спеки, раздел 7, с полем `stale`
  у каждой книги и названиями со строчной, правила `data_as_of`/`changed_at`/`stale`); в
  подразделе `/api/dishes/{legacy_id}` — строка «410 — блюдо удалено из листа «Блюда»; 404 —
  такого блюда не было»; в описании списков — «строки, удалённые из листа, не отдаются».
- [ ] **Шаг 6:** локальные проверки; пуш — CI зелёный.
- [ ] **Шаг 7:** коммит «API: /api/sync, удалённое из листа скрыто, 410 на карточке».

---

## Задача 9: Экран — строка свежести, самообновление, 410

**Files:**
- Create: `frontend/src/domain/vremya.ts`, `frontend/src/shell/Svezhest.tsx`,
  `frontend/tests/vremya.test.ts`, `frontend/tests/svezhest.test.tsx`
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/queries.ts`,
  `frontend/src/shell/Layout.tsx`, `frontend/src/shell/shell.css`,
  `frontend/src/pages/DishDetail.tsx`, `frontend/tests/obolochka.test.tsx`,
  `frontend/tests/vhod.test.tsx`, `frontend/tests/kartochka.test.tsx`, `docs/FRONTEND.md`

**Interfaces:**
- Consumes: `GET /api/sync` (задача 8); ключи запросов `['ingredients']`, `['dishes']`,
  `['dish']`, `['reconciliation']` из `api/queries.ts`.
- Produces: `vremyaDannyh(iso: string, seychas?: Date): string`; `useSync()`; `<Svezhest />`.

- [ ] **Шаг 1: падающий тест времени** `frontend/tests/vremya.test.ts`:

```ts
import { expect, test } from 'vitest'

import { vremyaDannyh } from '../src/domain/vremya'

test('сегодня по Москве — только часы и минуты', () => {
  expect(vremyaDannyh('2026-09-23T11:35:00Z', new Date('2026-09-23T12:00:00Z'))).toBe('14:35')
})

test('не сегодня — с датой', () => {
  expect(vremyaDannyh('2026-09-22T20:10:00Z', new Date('2026-09-23T09:00:00Z'))).toBe('22.09 23:10')
})

test('граница дня — по Москве, а не по UTC', () => {
  // 22:30 UTC 22.09 — это уже 01:30 23.09 в Москве: тот же день, что и «сейчас».
  expect(vremyaDannyh('2026-09-22T22:30:00Z', new Date('2026-09-23T09:00:00Z'))).toBe('01:30')
})
```

  `npx vitest run tests/vremya.test.ts` — падает (модуля нет). Реализация
  `frontend/src/domain/vremya.ts`:

```ts
// Время данных по Москве, где кухня, — а не по часовому поясу телефона.
const CHASY = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow',
  hour: '2-digit',
  minute: '2-digit',
})
const DEN = new Intl.DateTimeFormat('ru-RU', {
  timeZone: 'Europe/Moscow',
  day: '2-digit',
  month: '2-digit',
})

/** «14:35», а если не сегодня по Москве — «23.09 14:35». */
export function vremyaDannyh(iso: string, seychas: Date = new Date()): string {
  const moment = new Date(iso)
  const chasy = CHASY.format(moment)
  const den = DEN.format(moment)
  return den === DEN.format(seychas) ? chasy : `${den} ${chasy}`
}
```

- [ ] **Шаг 2: типы и запрос.** В `types.ts`:

```ts
/** Книга — Google-таблица. Ответ GET /api/sync. */
export type SyncBook = {
  book: string
  /** Со строчной: стоит посреди фразы. */
  title: string
  checked_at: string | null
  changed_at: string | null
  /** Старше 15 минут — считает сервер по своим часам. */
  stale: boolean
  /** Причина сбоя словами для всех, не код ошибки. */
  problem: string | null
  problem_since: string | null
}

export type SyncStatus = {
  data_as_of: string | null
  changed_at: string | null
  stale: boolean
  books: SyncBook[]
}
```

  В `queries.ts` (импорт `SyncStatus`):

```ts
export function useSync(): UseQueryResult<SyncStatus> {
  return useQuery({
    queryKey: ['sync'],
    queryFn: () => api<SyncStatus>('/sync'),
    // Синхронизация — раз в пять минут; строка отстаёт от неё не больше чем на минуту.
    refetchInterval: 60_000,
  })
}
```

- [ ] **Шаг 3: падающие тесты строки** `frontend/tests/svezhest.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { ReactNode } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from 'vitest'

import { useDishes } from '../src/api/queries'
import type { SyncBook, SyncStatus } from '../src/api/types'
import { Svezhest } from '../src/shell/Svezhest'

const KUHNYA = { book: 'kitchen', title: 'таблица кухни' }
const KARTOCHKI = { book: 'ingredient_cards', title: 'карточки ингредиентов' }

function kniga(chto: Partial<SyncBook> & { book: string; title: string }): SyncBook {
  return {
    checked_at: '2026-09-23T11:58:00Z',
    changed_at: '2026-09-23T11:20:00Z',
    stale: false,
    problem: null,
    problem_since: null,
    ...chto,
  }
}

function svezho(changed_at = '2026-09-23T11:20:00Z'): SyncStatus {
  return {
    data_as_of: '2026-09-23T11:35:00Z',
    changed_at,
    stale: false,
    books: [
      kniga({ ...KUHNYA, checked_at: '2026-09-23T11:35:00Z' }),
      kniga({ ...KARTOCHKI, checked_at: '2026-09-23T11:36:00Z' }),
    ],
  }
}

let otvet: SyncStatus = svezho()
const server = setupServer(http.get('/api/sync', () => HttpResponse.json(otvet)))

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
// Замораживаем только дату: msw и TanStack Query живут на настоящих таймерах.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-09-23T12:00:00Z')) // 15:00 по Москве
  otvet = svezho()
})
afterEach(() => {
  vi.useRealTimers()
  server.resetHandlers()
})
afterAll(() => server.close())

function narisovat(deti: ReactNode = <p>экран на месте</p>) {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queries}>
      <Svezhest />
      {deti}
    </QueryClientProvider>,
  )
  return queries
}

test('свежо — приглушённая строка со временем по Москве', async () => {
  narisovat()

  expect(await screen.findByText('Данные из таблицы на 14:35')).toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

test('отставшая книга — полоса с временем и причиной, свежая книга в неё не попадает', async () => {
  otvet = {
    ...svezho(),
    data_as_of: '2026-09-23T11:05:00Z',
    stale: true,
    books: [
      kniga({ ...KUHNYA }),
      kniga({
        ...KARTOCHKI,
        checked_at: '2026-09-23T11:05:00Z',
        stale: true,
        problem: 'доступ платформы к таблице закрыт',
        problem_since: '2026-09-23T11:10:00Z',
      }),
    ],
  }
  narisovat()

  const polosa = await screen.findByRole('status')
  expect(polosa).toHaveTextContent(
    'Данные не обновляются с 14:05 — карточки ингредиентов: доступ платформы к таблице закрыт',
  )
  expect(polosa).not.toHaveTextContent('таблица кухни')
})

test('отстала без причины — значит, не работает сам воркер', async () => {
  otvet = { ...svezho(), stale: true, books: [kniga({ ...KUHNYA, checked_at: '2026-09-23T11:05:00Z', stale: true }), kniga({ ...KARTOCHKI })] }
  narisovat()

  expect(await screen.findByRole('status')).toHaveTextContent('синхронизация не запущена')
})

test('ни одной проверки — так и сказано', async () => {
  otvet = {
    data_as_of: null,
    changed_at: null,
    stale: true,
    books: [
      kniga({ ...KUHNYA, checked_at: null, changed_at: null, stale: true }),
      kniga({ ...KARTOCHKI, checked_at: null, changed_at: null, stale: true }),
    ],
  }
  narisovat()

  expect(await screen.findByRole('status')).toHaveTextContent(
    'Данные не синхронизировались — таблица кухни',
  )
})

test('ручка не ответила — строки нет, экран живёт', async () => {
  server.use(http.get('/api/sync', () => new HttpResponse(null, { status: 500 })))
  const queries = narisovat()

  await waitFor(() => expect(queries.getQueryState(['sync'])?.status).toBe('error'))
  expect(screen.getByText('экран на месте')).toBeInTheDocument()
  expect(screen.queryByText(/Данные/)).not.toBeInTheDocument()
})

function Proba() {
  useDishes()
  return <p>экран на месте</p>
}

test('сменилось время изменения — открытые экраны перезапрашивают данные', async () => {
  let zaprosov = 0
  server.use(
    http.get('/api/dishes', () => {
      zaprosov += 1
      return HttpResponse.json([])
    }),
  )
  const queries = narisovat(<Proba />)
  await screen.findByText('Данные из таблицы на 14:35')
  await waitFor(() => expect(zaprosov).toBe(1))

  await queries.refetchQueries({ queryKey: ['sync'] }) // то же время изменения
  expect(zaprosov).toBe(1)

  otvet = svezho('2026-09-23T11:55:00Z')
  await queries.refetchQueries({ queryKey: ['sync'] })
  await waitFor(() => expect(zaprosov).toBe(2))
})
```

  `npx vitest run tests/svezhest.test.tsx` — падает (компонента нет).

- [ ] **Шаг 4: реализация** `frontend/src/shell/Svezhest.tsx`:

```tsx
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { useSync } from '../api/queries'
import type { SyncBook } from '../api/types'
import { vremyaDannyh } from '../domain/vremya'

// Экраны с данными из таблиц — префиксы ключей из api/queries.ts. Их
// перезапрашиваем, когда синхронизация перенесла изменения.
const EKRANY = [['ingredients'], ['dishes'], ['dish'], ['reconciliation']]

function otstavanie(kniga: SyncBook): string {
  if (kniga.checked_at === null) {
    return `Данные не синхронизировались — ${kniga.title}${kniga.problem ? `: ${kniga.problem}` : ''}`
  }
  const prichina = kniga.problem ?? 'синхронизация не запущена'
  return `Данные не обновляются с ${vremyaDannyh(kniga.checked_at)} — ${kniga.title}: ${prichina}`
}

/**
 * Насколько свежи данные на экране.
 *
 * Сайт, который расходится с таблицей и молчит, хуже никакого: шеф правит
 * лист и не понимает, почему на сайте старое. Свежо — приглушённая строка.
 * Книга отстала больше чем на 15 минут (считает сервер) — полоса с причиной;
 * её видят все, часть причин шеф устранит сам. Ручка не ответила — строки
 * нет, экраны живут как жили.
 */
export function Svezhest() {
  const query = useSync()
  const queries = useQueryClient()
  const prezhnee = useRef<string | null | undefined>(undefined)
  const izmeneno = query.data?.changed_at

  useEffect(() => {
    if (izmeneno === undefined) return
    if (prezhnee.current !== undefined && prezhnee.current !== izmeneno) {
      for (const queryKey of EKRANY) void queries.invalidateQueries({ queryKey })
    }
    prezhnee.current = izmeneno
  }, [izmeneno, queries])

  if (!query.data) return null
  const otstavshie = query.data.books.filter((kniga) => kniga.stale)
  if (otstavshie.length === 0 && query.data.data_as_of !== null) {
    return <p className="svezhest">Данные из таблицы на {vremyaDannyh(query.data.data_as_of)}</p>
  }
  return (
    <div className="svezhest svezhest--staro" role="status">
      {otstavshie.map((kniga) => (
        <p key={kniga.book}>{otstavanie(kniga)}</p>
      ))}
    </div>
  )
}
```

  `Layout.tsx`: импорт `Svezhest` и `<main className="soderzhimoe"><Svezhest /><Outlet /></main>`.
  `shell.css`:

```css
/* Строка свежести данных над каждым экраном. */
.svezhest {
  margin: 0 0 calc(var(--shag) * 2);
  color: var(--priglushyonnyy);
  font-size: 0.875em;
}

.svezhest--staro {
  padding: calc(var(--shag) * 2);
  border-left: 3px solid var(--vnimanie);
  background: var(--vnimanie-fon);
  color: var(--tekst);
}

.svezhest--staro p {
  margin: 0;
}
```

- [ ] **Шаг 5: 410 на карточке.** Падающий тест в `kartochka.test.tsx`:

```tsx
test('блюдо удалено из таблицы — своё сообщение, а не «такого нет»', async () => {
  server.use(
    http.get('/api/dishes/B001', () =>
      HttpResponse.json({ detail: 'блюдо удалено из таблицы' }, { status: 410 }),
    ),
  )
  narisovat()

  expect(await screen.findByRole('heading', { name: 'Блюдо удалено из таблицы' })).toBeInTheDocument()
  expect(screen.queryByText('Такого блюда нет')).not.toBeInTheDocument()
})
```

  В `DishDetail.tsx` перед веткой 404:

```tsx
  // 410 — блюдо было, шеф убрал строку из листа «Блюда». Не «такого нет»: вернёт
  // строку — блюдо появится снова. «Повторить» не нужно, как и у 404.
  if (query.isError && query.error instanceof ApiError && query.error.status === 410) {
    return (
      <section className="kartochka">
        <Link to="/dishes" className="nazad">
          ← Блюда
        </Link>
        <div className="sostoyanie" role="alert">
          <h1>Блюдо удалено из таблицы</h1>
          <p className="sostoyanie-prichina">
            Строки «{legacyId}» больше нет в листе «Блюда», поэтому на сайте блюдо скрыто. Вернут
            строку в таблицу — блюдо появится снова.
          </p>
        </div>
      </section>
    )
  }
```

- [ ] **Шаг 6: тесты оболочки не ходят в сеть** (правило Ruling 42). В `obolochka.test.tsx`
  добавить в `setupServer(...)` обработчик `/api/sync` со свежим ответом и тест-проводку:

```tsx
  http.get('/api/sync', () =>
    HttpResponse.json({
      data_as_of: '2026-09-23T11:35:00Z',
      changed_at: '2026-09-23T11:20:00Z',
      stale: false,
      books: [
        { book: 'kitchen', title: 'таблица кухни', checked_at: '2026-09-23T11:35:00Z', changed_at: '2026-09-23T11:20:00Z', stale: false, problem: null, problem_since: null },
        { book: 'ingredient_cards', title: 'карточки ингредиентов', checked_at: '2026-09-23T11:36:00Z', changed_at: '2026-09-23T09:00:00Z', stale: false, problem: null, problem_since: null },
      ],
    }),
  ),
```

```tsx
test('строка свежести стоит над экраном', async () => {
  narisovat()
  expect(await screen.findByText(/^Данные из таблицы на /)).toBeInTheDocument()
})
```

  В `vhod.test.tsx` — тот же обработчик `/api/sync` в `beforeEach` рядом с `/api/dishes`
  (перекрываемый по тем же правилам).
- [ ] **Шаг 7:** `npm run types && npm run lint && npm run test && npm run build` — всё
  зелёное; подмена — убрать сравнение `prezhnee.current !== izmeneno` краснит тест
  перезапроса («то же время изменения»).
- [ ] **Шаг 8: документ** — в `docs/FRONTEND.md`, раздел 5, подраздел «5.6 Строка свежести
  данных» (где стоит, четыре состояния из раздела 8 спеки, самообновление экранов, роль
  `status`); в «5.4 Карточка блюда» — состояние 410.
- [ ] **Шаг 9:** коммит «Строка свежести данных и самообновление экранов».

---

## Задача 10: Итоговое ревью, слияние, выкладка, приёмка

- [ ] **Шаг 1:** полные проверки бэкенда и фронтенда локально, CI зелёный на голове ветки.
- [ ] **Шаг 2:** итоговое ревью всей ветки (`git diff origin/main...HEAD`) на самой способной
  модели: спека против кода по каждому разделу, инварианты domain-guard, миграция по
  чек-листу, тексты для шефа. Находки Critical/Important — исправить с тестами, повторное
  ревью правок.
- [ ] **Шаг 3:** PR из черновика — в готовые; описание с доказательствами (число тестов,
  подмены, CI). **Слияние — только по слову Александра**, merge-коммитом, после зелёного CI;
  при защите `main` — ветка должна быть свежей (кнопка Update branch и новый прогон CI).
- [ ] **Шаг 4:** выкладка `./scripts/deploy.sh` в фоне (`nohup setsid … > ~/deploy-….log`),
  ждать «Готово» или «ОШИБКА»; миграция применится сама, смоук проверит воркер.
- [ ] **Шаг 5:** приёмка на сервере — раздел «Проверка» ниже.
- [ ] **Шаг 6:** роадмап — отдельным небольшим PR после приёмки (как PR №16: доказательства
  появляются только на сервере): пункт фазы 1 «Поллинг раз в 5 минут» — в сделанные со строкой
  доказательства из раздела «Проверка»; абзац «Данные на сайте» в «Где мы сейчас» —
  «синхронизируются раз в 5 минут, строка свежести над каждым экраном»; в порядке работ п. 2
  отмечен. Слияние — по слову Александра.
- [ ] **Шаг 7:** обновить план (`C:\Users\karppacho\.claude\plans\pasted-content-id-0098-eventual-flute.md`)
  и память проекта: этап 2 закрыт, дальше — этап 3 (фильтры и сортировка).

---

## Проверка (сквозная)

Локально, до PR:
- бэкенд: `VIRTUAL_ENV= uv run ruff check . && VIRTUAL_ENV= uv run ruff format --check . && VIRTUAL_ENV= uv run mypy src && VIRTUAL_ENV= uv run lint-imports && VIRTUAL_ENV= uv run pytest` — всё зелёное, новые офлайн-тесты посчитаны;
- фронтенд: `npm run types && npm run lint && npm run test && npm run build`.

CI на PR: 8 из 8, включая «тесты» (офлайн + интеграция на Postgres 16, порог покрытия
`kitchen.domain` + `kitchen.sync` ≥ 85 %) и «образ nginx (живая проверка)».

На сервере после выкладки:
1. `/healthz` отдаёт хеш `main`; `docker ps` — контейнер `kitchen-platform-worker-1` в
   состоянии Up, без перезапусков; `docker compose -f infra/docker-compose.yml logs worker` —
   «воркер запущен», затем «перенесены изменения» по обеим книгам (первый цикл после выкладки
   переносит всё: отпечатков ещё нет).
2. Через 10–15 минут: в `sync_state` у обеих книг `checked_at` сдвигается каждые 5 минут,
   `problem` пусто; `sync_runs` не растёт, пока таблица не меняется. В замечаниях первого
   переноса — сколько строк скрыто (если шеф что-то удалял с 10.09); если скрыто много —
   разобрать с Александром до следующих шагов.
3. На сайте над экранами — «Данные из таблицы на ЧЧ:ММ» (время по Москве).
4. Живая проверка с Александром: он или шеф меняет безобидную ячейку (комментарий у блюда в
   «Блюда») — через 5–6 минут открытый экран списка блюд обновляется сам; ячейку возвращают, и
   через 5–6 минут — снова.
5. `docker compose -f infra/docker-compose.yml exec -T api python scripts/verify_golden.py` —
   129 из 130, код 0. Если появились расхождения только вида «удалён из справочника» —
   значит, шеф удалил строку, на которую ссылается ТТК: это новое правило, эталон не трогать,
   показать Александру список.
6. `docker compose -f infra/docker-compose.yml exec -T api python scripts/import_sheets.py` —
   обе книги «перенесена», код 0 (ручной путь жив).
7. `docker compose -f infra/docker-compose.yml restart worker` — в журнале «воркер
   остановлен» и новый «воркер запущен», без обрыва посреди переноса.

---

## Сверка плана со спекой

| Раздел спеки | Задача |
|---|---|
| 3. Воркер (цикл, SIGTERM, grace, профиль, журнал смен) | 6, 7 |
| 4.1–4.2 Книги, чтение, проверка книги, отпечаток, перенос | 4, 5 |
| 4.3 Два импорта не накладываются; старое чтение не перезаписывает новое | 2, 5 |
| 4.4 Ручной импорт = цикл с force, книга со сдвигом не переносится | 6 |
| 4.5 Сбои | 4 (`explain`), 5, 6 |
| 5.1–5.3 `sync_state`, события в `sync_runs`, миграция | 1, 5 |
| 6. Удаление строк: отметка, возврат, скрытие, 410, расчёт, подбор пары | 1, 2, 3, 8, 9 |
| 7. `GET /api/sync` | 8 |
| 8. Экран: строка, полоса, самообновление, время по Москве | 9 |
| 9. Проверки | во всех задачах |
| 10. Выкладка и приёмка | 7, 10 |
