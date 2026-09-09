# Supabase self-hosted

Своей копии compose-файла Supabase мы не держим: он большой, меняется от
релиза к релизу, и поддерживать форк дороже, чем накладывать оверлей.
Официальный файл берётся как есть, наши решения — в
`docker-compose.override.yml` рядом.

**Развёрнуто и проверено на боевом сервере 09.09.2026.** Всё описанное ниже
выполнялось, а не предполагалось.

## Что поднимаем

Шесть сервисов из одиннадцати. Стек занимает **581 МБ**.

| Сервис | | Память | Почему |
|---|---|---|---|
| `db` | да | 81 МБ | Postgres 17.6 |
| `supavisor` | да | 167 МБ | пулер |
| `auth` (GoTrue) | да | 10 МБ | учётки, сессии, JWT |
| `api-gw` | да | 20 МБ | шлюз на Envoy |
| `studio` | да | 199 МБ | «открыть и посмотреть данные» |
| `meta` | да | 104 МБ | нужен Studio |
| `rest` (PostgREST) | **нет** | | API у нас на FastAPI, см. [ADR-0002](../../docs/adr/0002-supabase-kak-hranilische-a-ne-bekend.md) |
| `realtime` | нет | | не используется |
| `storage` + `imgproxy` | нет | | фото на Google Drive: ссылки уже в таблице шефа |
| `functions` | нет | | не используется |

Ни один из оставленных не зависит от выключенных, так что правок
`depends_on` не потребовалось.

## Три места, на которых легко обжечься

Все три встретились при первой установке.

**1. Шлюз — Envoy, а не Kong.** Сервис называется `api-gw`. Kong вынесен в
отдельный необязательный `docker-compose.kong.yml`. Инструкции из интернета,
где фигурирует сервис `kong`, к текущему upstream не подходят.

**2. `COMPOSE_FILE` в `.env` отключает оверлей.** По умолчанию там
`COMPOSE_FILE=docker-compose.yml`, и пока значение одно, файл
`docker-compose.override.yml` **молча игнорируется** — compose подхватывает
его автоматически только когда переменная не задана. Симптом коварный:
`docker compose config` отрабатывает без ошибок, показывая конфигурацию
вообще без наших правок. Обязательно:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml
```

**3. Порты нельзя ограничить переменными окружения.** Соблазн написать
`POSTGRES_PORT=127.0.0.1:5432` велик, но эта переменная уходит внутрь
контейнера как `PGPORT` и подставляется во все строки подключения. База
падает в цикле с `FATAL: invalid value for parameter "port"`. Привязка к
петле делается **только** в оверлее, и обязательно с тегом `!override`:
списки портов при слиянии дописываются, и без тега рядом с нашей привязкой
осталась бы исходная `0.0.0.0`.

## Установка

```bash
cd /home/artem
git clone -q --depth 1 --filter=blob:none --sparse https://github.com/supabase/supabase supabase-src
cd supabase-src && git sparse-checkout set docker && cd ..
cp -r supabase-src/docker supabase && rm -rf supabase-src
cd supabase && cp .env.example .env
```

Заполнить `.env`. **Все значения по умолчанию публично известны** — менять
обязательно: `POSTGRES_PASSWORD`, `JWT_SECRET`, `ANON_KEY`,
`SERVICE_ROLE_KEY`, `DASHBOARD_PASSWORD`, `SECRET_KEY_BASE`, `VAULT_ENC_KEY`,
`PG_META_CRYPTO_KEY`, `REALTIME_DB_ENC_KEY`, `S3_PROTOCOL_*`.

`ANON_KEY` и `SERVICE_ROLE_KEY` — не случайные строки, а JWT, подписанные
тем же `JWT_SECRET`, с полем `role` равным `anon` и `service_role`. Мусор
вместо них не пройдёт: GoTrue и Studio проверяют подпись.

Дальше положить оверлей, поправить `COMPOSE_FILE` и поднять:

```bash
cp /home/artem/kitchen_platform/infra/supabase/docker-compose.override.yml .
docker compose up -d
docker compose ps
```

## Подключение к базе

Прямой доступ к Postgres наружу не публикуется вовсе. Ходить через пулер:

| Порт | Режим | Кто ходит |
|---|---|---|
| 6543 | transaction | приложение |
| 5432 | session | Alembic — миграции держат одну сессию от начала до конца |

**Имя пользователя обязано нести идентификатор арендатора:**
`postgres.<POOLER_TENANT_ID>`. Без суффикса Supavisor отвечает
`FATAL: (ENOIDENTIFIER) no tenant identifier provided`.

Контейнеры приложения живут в той же сети и ходят по именам сервисов —
`supavisor:6543` и `db:5432`, host-порты им не нужны вовсе.

## Проверка

```bash
docker compose ps --services            # ничего лишнего не поднялось
ss -tlnp | grep -vE '127\.0\.0\.1|\[::1\]'   # снаружи слушает только sshd
nmap -Pn <IP сервера>                   # с ДРУГОЙ машины: только 22, 80, 443
```

Последняя проверка делается с другой машины, а не с сервера: локальный `ss`
покажет привязку, но не докажет, что фильтрация работает.

## Studio

Наружу не публикуется никогда: за ним полный доступ к базе, а защита — одна
пара логин/пароль. Доступ с машины разработчика:

```bash
ssh -N -L 8000:127.0.0.1:8000 artem@<IP>
# затем http://127.0.0.1:8000
```

Пароль лежит на сервере в `/home/artem/supabase/.dashboard-password` (0600).

## Бэкапы

В облаке это делалось само. Здесь — наше:

```bash
0 4 * * * cd /home/artem/supabase && docker compose exec -T db \
  pg_dump -U postgres postgres | gzip > /var/backups/kitchen-platform/nightly-$(date +\%F).sql.gz
```

**Восстановление обязано быть проверено.** Дамп, который ни разу не
разворачивали в пустую базу, бэкапом не является — это файл с надеждой.
