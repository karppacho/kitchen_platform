#!/usr/bin/env bash
#
# Выпустить сертификат Let's Encrypt и поднять nginx с TLS.
#
# Запускается НА сервере, один раз:
#
#     ./scripts/setup_tls.sh
#
# Требует, чтобы A-запись домена уже указывала на этот сервер: Let's Encrypt
# проверяет владение, обращаясь по нему извне. Скрипт это проверяет первым
# делом и отказывается работать вслепую — иначе упрётся в лимит неудачных
# попыток, а он у Let's Encrypt строгий.
#
# Курица и яйцо: nginx не стартует без файлов сертификата, а сертификат не
# получить без работающего nginx. Решается временным самоподписанным:
# nginx поднимается на нём, доказывает, что отдаёт acme-challenge, после
# чего заглушка удаляется и certbot выпускает настоящий.
#
# Удалять заглушку до certbot обязательно: в live/<домен>, созданный не им,
# certbot выпускать отказывается («live directory exists for <домен>»,
# certbot/_internal/storage.py, new_lineage). nginx при этом не страдает —
# сертификат он уже прочитал в память при старте.
#
# Всё пишется в хостовый /etc/letsencrypt: именно его nginx монтирует
# (infra/docker-compose.yml), и туда же пишет cron продления.

set -Eeuo pipefail

DOMAIN="${DOMAIN:-art.karppacho.ru}"
EMAIL="${CERTBOT_EMAIL:-karppacho123@yandex.ru}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="docker compose -f $REPO_DIR/infra/docker-compose.yml"
LIVE="/etc/letsencrypt/live/$DOMAIN"
RENEWAL="/etc/letsencrypt/renewal/$DOMAIN.conf"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
log "1/6  Проверка DNS"
resolved="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
[ -n "$resolved" ] || fail "$DOMAIN не резолвится. Нужна A-запись на этот сервер."

mine="$(curl -s --max-time 10 https://api.ipify.org || true)"
printf '  %s → %s\n' "$DOMAIN" "$resolved"
printf '  этот сервер → %s\n' "${mine:-неизвестно}"
if [ -n "$mine" ] && [ "$resolved" != "$mine" ]; then
  fail "домен указывает не на этот сервер — Let's Encrypt проверку не пройдёт"
fi

# ---------------------------------------------------------------------------
log "2/6  Временный самоподписанный сертификат"
# Нужен только чтобы nginx смог стартовать и отдать ACME-проверку.
# Признак настоящего сертификата — renewal/<домен>.conf: его создаёт только
# certbot. Если его нет, всё, что лежит в live/<домен>, — заглушка от
# прошлого запуска, её пересоздаём.
sozdat_zaglushku() {
  sudo mkdir -p "$LIVE"
  sudo openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout "$LIVE/privkey.pem" -out "$LIVE/fullchain.pem" \
    -subj "/CN=$DOMAIN" 2>/dev/null
}

if sudo test -f "$RENEWAL"; then
  ZAGLUSHKA=0
  echo "  настоящий сертификат уже выпущен, заглушка не нужна"
else
  ZAGLUSHKA=1
  sudo rm -rf "$LIVE"
  sozdat_zaglushku
  echo "  создан (на сутки, будет удалён перед запросом настоящего)"
fi

# ---------------------------------------------------------------------------
log "3/6  Поднимаю nginx и проверяю acme-challenge"
$COMPOSE up -d nginx || fail "nginx не поднялся — смотрите вывод compose выше"
WEBROOT="$(docker volume inspect kitchen-platform_certbot-webroot -f '{{.Mountpoint}}')"

# Тестовый файл кладём туда же, куда его положит certbot, и забираем по 80
# порту через nginx. Молчание здесь — не норма: без ответа certbot пойдёт
# в заведомо проваленную проверку и потратит попытку из лимита Let's Encrypt.
PROBA="proverka-$(date +%s)-$$"
sudo mkdir -p "$WEBROOT/.well-known/acme-challenge"
echo "$PROBA" | sudo tee "$WEBROOT/.well-known/acme-challenge/$PROBA" > /dev/null

for attempt in $(seq 1 30); do
  # grep без -q: с -q он выходит на первом совпадении, compose может
  # получить SIGPIPE, и pipefail засчитал бы провал при запущенном nginx.
  if $COMPOSE ps --status running --services | grep -x nginx > /dev/null \
    && [ "$(curl -fsS --max-time 5 --resolve "$DOMAIN:80:127.0.0.1" \
         "http://$DOMAIN/.well-known/acme-challenge/$PROBA" 2>/dev/null || true)" = "$PROBA" ]; then
    echo "  nginx запущен и отдаёт тестовый файл"
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    sudo rm -f "$WEBROOT/.well-known/acme-challenge/$PROBA"
    $COMPOSE logs --tail 30 nginx || true
    fail "nginx не запущен или не отдаёт /.well-known/acme-challenge/ по 80 порту за 30 секунд"
  fi
  sleep 1
done
sudo rm -f "$WEBROOT/.well-known/acme-challenge/$PROBA"

# ---------------------------------------------------------------------------
log "4/6  Убираю заглушку"
if [ "$ZAGLUSHKA" -eq 1 ]; then
  sudo rm -rf "$LIVE" "/etc/letsencrypt/archive/$DOMAIN" "$RENEWAL"
  echo "  удалена: certbot не выпускает в live/, созданный не им"
else
  echo "  заглушки нет, пропускаю"
fi

# ---------------------------------------------------------------------------
log "5/6  Запрашиваю сертификат"
# --webroot: файл проверки кладётся в общий с nginx каталог, останавливать
# сервер не нужно.
if ! sudo docker run --rm \
  -v /etc/letsencrypt:/etc/letsencrypt \
  -v "$WEBROOT":/var/www/certbot \
  certbot/certbot:latest certonly \
  --webroot -w /var/www/certbot \
  -d "$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos --no-eff-email \
  --non-interactive \
  --force-renewal; then
  # Без файлов nginx не переживёт ни перезапуска, ни следующей выкладки.
  if [ "$ZAGLUSHKA" -eq 1 ]; then
    sozdat_zaglushku
    echo "  заглушка возвращена, чтобы nginx не остался без файлов"
  fi
  fail "certbot не выдал сертификат — смотрите вывод выше"
fi

# ---------------------------------------------------------------------------
log "6/6  Перечитываю nginx"
$COMPOSE exec -T nginx nginx -t || fail "конфигурация nginx не проходит проверку"
$COMPOSE exec -T nginx nginx -s reload

echo
printf '\033[32mГотово. https://%s\033[0m\n' "$DOMAIN"
echo
echo "Продление: сертификат живёт 90 дней. Добавьте в crontab root (sudo crontab -e):"
echo "  0 3 * * 1 docker run --rm -v /etc/letsencrypt:/etc/letsencrypt \\"
echo "      -v \$(docker volume inspect kitchen-platform_certbot-webroot -f '{{.Mountpoint}}'):/var/www/certbot \\"
echo "      certbot/certbot renew --webroot -w /var/www/certbot --quiet \\"
echo "      && docker compose -f $REPO_DIR/infra/docker-compose.yml exec -T nginx nginx -s reload"
