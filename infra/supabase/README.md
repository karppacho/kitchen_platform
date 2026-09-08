# Supabase self-hosted

Мы не держим свою копию compose-файла Supabase: он большой, меняется от
релиза к релизу, и поддерживать форк дороже, чем накладывать оверлей.
Официальный файл берётся как есть, а наши решения живут в
`docker-compose.override.yml` рядом.

**Ничего из описанного здесь не проверено на боевом сервере** — на машине
разработчика нет docker. Первый прогон делается по шагам ниже, и результат
дописывается в конец файла.

## Что поднимаем и что нет

| Сервис | | Почему |
|---|---|---|
| `db` | да | Postgres |
| `supavisor` | да | пулер: приложение ходит через 6543 |
| `auth` (GoTrue) | да | учётки, сессии, JWT |
| `kong` | да | шлюз перед auth |
| `studio` + `meta` | да | «открыть и посмотреть данные» — ради этого и выбран Supabase |
| `rest` (PostgREST) | **нет** | API у нас на FastAPI, см. [ADR-0002](../../docs/adr/0002-supabase-kak-hranilische-a-ne-bekend.md) |
| `realtime` | нет | не используется |
| `storage` + `imgproxy` | нет | фото пока на Google Drive: ссылки на них уже лежат в таблице шефа |
| `functions` | нет | не используется |
| `analytics` + `vector` | нет | логи собираем через `journalctl`, а Logflare заметно ест память |

## Подводный камень

В официальном файле несколько сервисов объявляют `depends_on: analytics`
(и `vector`). Выключив analytics профилем, мы обязаны заменить эти
зависимости — иначе compose откажется стартовать, сославшись на
невыполнимое условие. Оверлей это и делает: `depends_on` при слиянии
**заменяется целиком**, а не дополняется, поэтому наши три строки полностью
вытесняют исходные.

Если после обновления Supabase появится новый сервис с такой зависимостью,
симптом будет тот же: `service "X" depends on undefined service`. Лечится
добавлением его в оверлей.

## Установка

```bash
cd /home/artem
git clone --depth 1 https://github.com/supabase/supabase supabase-src
mkdir -p supabase && cp -r supabase-src/docker/* supabase/
cd supabase
cp .env.example .env
```

Заполнить `.env`. Обязательно поменять всё, что похоже на пример:
`POSTGRES_PASSWORD`, `JWT_SECRET`, `ANON_KEY`, `SERVICE_ROLE_KEY`,
`DASHBOARD_USERNAME`, `DASHBOARD_PASSWORD`, `SECRET_KEY_BASE`,
`VAULT_ENC_KEY`. Значения по умолчанию из репозитория публично известны.

Положить оверлей и зафиксировать версии образов:

```bash
cp /home/artem/kitchen_platform/infra/supabase/docker-compose.override.yml .
# Плавающий latest здесь противопоказан: апгрейд self-hosted Supabase
# делается руками и не всегда бесшовно.
grep -n "image:" docker-compose.yml    # убедиться, что теги проставлены явно
docker compose up -d
docker compose ps
```

## Проверка

```bash
# Ничего лишнего не поднялось
docker compose ps --services

# Все порты на localhost. Ни одной строки с 0.0.0.0 быть не должно.
docker compose ps --format '{{.Names}}\t{{.Ports}}'
ss -tlnp | grep -E '5432|6543|8000'

# Снаружи видны только 22, 80, 443
nmap -Pn <IP сервера>
```

Последняя проверка — самая важная и делается **с другой машины**, а не с
сервера. Локальный `ss` покажет привязку, но не докажет, что фильтрация
работает.

## Studio

Наружу не публикуется. Доступ с машины разработчика:

```bash
ssh -N -L 8000:127.0.0.1:8000 artem@<IP>
# затем http://127.0.0.1:8000 в браузере
```

## Память

На сервере уже работают три Telegram-бота и Chrome с Xvfb для CDP-скрапинга
конкурентов; Chrome с полным профилем ест гигабайтами. Замерить **после**
подъёма стека и при работающем Chrome:

```bash
free -m
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}'
```

Если запаса нет, самый дешёвый размен — вынести Chrome и скрапинг на
отдельную машину с российским адресом (российский IP обязателен: «Вкусно и
точка» и Cofix гео-блокируют зарубежные адреса).

## Бэкапы

В облаке это делалось само. Здесь — наше:

```bash
# в crontab пользователя artem
0 4 * * * cd /home/artem/supabase && docker compose exec -T db \
  pg_dump -U postgres postgres | gzip > /var/backups/kitchen-platform/nightly-$(date +\%F).sql.gz
```

**Восстановление обязано быть проверено.** Дамп, который ни разу не
разворачивали в пустую базу, бэкапом не является — это файл с надеждой.

## Результаты первого прогона

_(заполнить после установки на сервере: что поднялось, сколько памяти
осталось, что пришлось поправить в оверлее)_
