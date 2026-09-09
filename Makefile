# Единая точка запуска проверок. Тем же самым пользуется CI — если что-то
# зелёное локально, оно зелёное и в PR, расхождения между «у меня работало»
# и раннером быть не может.
#
# На Windows make обычно не установлен: команды из каждой цели можно
# выполнять как есть, они однострочные. См. CONTRIBUTING.md.

BACKEND := backend
PY := $(BACKEND)/.venv/bin/python

.PHONY: help install lint format types arch test test-live test-int cov eval e2e check deploy

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Поставить зависимости (uv) и хуки pre-commit
	cd $(BACKEND) && uv sync --all-extras --dev
	pre-commit install

lint:  ## ruff: проверка и формат
	cd $(BACKEND) && uv run ruff check .
	cd $(BACKEND) && uv run ruff format --check .

format:  ## ruff: починить что чинится
	cd $(BACKEND) && uv run ruff check --fix .
	cd $(BACKEND) && uv run ruff format .

types:  ## mypy (strict в domain/sync)
	cd $(BACKEND) && uv run mypy src

arch:  ## import-linter: слои не перепутаны
	cd $(BACKEND) && uv run lint-imports

test:  ## Офлайн-тесты (без сети и без БД)
	cd $(BACKEND) && uv run pytest

test-int:  ## Тесты, которым нужен поднятый Postgres
	cd $(BACKEND) && uv run pytest -m integration

test-live:  ## Живые тесты: настоящие Sheets, нужны креды
	cd $(BACKEND) && uv run pytest -m live

cov:  ## Покрытие с порогом на ядре. Нужен поднятый Postgres: часть кода
      ## осмысленно покрывается только интеграционными тестами
	cd $(BACKEND) && uv run pytest --cov=kitchen.domain --cov=kitchen.sync --cov-report=
	cd $(BACKEND) && uv run pytest -m integration --cov=kitchen.domain --cov=kitchen.sync --cov-append --cov-report=term-missing --cov-fail-under=85

eval:  ## LLM-евалы. Стоят денег, в CI не входят
	cd $(BACKEND) && uv run pytest -m llm

e2e:  ## Браузерные сценарии
	cd $(BACKEND) && uv run pytest -m e2e

check: lint types arch test  ## Всё, что блокирует PR. Цель — уложиться в 5 минут

deploy:  ## Выкладка на сервер. Запускается НА сервере, одной командой
	./scripts/deploy.sh
