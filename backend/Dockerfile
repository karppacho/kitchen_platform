# Образ бэкенда. Один и тот же для api и worker — различается только команда
# запуска, поэтому пересобирать и хранить два образа незачем.

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

WORKDIR /app


# ---------------------------------------------------------------------------
# Зависимости отдельным слоем: правка кода не должна тянуть переустановку
# полутора сотен пакетов при каждой сборке.
# ---------------------------------------------------------------------------
FROM base AS deps

COPY pyproject.toml uv.lock ./
# --frozen: ставим ровно то, что в локе. Разъезд между проверенным в CI и
# установленным на сервере — источник ошибок, которые невозможно повторить.
RUN uv sync --frozen --no-dev --no-install-project


# ---------------------------------------------------------------------------
FROM base AS runtime

# Не root. Если процесс скомпрометируют, у него не будет прав на систему.
#
# uid и gid закреплены числом намеренно. Ключ сервисного аккаунта лежит на
# хосте и монтируется внутрь; при плавающем uid (`--system` выдаёт из
# системного диапазона, у нас вышел 999) контейнер не может его прочитать.
# На хосте нужно отдать группе и файл, и каталог — без права прохода через
# каталог доступ к файлу не поможет:
#     chown artem:10001 /etc/kitchen-platform /etc/kitchen-platform/service_account.json
#     chmod 750 /etc/kitchen-platform
#     chmod 640 /etc/kitchen-platform/service_account.json
# так владельцем остаётся человек, а читать может процесс.
RUN groupadd --gid 10001 kitchen \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/kitchen kitchen

COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

COPY --chown=kitchen:kitchen pyproject.toml uv.lock ./
COPY --chown=kitchen:kitchen src ./src
COPY --chown=kitchen:kitchen alembic ./alembic
COPY --chown=kitchen:kitchen alembic.ini ./alembic.ini
# Эксплуатационные скрипты (отчёт по таблицам, сверка) едут в образ: их
# запускают на сервере, а значит они должны быть там же, где зависимости.
COPY --chown=kitchen:kitchen scripts ./scripts

USER kitchen

# Проставляется сборкой из хеша коммита и отдаётся в /healthz: по нему
# смоук деплоя понимает, что процесс действительно перезапустился.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

EXPOSE 8080

# Порт слушается внутри сети docker; наружу его публикует только nginx.
CMD ["uvicorn", "kitchen.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
