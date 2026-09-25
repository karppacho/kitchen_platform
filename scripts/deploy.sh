#!/usr/bin/env bash
#
# Выкладка на сервер. Запускается НА сервере, одной командой:
#
#     ./scripts/deploy.sh
#
# Почему это скрипт, а не набор команд в голове — два инцидента:
#
#   19.08.2026  после `git pull` забыли `systemctl restart`, и процесс
#               продолжил работать со старым кодом. Здесь перезапуск —
#               обязательный шаг, пропустить его нельзя.
#   17.08.2026  многострочная команда, отправленная с Windows-машины,
#               приехала с CRLF, bash порвал кавычки и запустил лишний
#               процесс на бою. Здесь вызов — одно слово.
#
# Свойства: дамп базы снимается ВСЕГДА до миграций; при провале смоука
# происходит откат на предыдущий тег.

set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

BACKUP_DIR="${BACKUP_DIR:-/var/backups/kitchen-platform}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/healthz}"
# Домен, под которым nginx отдаёт сайт: смоук ходит через него, а не мимо.
DOMAIN="${DOMAIN:-art.karppacho.ru}"
COMPOSE="docker compose -f infra/docker-compose.yml"
# База живёт не в нашем compose, а в стеке Supabase (infra/supabase/README.md):
# сервис `db`, контейнер `supabase-db` — так он назван в официальном
# compose Supabase. Переменная — на случай, если upstream его переименует.
DB_CONTAINER="${DB_CONTAINER:-supabase-db}"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }

PREVIOUS_REF="$(git rev-parse HEAD)"
# Запускались ли уже новые контейнеры — ставится перед `up -d` шага 5.
# Начальное значение обязательно: под set -u откат после упавшей сборки
# оборвался бы на чтении незаданной переменной.
NOVYE_ZAPUSHCHENY=0

rollback() {
  log "Откат на ${PREVIOUS_REF:0:8}"
  # Воркер нового кода останавливаем, пока compose на диске ещё новый и
  # воркер в нём описан: `up -d` ниже поднимает то, что есть в откаченном
  # compose, и воркер, которого там в выкладке нет, не тронет — новый код
  # писал бы в базу при откаченном сайте, а падающий на старте крутился бы
  # по кругу. Если воркер в откаченном compose есть, `up -d` поднимет его уже
  # со старым кодом. `|| true`: сбой остановки не должен оборвать откат
  # (set -e) раньше, чем откатится код.
  #
  # Только если новые контейнеры уже запускались. До шага 5 работает старый
  # здоровый воркер, а откат после упавшей сборки или миграций может упасть
  # на пересборке по той же причине (реестр пакетов недоступен) — и воркер
  # остался бы остановленным насовсем: `unless-stopped` после ручной
  # остановки не поднимает и перезагрузка.
  if [[ "$NOVYE_ZAPUSHCHENY" == 1 ]]; then
    $COMPOSE stop worker || true
  fi
  git reset --hard "$PREVIOUS_REF"
  $COMPOSE up -d --build
  printf '\nОткат выполнен. Дамп базы лежит в %s — миграции при необходимости\n' "$BACKUP_DIR"
  printf 'откатывать вручную: автоматический downgrade на боевой базе опаснее,\n'
  printf 'чем осознанное решение человека.\n'
}

# ---------------------------------------------------------------------------
log "1/6  Обновление кода"
# --ff-only: если на сервере оказались локальные правки, лучше остановиться,
# чем молча их потерять в merge.
git pull --ff-only || fail "git pull не прошёл — на сервере есть расхождение с origin"
TARGET_REF="$(git rev-parse HEAD)"
printf 'Было %s → стало %s\n' "${PREVIOUS_REF:0:8}" "${TARGET_REF:0:8}"

if [[ "$PREVIOUS_REF" == "$TARGET_REF" ]]; then
  log "Нечего выкладывать: коммит тот же"
fi

# ---------------------------------------------------------------------------
log "2/6  Дамп базы"
# Всегда и до миграций. Бэкап, снятый после — бесполезен.
mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/pre-deploy-$(date +%Y%m%d-%H%M%S)-${TARGET_REF:0:8}.sql.gz"
docker ps --format '{{.Names}}' | grep -x "$DB_CONTAINER" > /dev/null \
  || fail "контейнер базы «$DB_CONTAINER» не найден — проверьте DB_CONTAINER"
# pipefail (set выше) обязателен: без него статус конвейера — статус gzip,
# и упавший pg_dump дал бы «успешный» полупустой архив. Недоделанный файл
# удаляем, чтобы его не приняли за бэкап.
docker exec "$DB_CONTAINER" pg_dump -U postgres postgres | gzip > "$DUMP" \
  || { rm -f "$DUMP"; fail "не снялся дамп — деплой остановлен"; }
printf 'Дамп: %s (%s)\n' "$DUMP" "$(du -h "$DUMP" | cut -f1)"

# ---------------------------------------------------------------------------
log "3/6  Сборка"
$COMPOSE build || { rollback; fail "сборка не прошла"; }

# ---------------------------------------------------------------------------
log "4/6  Миграции"
# Правило: сначала совместимая схема, потом код. Миграция обязана быть
# безопасной для ещё работающего старого процесса.
$COMPOSE run --rm api alembic upgrade head || { rollback; fail "миграции не прошли"; }

# ---------------------------------------------------------------------------
log "5/6  Перезапуск"
# Флаг — до `up -d`, а не после: частично упавший `up -d` мог успеть поднять
# новый воркер, и откату нужно его остановить (см. rollback).
NOVYE_ZAPUSHCHENY=1
APP_VERSION="$TARGET_REF" $COMPOSE up -d || { rollback; fail "контейнеры не поднялись"; }

# ---------------------------------------------------------------------------
log "6/6  Смоук"
for attempt in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" > /tmp/health.json 2>/dev/null; then
    break
  fi
  [[ $attempt -eq 30 ]] && { rollback; fail "/healthz не ответил за 30 секунд"; }
  sleep 1
done

RUNNING_VERSION="$(python3 -c 'import json,sys; print(json.load(open("/tmp/health.json")).get("version",""))')"
if [[ "$RUNNING_VERSION" != "$TARGET_REF" ]]; then
  rollback
  fail "на сервере версия $RUNNING_VERSION, ожидалась $TARGET_REF — код не перезапустился"
fi

# /healthz выше ходит на 127.0.0.1:8080 — мимо nginx и пройдёт при мёртвом
# nginx. Сайт для шефа — это nginx: статика, TLS и проксирование /api/.
# Проверяем все три тем же путём, что и браузер, только без внешней сети:
# --resolve направляет имя домена на 127.0.0.1, SNI и Host остаются
# настоящими.
#
# -k — только пока сертификат может быть самоподписанным (заглушка из
# scripts/setup_tls.sh до выпуска настоящего). Здесь проверяется, что
# nginx отвечает, а не что Let's Encrypt выдал сертификат.
proverit_nginx() {
  local stranica code
  # grep без -q: с -q он выходит на первом совпадении, compose может
  # получить SIGPIPE, и pipefail засчитал бы провал при запущенном nginx.
  if ! $COMPOSE ps --status running --services | grep -x nginx > /dev/null; then
    PRICHINA="nginx не запущен"
    return 1
  fi
  # Страницу — в переменную, а не curl | grep -q: по той же причине
  # SIGPIPE ранний выход grep уронил бы конвейер.
  stranica="$(curl -fsSk --max-time 5 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" 2>/dev/null || true)"
  if ! grep -q 'id="root"' <<< "$stranica"; then
    PRICHINA="nginx не отдаёт index.html"
    return 1
  fi
  # Без куки API обязан ответить 401. Любой другой код (502 от nginx,
  # 200 от SPA-фолбэка, 000 — нет соединения) значит, что /api/ до API
  # не доходит.
  code="$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' \
    --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/me" || true)"
  if [[ "$code" != 401 ]]; then
    PRICHINA="/api/ не доходит до API ($code)"
    return 1
  fi
}

PRICHINA=""
for attempt in $(seq 1 30); do
  if proverit_nginx; then
    break
  fi
  [[ $attempt -eq 30 ]] && { rollback; fail "$PRICHINA — проверка через nginx не прошла за 30 секунд"; }
  sleep 1
done

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

printf '\n\033[32mГотово. Версия %s\033[0m\n' "${TARGET_REF:0:8}"
