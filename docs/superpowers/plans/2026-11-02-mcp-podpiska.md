# MCP по подписке — план

Спека: [2026-11-02-mcp-podpiska-design.md](../specs/2026-11-02-mcp-podpiska-design.md) — закон. Ветка `mcp-podpiska` (от `main` 2fbd1d1), дерево `kitchen_platform-mcp`. Образец кода — прототип ветки `mcp-razvedka` (97c6c4f): `backend/src/kitchen/mcp/server.py`, `db/ingredients.py`, монтирование в `web/app.py`, тесты `tests/unit|integration/test_mcp_tool.py`; его отчёт — `kitchen_platform-mcp-razvedka/.superpowers/sdd/2026-11-02-mcp-razvedka/report.md`. Переносить из прототипа можно и нужно (это наш код), но через TDD по этому плану.

## Global Constraints
- Исполнители — Sonnet 5.5; контроллер — главная сессия. Коммиты по-русски, последняя строка `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; **не пушить**.
- SDK: `mcp>=2.3.0` (API v2: `mcp.server.MCPServer`, `mcp.Client`); примеры под 1.x (`FastMCP`) не годятся.
- Правила 1, 3, 5 `CLAUDE.md` (см. «Неизменные правила» спеки); import-linter: слой `kitchen.mcp` между `kitchen.web | kitchen.worker` и `kitchen.cards`; `kitchen.domain` ничего нового не импортирует.
- Цены повару не уходят никогда — проверяется тестом на уровне данных ответа.
- Не трогать: `CLAUDE.md`, `.claude/agents/*`, фронтенд, ROADMAP (документы сводит контроллер в M5).
- Windows: файлы — Edit/Write, `git ls-files --eol` → `w/lf`; перед `uv` — `VIRTUAL_ENV=`.
- Репозиторий публичный: без названий сети и юрлица клиента, имён (кроме «Александр»), почт, id таблиц, адресов сервисного аккаунта.

## Как исполнять
- TDD: красный тест → код → зелёный; мутации — в отчёте.
- Полный набор: `bash <scratchpad>/check_backend.sh <дерево> <база>` (очередь тяжёлых прогонов, таймаут вызова 600000 мс).
- Живые клиенты (claude.ai, ChatGPT) — после возвращения сервера; локально — клиент SDK `mcp.Client` в памяти и по HTTP на 127.0.0.1.

## Задача M2a: роли вниз и каркас `kitchen.mcp`
**Файлы:** вынести из `web/auth.py` проверку пользователя и ролей (`CurrentUser`, загрузка профиля и ролей, разбор токена) в нижний модуль (например `kitchen/db/profiles.py` + `kitchen/auth_core.py` или как удобнее слоям) — `web/auth.py` остаётся тонкой обёрткой FastAPI, поведение и тексты ответов те же; `kitchen/mcp/__init__.py`, `server.py` (`build_mcp(sessions, settings)`, stateless Streamable HTTP, `TransportSecuritySettings` из настроек, регистрация `register_read_tools(server, deps)` и `register_card_tools(server, deps)` — заглушки в `tools_read.py`, `tools_cards.py`; проверка доступа — интерфейс в `auth.py` с временной реализацией «только локальный токен разработчика из настроек», пока нет OAuth); `config.py` — `mcp_enabled` (false), `mcp_allowed_hosts`, `mcp_dev_token` (SecretStr, пусто — выключено); `web/app.py` — монтирование последним и `session_manager.run()` в lifespan только при флаге; `pyproject.toml` — зависимость `mcp>=2.3.0`, слой import-linter; `Makefile` и `.github/workflows/ci.yml` — к `--cov=…` добавить `--cov=kitchen.mcp`.
**Тесты сначала:** флаг выключен — `/mcp` 404 (приложение как раньше); включён без токена — 401 с `WWW-Authenticate`; с токеном разработчика — `tools/list` по HTTP отдаёт заглушечный список; роли из нового модуля — те же, что в прежних тестах `web/auth` (все прежние тесты зелёные без правок ожиданий); `lint-imports` — 2 контракта kept.
**Мутации:** монтирование без флага; проверка токена пропущена; `web` обходит новый модуль ролей.
**Коммит:** «MCP: каркас сервера под флагом, роли вынесены из web/auth».

## Задача M2: инструменты чтения (`mcp/tools_read.py`)
После M2a. Инструменты спеки: `poisk_ingredienta`, `ingredient`, `spisok_blyud`, `blyudo`, `poschitat_sostav`, `sverka`, `svezhest` — с `readOnlyHint`. Данные — теми же функциями, что ручки `web/api.py`, `web/reconciliation.py`, `web/sync_status.py` (переиспользовать; если логика живёт в ручке — вынести вниз в `db/*` или `cards/*`, ручка зовёт вынесенное, ответы ручек не меняются ни на байт). `poschitat_sostav` — `domain.costs` по составу из запроса и справочнику базы; ничего не пишет. Деньги — строками, пусто — `null`.
**Тесты сначала:** каждый инструмент — офлайн (подмена чтения) и интеграция на фальшивых листах (`tests/fake_sheets.py` → `Importer`); повар не получает ни одного поля с ценой, себестоимостью или маржой ни в одном инструменте (перебором ответов); повару `spisok_blyud`, `blyudo`, `poschitat_sostav`, `sverka` — отказ «нужна роль»; `blyudo("B001")` на фикстуре совпадает с ответом ручки `/api/dishes/B001` по UC, составу и замечаниям; `poschitat_sostav` с тем же составом, что у блюда, даёт тот же UC.
**Мутации:** цена повару; инструмент пересчитал число сам (не через `domain.costs`); `readOnlyHint` снят.
**Коммит:** «MCP: инструменты чтения — справочник, блюда, расчёт состава, сверка».

## Задача M3: OAuth 2.1 (`mcp/auth.py`, база, страница входа)
После M2a, параллельно M2 и M4. Провайдер `OAuthAuthorizationServerProvider` SDK (`get_client`, `register_client`, `authorize`, `load_authorization_code`, `exchange_authorization_code`, `load_refresh_token`, `exchange_refresh_token`, `load_access_token`, `revoke_token`); миграция: `mcp_oauth_clients`, `mcp_oauth_codes`, `mcp_oauth_tokens` — токены, коды и refresh только хешами (sha256), сроки (код 10 мин, access 1 ч), признак использования кода, отзыв, ротация refresh; страница `/authorize` — серверная форма: вход логином и паролем тем же путём, что `POST /api/auth/login` (GoTrue), затем согласие («Подключить Claude/ChatGPT к платформе от имени …: читать справочник и блюда, создавать черновики карточек»), CSRF-токен формы; белый список `redirect_uri` (настройка); `sub` токена → пользователь → роли из M2a; замена временной проверки «токен разработчика» на проверку access-токена (токен разработчика остаётся только при `app_env=development`). Миграция — по `.claude/agents/migration-guard.md` (обратимость: upgrade → downgrade → upgrade).
**Тесты сначала:** полный поток код+PKCE (через TestClient): регистрация клиента, `/authorize` с входом (GoTrue — фальшивка, как в тестах `auth_api`), обмен кода, вызов `/mcp` с access, refresh с ротацией (старый refresh — отказ), отзыв; повторное использование кода — отказ; неверный `code_verifier` — отказ; чужой `redirect_uri` — отказ; истёкший access — 401; в базе нет ни одного токена в открытом виде.
**Мутации:** код принимается дважды; refresh не ротируется; токен хранится открыто; PKCE не проверяется.
**Коммит:** «MCP: свой OAuth 2.1 — DCR, PKCE, вход паролем сайта, токены хешами».

## Задача M4: черновик по этикетке и подтверждение пары (`mcp/tools_cards.py`)
После M2a, параллельно M2 и M3. `chernovik_po_etiketke(polya)`: вход — поля формы `LabelExtraction` (`llm/label.py`) строками; проверка той же схемой (длины, только строки); разбор `domain.cards.label_fields_from_extraction` и `domain.shelf_life`; запись в черновик пользователя через `cards/drafts.py` (создать, если нет; заменить поля этикетки, как это делает распознавание; фото не трогать); ответ — разобранные поля, замечания (КБЖУ, сроки) и ссылка на веб-мастер (`/cards`). Без `readOnlyHint`. Журнал `llm_calls` не пишется (модель — у пользователя). `eto_on(card_id, ingredient_id)` — тем же путём, что ручка `POST /api/reconciliation/{card_id}/confirm` (только база). Промпт MCP «Карточка по этикетке» — правила чтения из `llm/label.py` (вынести общий текст правил в одно место, чтобы не разошлись).
**Тесты сначала:** поля с «30|125 ккал» — замечание и пустое значение, как у распознавания; длинное поле — отказ схемой; черновик создаётся, повторный вызов заменяет поля этикетки, не трогая фото и прочие шаги; отправленный черновик — отказ, как в вебе; `eto_on` поваром — «нужна роль»; `eto_on` пишет пару только в базу (фальшивые листы не тронуты).
**Мутации:** число из ответа модели без разбора кодом; поля этикетки пишутся поверх фото; `eto_on` без проверки роли.
**Коммит:** «MCP: черновик карточки по тексту этикетки и «Это он»».

## Задача M5: nginx, документы, ADR
nginx: локации спеки, пределы, таймаут стрима; проверка L4 (сборка образа, `nginx -t`, curl: `/mcp` без токена — 401, `/.well-known/oauth-authorization-server` — 200 JSON). Документы: README «Как подключить Claude и ChatGPT» (шаги для шефа), ADR-0004, ссылка в `FRONTEND.md`; правки `CLAUDE.md` (правило 4, архитектура) — по согласию Александра.

## Проверка ветки (контроллер)
L1+L2 зелёные; L7: локальный api на `kp_mirror` с `MCP_ENABLED=true` — клиент SDK по HTTP проходит OAuth-поток и вызывает каждый инструмент от шефа и от повара; повар не видит цен. Живые клиенты — после возвращения сервера.
