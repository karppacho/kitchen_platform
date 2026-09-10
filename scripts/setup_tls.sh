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
# получить без работающего nginx. Решается временным самоподписанным —
# он живёт минуту, ровно до подмены настоящим.

set -Eeuo pipefail

DOMAIN="${DOMAIN:-art.karppacho.ru}"
EMAIL="${CERTBOT_EMAIL:-karppacho123@yandex.ru}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="docker compose -f $REPO_DIR/infra/docker-compose.yml"
LIVE="/etc/letsencrypt/live/$DOMAIN"

log()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mОШИБКА: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
log "1/5  Проверка DNS"
resolved="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
[ -n "$resolved" ] || fail "$DOMAIN не резолвится. Нужна A-запись на этот сервер."

mine="$(curl -s --max-time 10 https://api.ipify.org || true)"
printf '  %s → %s\n' "$DOMAIN" "$resolved"
printf '  этот сервер → %s\n' "${mine:-неизвестно}"
if [ -n "$mine" ] && [ "$resolved" != "$mine" ]; then
  fail "домен указывает не на этот сервер — Let's Encrypt проверку не пройдёт"
fi

# ---------------------------------------------------------------------------
log "2/5  Временный самоподписанный сертификат"
# Нужен только чтобы nginx смог стартовать и отдать ACME-проверку.
if [ ! -f "$LIVE/fullchain.pem" ]; then
  sudo mkdir -p "$LIVE"
  sudo openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout "$LIVE/privkey.pem" -out "$LIVE/fullchain.pem" \
    -subj "/CN=$DOMAIN" 2>/dev/null
  echo "  создан (на сутки, будет заменён)"
else
  echo "  уже есть, пропускаю"
fi

# ---------------------------------------------------------------------------
log "3/5  Поднимаю nginx"
$COMPOSE up -d nginx
sleep 3
curl -fsS -o /dev/null "http://$DOMAIN/.well-known/acme-challenge/" 2>/dev/null \
  && echo "  каталог проверки отвечает" \
  || echo "  каталог проверки пуст — это нормально, файла там пока нет"

# ---------------------------------------------------------------------------
log "4/5  Запрашиваю сертификат"
# --webroot: файл проверки кладётся в общий с nginx каталог, останавливать
# сервер не нужно.
sudo docker run --rm \
  -v /etc/letsencrypt:/etc/letsencrypt \
  -v "$(docker volume inspect kitchen-platform_certbot-webroot -f '{{.Mountpoint}}')":/var/www/certbot \
  certbot/certbot:latest certonly \
  --webroot -w /var/www/certbot \
  -d "$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos --no-eff-email \
  --non-interactive \
  --force-renewal \
  || fail "certbot не выдал сертификат — смотрите вывод выше"

# ---------------------------------------------------------------------------
log "5/5  Перечитываю nginx"
$COMPOSE exec nginx nginx -t || fail "конфигурация nginx не проходит проверку"
$COMPOSE exec nginx nginx -s reload

echo
printf '\033[32mГотово. https://%s\033[0m\n' "$DOMAIN"
echo
echo "Продление: сертификат живёт 90 дней. Добавьте в crontab:"
echo "  0 3 * * 1 docker run --rm -v /etc/letsencrypt:/etc/letsencrypt \\"
echo "      -v \$(docker volume inspect kitchen-platform_certbot-webroot -f '{{.Mountpoint}}'):/var/www/certbot \\"
echo "      certbot/certbot renew --webroot -w /var/www/certbot --quiet \\"
echo "      && docker compose -f $REPO_DIR/infra/docker-compose.yml exec nginx nginx -s reload"
