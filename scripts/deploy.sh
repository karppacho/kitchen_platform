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
COMPOSE="docker compose -f infra/docker-compose.yml"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }

PREVIOUS_REF="$(git rev-parse HEAD)"

rollback() {
  log "Откат на ${PREVIOUS_REF:0:8}"
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
$COMPOSE exec -T postgres pg_dump -U postgres postgres | gzip > "$DUMP" \
  || fail "не снялся дамп — деплой остановлен"
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

printf '\n\033[32mГотово. Версия %s\033[0m\n' "${TARGET_REF:0:8}"
