# Сборка фронтенда и его раздача.
#
# Node живёт только здесь: на сервер он не ставится. Сборка привязана к
# коммиту, поэтому «какая версия интерфейса выложена» перестаёт быть
# вопросом — она та же, что у бэкенда.

FROM node:22-alpine AS sborka
WORKDIR /app

# Сначала манифесты, потом код: слой с зависимостями переживает правку
# исходников и не пересобирается на каждый коммит.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
# Конфиг не копируем: он подмонтирован томом из репозитория, и правка
# nginx не должна требовать пересборки образа.
COPY --from=sborka /app/dist /usr/share/nginx/html
