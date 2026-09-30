# Новый ингредиент по фото этикетки — спека

Замена `pizza_bot_new` на веб-платформе. План исполнения: `docs/superpowers/plans/2026-09-30-novyi-ingredient.md`.

## 1. Зачем

Шеф просит вернуть бота, через которого повара заводили новые ингредиенты: фото этикетки → распознавание → правка → фото продукта → строка в Google-таблице карточек. Бот (`pizza_bot_new`, Telegram) лежит с 08.09 — Telegram закрыт с сервера в РФ, боты не вернутся (решение 23.09). Переносим поток на веб-платформу как раздел «Новый ингредиент» — это этап 5 общего плана и **первая функция платформы, которая пишет в Google-таблицу**.

Бот (разобран полностью): 9 шагов — поставщик (F), категория (A), фото этикетки с названием (B, ссылка в P), распознавание через polza.ai (модель `qwen/qwen3.6-plus`, 11 полей → C, E, G, H–K, L–O) и правка, «согласован?» (V: «Да»/«Отбракован»), три необязательных фото (S, T, U), описание (D). Q (декларация) и R (халяль) — только люди. Его беды: две записи в одну строку при одновременной работе, брошенные на последнем шаге черновики, фото открыты всем по ссылке, модель сама «считала» сроки по датам, HTML-инъекции.

**Решения Александра (30.09):** фото — в закрытой папке Google Drive на **общем диске** (никаких «доступно всем по ссылке»); у каждого повара своя учётка (роль `cook`); «Продукт согласован?» отмечает повар, как в боте; **повар видит только «Новый ингредиент»**, цены и маржу ему сервер не отдаёт; срок по двум датам ровно в год пишется «12 месяцев» (всё длиннее 60 дней — месяцами).

Итог: повар на телефоне проходит мастер из 9 шагов на `/cards`, может закрыть браузер и продолжить; при «Отправить» платформа одной безопасной записью добавляет строку в «Лист1» карточек, карточка сразу появляется в базе обычным переносом и видна на экране сверки.

**Tech Stack:** FastAPI + SQLAlchemy 2 + Alembic (Postgres/Supabase), gspread 6.2 + REST Google Drive, OpenAI SDK с `base_url` polza.ai; React 18 + TanStack Query 5, vitest + msw.

## 2. Решения

1. **Черновик — на сервере** (таблица `card_drafts`, один активный на повара); лист пишется **один раз при отправке**, одной записью — без резерва строк, без частичных строк, без гонок.
2. **Запись открыта только книге карточек:** `BOTS_ALIVE = False` (боты сняты — проверяется на сервере) плюс второй замок `WRITE_OPEN = frozenset({"ingredient_cards"})`; книга кухни остаётся закрытой до своего пути записи. ADR-0003 — первая ступень: только добавление новой строки карточки.
3. **Писатель строки** (`sync/writer.py`): своя advisory-блокировка писателей `SHEET_WRITE_LOCK_KEY` (не импортная); свободная строка — первая после последней непустой B, пустая во всей ширине (A–V и дальше); проверка двустрочной шапки тем же `check_header`, что у импорта; одна запись `values.batchUpdate` RAW двумя диапазонами A–P и S–V (Q и R — никогда); перечитать и сверить; при расхождении очистить **только наши** ячейки; журнал `sheet_writes` со снимком «до», значениями, автором и `request_key` (повтор отправки не даёт второй строки); неясный исход записи решается перечитыванием строки. Точный дубль имени (после `normalise_name`) в свежем листе — отказ; похожие — подсказка.
4. **Карточка попадает в базу обычным путём** — принудительный перенос одной книги карточек `SyncCycle.run(force=True, books=("ingredient_cards",))`; карточку только в базе импорт бы скрыл.
5. **Фото:** уменьшаются в браузере (canvas, до 1600 px, JPEG; превью через `data:` — CSP не трогаем), сервер проверяет размер (≤ 8 МБ) и сигнатуру JPEG, в Drive грузит REST-запросом в закрытую папку общего диска (`supportsAllDrives`), **никогда не вызывает `permissions.create`**; в лист пишется ссылка просмотра Drive; платформа показывает фото вошедшим через свой прокси по слоту черновика (не по произвольному id). Имя файла как у бота: `{поставщик}_{ингредиент}_{этикетка|упаковка|до|после}_{дата-время МСК}.jpg`.
6. **Распознавание:** свой клиент polza.ai (`temperature 0`, режим JSON с откатом без него, свои таймауты и один повтор, стоимость `cost_rub` в журнал `llm_calls`, дневной бюджет и лимит на повара); промпт бота портирован, но **модель только читает** (даты — как написаны), период, склонения и формат строки срока **считает код** (`domain/shelf_life.py`); КБЖУ из строк модели в `Decimal` разбирает код; ответ проверяется схемой; текст этикетки — данные, не инструкции. Состояние распознавания хранится в черновике — телефон мог уснуть, результат не теряется.
7. **Роли:** ручки карточек — `cook`, `chef`, `developer`; ручки справочника, блюд и сверки — `chef`, `developer`, `commerce` (повару 403); меню фильтруется по ролям, для человека только с ролью `cook` главная — `/cards`.
8. **Защита от подделки запросов** на все изменяющие запросы: заголовок `X-Kitchen-Csrf: 1` + проверка `Origin` (закрывает и долг «подделка выхода»).
9. **Категории:** 15 категорий бота как затравка ∪ категории существующих карточек по частоте, плюс «Другая…»; общего редактируемого списка нет. **Название:** подсказки из карточек и из справочника ING — взяв имя из справочника, карточка склеится сама.
10. **Брошенный черновик** живёт, пока повар его не продолжит или не сбросит; «Начать заново» отправляет его фото в корзину Drive. Уборка по сроку и отзыв публичного доступа у старых фото бота — после этапа.

## 3. Ограничения

- Запись в Google-таблицы — **только** «Лист1» книги карточек, **только добавление новой строки**, только колонки A–P и S–V; Q и R не пишутся никогда; существующие строки не меняются; никаких `append_row`; перед записью — проверка шапки, после — перечитать и сверить. Книга кухни закрыта.
- Модель вызывается **только через polza.ai**; модель не считает и не форматирует числа и сроки — это делает код; КБЖУ и деньги — `Decimal`, никаких `float`/`Number()`/`parseFloat` в расчётном пути; ответ модели проверяется схемой.
- Фото — только в закрытой папке общего диска; ни одного права «anyone/domain».
- Повару (`cook`) не отдаются цены, маржа и справочник; изменяющие запросы требуют CSRF-заголовка.
- Слои import-linter: `kitchen.web | kitchen.worker` → `kitchen.cards` → `kitchen.sync | kitchen.llm` → `kitchen.db` → `kitchen.domain`; mypy strict для `kitchen.domain.*`, `kitchen.sync.*`, `kitchen.llm.*`, `kitchen.cards.*`; порог покрытия 85 % включает новые пакеты.
- Миграции — только новые таблицы, обратимые (чек-лист migration-guard).
- Имена: бэкенд — английские, фронтенд — транслит; тексты для людей и комментарии — по-русски, простыми словами. В репозитории нет названия сети и юрлица клиента, нет имён людей, адресов почты и id папок.
- Правки `CLAUDE.md` и `.claude/agents/*` — только с согласия Александра.
- Блокирующий CI — до пяти минут. Коммиты заканчиваются `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Windows: файлы — только Write/Edit, после правок `git ls-files --eol` → `w/lf`; перед `uv` — `VIRTUAL_ENV=`.

## 4. Устройство

**Бэкенд, новые и изменённые файлы:**
```
domain/cards.py        поля карточки, чистка текста, КБЖУ (parse_nutrient поверх money.parse_decimal), проверки КБЖУ,
                       CardDraftData, missing_for_submit, card_row (ровно 20 полей A–P, S–V), drive_view_url,
                       photo_file_name, label_fields_from_extraction, DEFAULT_CATEGORIES (15 из бота), APPROVED/REJECTED
domain/shelf_life.py   parse_label_date, full_months_between, plural_ru, describe_shelf_life(period, manufactured,
                       best_before, conditions) -> (text, warnings); > 60 дней — месяцами («12 месяцев»), иначе днями
sync/ownership.py      BOTS_ALIVE=False, WRITE_OPEN, _may_write(owner, spreadsheet)
sync/client.py         протокол записи: values_batch_get(ranges, params), values_batch_update, values_batch_clear,
                       batch_update; Worksheet.row_count, .id (методы gspread 6.2 уже есть)
sync/reader.py         check_header, convert_cell, row_hash — публичными
sync/writer.py         CardSheetWriter.append(values, *, actor_id, request_key) -> AppendResult(row, journal_id,
                       already_written); find_free_row, name_conflicts, row_payload, sheet_number; SHEET_WRITE_LOCK_KEY;
                       ошибки WriteRefusedError > HeaderDriftError, DuplicateNameError(row), WriteNotConfirmedError,
                       SheetBusyError (тексты — для повара)
sync/drive.py          DriveClient (REST на AuthorizedSession с таймаутами): upload_jpeg, download(max_bytes), trash;
                       explain_drive_error (quota/forbidden/not_found/unavailable/bad_reply), folder_is_closed
sync/cycle.py          SyncCycle.run(*, force=False, books=None)
db/models.py           SheetWrite, LlmCall, CardDraft;  db/journal.py, db/drafts.py
llm/polza.py           PolzaClient.vision_json(...) -> LlmReply(text, model, cost_rub: Decimal|None, tokens, duration_ms)
llm/label.py           LABEL_PROMPT_VERSION, LABEL_SYSTEM_PROMPT, LabelExtraction (pydantic), parse_label_reply, LabelReader
llm/budget.py          ensure_allowed(budget в сутки по МСК, лимит на повара), record_call
cards/drafts.py, cards/recognize.py, cards/submit.py  — слой приложения; web/cards.py, web/csrf.py — тонкие ручки
alembic: 20261001_1000_zhurnal_zapisi.py, _1100_vyzovy_modeli.py, _1200_chernoviki_kartochek.py
scripts/check_cards_setup.py; docs/adr/0003-platforma-pishet-v-tablitsu.md
```

**Таблицы:** `sheet_writes` (book, sheet, row, action 'append', status pending/verified/rolled_back/failed, request_key с частичным уникальным индексом по открытым, actor_id FK profiles SET NULL, before jsonb, values jsonb, content_hash, error, created_at, finished_at); `llm_calls` (created_at, purpose, model, prompt_version, profile_id, ok, error, cost_rub Numeric(12,4), tokens, duration_ms); `card_drafts` (id uuid, owner_id FK CASCADE, status active/submitted/cancelled, step, текстовые поля карточки, protein/fat/carbs/kcal Numeric(12,3), approval «Да»/«Отбракован», четыре file_id, recognition_status/started_at, recognition jsonb, recognition_warnings jsonb, submitted_row/at, sheet_write_id FK, created/updated; частичный уникальный индекс `(owner_id) WHERE status='active'`). У каждого FK — индекс.

**API** (`/api/cards/*`, роли cook/chef/developer): `GET options` (категории, поставщики), `GET name-check?name=` (точные/похожие карточки, в т.ч. скрытые; точные/похожие из справочника), `GET drafts/current`, `POST drafts` (409 если есть активный), `PATCH drafts/{id}` (частичное; 422 с именем поля), `DELETE drafts/{id}`, `PUT/DELETE drafts/{id}/photos/{kind}` (multipart; 413, 415, 409 без поставщика/названия, 502 Drive), `GET drafts/{id}/photos/{kind}` (прокси, `private`, `nosniff`), `POST recognize/{draft_id}` (409 идёт/нет этикетки, 429 бюджет/лимит, 502 модель, 503 не настроено), `POST drafts/{id}/submit` (→ `{row, already_written, imported, name}`; 422 недостаёт, 409 дубль «уже есть в таблице — строка N», 503 шапка/таблица занята, 502 Google «черновик сохранён»). Числа — строками, как во всём API.

**Фронтенд:** `api/types.ts`, `api/kartochki.ts`, `domain/foto.ts` (`razmerPosleUmensheniya`, `umenshitFoto`, `FotoNeOtkrylos`), `ui/FotoVybor.tsx`, `pages/NovyiIngredient.tsx`, `pages/kartochka/Shag*.tsx`, `kartochka.css`; `shell/razdely.ts` — у раздела `roli`; `Nav` фильтрует; `App.tsx` — `/cards`, для одной роли `cook` главная — `/cards`. Шаги: поставщик → категория → название → этикетка (камера/галерея, уменьшение, загрузка, автораспознавание) → проверка (11 полей, замечания, «Переснять», «Распознать ещё раз») → «согласован?» (Отбракован → сразу итог) → три фото (необязательны) → описание → итог «Отправить в таблицу» → «Записано в строку N» и «Добавить ещё». «Шаг N из 9», кнопки от 44 px, одна колонка. Таймауты: загрузка 60 с (два автоповтора тем же Blob), распознавание 180 с (опрос черновика каждые 3 с, пока `running`), отправка 60 с.

**Настройки** (`config.py`, `.env.example`): `drive_cards_folder_id`, `drive_scope` («drive» | «drive.file»), `llm_vision_model` (`qwen/qwen3.6-plus`), `llm_vision_timeout_seconds` (60), `llm_label_calls_per_user_daily` (40). **nginx:** `location /api/cards/recognize/` с `limit_req zone=llm burst=3 nodelay`, `limit_req_status 429`, `proxy_read_timeout 180s`, без `add_header`.

---

## 5. Риски

- **Два повара разом** — сериализует блокировка писателей; второй читает лист после первого. Ждать дольше 30 с — «Таблица занята, попробуйте ещё раз».
- **Шеф правит лист в ту же секунду** — в окне между чтением и записью (около секунды) его ячейка в целевой строке может быть перезаписана; окно описано в ADR, след — в журнале; после записи расхождение ловит проверка, откатываются только наши ячейки.
- **Сдвиг колонок** — писатель отказывает тем же правилом, что импорт; черновик остаётся.
- **Drive:** квота/доступ/API не включён — понятные ошибки, выявляются скриптом в задаче 4; «сироты» при неудачной корзине — только в журнал.
- **Модель:** таймаут и мусор → ручной ввод; бюджет исчерпан → ручной ввод; инъекция через этикетку — только строки ограниченной длины, запись RAW (формулы не исполняются), ответ не идёт в следующий промпт.
- **Фото с iPhone (HEIC)** — Safari обычно отдаёт JPEG; если браузер не открыл фото — сообщение про формат; проверяется на живом телефоне.
- **Мобильная сеть** — загрузка повторяется тем же Blob, отправка идемпотентна по `request_key`, распознавание переживает сон телефона.
- **Транзакция открыта во время запросов к Google** (до ~6 с) — проверить `idle_in_transaction_session_timeout` (задача 0).
- **CSRF при выкладке** — если `CORS_ORIGINS` пуст, свой origin проходит по `Host`; выход и продление сессии проверить на приёмке.

## 6. Проверка

1. Каждая задача: полный набор бэкенда и/или фронтенда зелёный; интеграция — CI (два прогона подряд для задач с записью); миграции вверх-вниз-вверх на непустой базе; мутации краснят свои тесты.
2. PR: CI 8/8, `lint-imports` с новыми слоями, mypy strict для новых пакетов, покрытие ≥ 85 %, сборка nginx с новым блоком; ревью с линзами sheets-guard, domain-guard, migration-guard, infra-auditor.
3. Сервер: смоук `deploy.sh` (воркер жив), `/healthz` с хешем, `verify_golden.py` 129/130, `check_cards_setup.py --llm` OK, живая проверка по задаче 12 вместе с Александром и поварами.
4. ROADMAP: «ADR-0003 (первая ступень)» и «Карточки по этикетке» — в сделанное только со строкой доказательства.
