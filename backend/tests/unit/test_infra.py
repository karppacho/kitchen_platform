"""Периметр: что публикуется наружу.

У трёх Telegram-ботов не было ни одного входящего порта. У веба он есть —
это новая поверхность атаки, которой у проекта никогда не было.

Самый вероятный способ пострадать здесь — не изощрённая атака, а забытое
двоеточие: `"5432:5432"` вместо `"127.0.0.1:5432:5432"`. У self-hosted
Supabase пулер и шлюз по умолчанию публикуются на все интерфейсы, и на
машине с белым адресом их начинают сканировать в течение часов.

Поэтому правило проверяется тестом, а не внимательностью при код-ревью.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "infra" / "docker-compose.yml"
SUPABASE_OVERRIDE = REPO / "infra" / "supabase" / "docker-compose.override.yml"
NGINX_CONF = REPO / "infra" / "nginx" / "kitchen-platform.conf"
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

# Ниже этой версии в Node нет флага --no-experimental-webstorage, которым в
# frontend/vite.config.ts заглушено предупреждение при загрузке msw: процесс
# тестов падает с «bad option» ещё до первого теста. Найдено на живую при
# подготовке задачи 15.
MIN_NODE_VERSION = (22, 4)

# Единственное, чему положено смотреть наружу. Расширение этого множества —
# осознанное решение, которое обязано сопровождаться правкой UFW и внятным
# ответом на вопрос «зачем».
PUBLIC_ALLOWED: dict[str, set[str]] = {"nginx": {"80:80", "443:443"}}


class _ComposeLoader(yaml.SafeLoader):
    """Понимает теги Compose (`!override`, `!reset`).

    Обычный SafeLoader на них падает, а без них оверлей не написать: без
    `!override` списки портов дописываются вместо замены.
    """


def _passthrough(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", lambda loader, suffix, node: _passthrough(loader, node))


def _load(path: Path) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)


def _published(service: dict[str, Any]) -> list[str]:
    return [str(p) for p in (service.get("ports") or [])]


def _all_yaml_files() -> list[Path]:
    found: list[Path] = []
    for pattern in ("*.yml", "*.yaml"):
        found += [
            p
            for p in REPO.rglob(pattern)
            if ".venv" not in p.parts and "node_modules" not in p.parts
        ]
    return sorted(found)


@pytest.mark.parametrize("path", _all_yaml_files(), ids=lambda p: str(p.name))
def test_yaml_files_parse(path: Path) -> None:
    """Синтаксическая ошибка в конфиге обнаруживается здесь, а не на сервере."""
    yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)


def test_only_nginx_is_published_outside() -> None:
    compose = _load(COMPOSE)
    for name, service in compose["services"].items():
        for port in _published(service):
            if port.startswith("127.0.0.1:"):
                continue
            assert port in PUBLIC_ALLOWED.get(name, set()), (
                f"Сервис «{name}» публикует {port} на все интерфейсы. "
                f"Наружу смотрит только nginx на 80 и 443; всё остальное "
                f"привязывается к 127.0.0.1."
            )


def test_supabase_ports_are_local_only() -> None:
    """Пулер и шлюз не должны быть видны из интернета.

    Имена сервисов сверены с upstream 09.09.2026: шлюз называется `api-gw`
    и работает на Envoy, Kong вынесен в отдельный необязательный файл.
    `db` портов не публикует вовсе — доступ к Postgres идёт через пулер.
    """
    override = _load(SUPABASE_OVERRIDE)["services"]
    for name in ("supavisor", "api-gw"):
        ports = _published(override[name])
        assert ports, f"«{name}»: оверлей обязан переопределить порты, иначе останутся базовые"
        for port in ports:
            assert port.startswith("127.0.0.1:"), f"«{name}»: {port} смотрит наружу"


def test_port_overrides_replace_rather_than_append() -> None:
    """Тег !override обязателен, и это не стилистика.

    Списки портов при слиянии compose ДОПИСЫВАЮТСЯ. Без тега рядом с нашей
    привязкой к 127.0.0.1 осталась бы исходная 0.0.0.0 — то есть Postgres,
    открытый в интернет, при внешне правильном на вид оверлее.
    """
    port_lines = [
        line.strip()
        for line in SUPABASE_OVERRIDE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("ports:")
    ]
    assert port_lines, "в оверлее вообще нет переопределения портов"
    for line in port_lines:
        assert line == "ports: !override", (
            f"«{line}» — без тега !override привязка допишется к исходной 0.0.0.0, а не заменит её"
        )


def test_db_is_not_published() -> None:
    """Прямой доступ к Postgres наружу не публикуется совсем."""
    assert "db" not in _load(SUPABASE_OVERRIDE)["services"]


def test_unused_supabase_services_are_disabled() -> None:
    """То, чем не пользуемся, не поднимаем: это память, которой в обрез."""
    override = _load(SUPABASE_OVERRIDE)["services"]
    for name in ("rest", "realtime", "storage", "imgproxy", "functions"):
        assert override[name].get("profiles") == ["unused"], (
            f"«{name}» должен быть выключен профилем. PostgREST отдельно: "
            f"решение не использовать его принято в docs/adr/0002."
        )


def test_nginx_sobiraetsya_a_ne_tyanetsya() -> None:
    """Сборка фронтенда привязана к коммиту.

    Готовый образ nginx с примонтированной статикой означал бы, что
    выложенный фронтенд и выложенный бэкенд могут разъехаться по версиям:
    образ тот же, а содержимое тома — какое осталось с прошлого раза.
    """
    nginx = _load(COMPOSE)["services"]["nginx"]

    assert "build" in nginx, "статика собирается вместе с образом"
    assert nginx["build"]["dockerfile"] == "infra/nginx/Dockerfile"
    assert nginx["build"]["context"] == "..", "в контекст должны попасть и frontend, и infra"
    assert nginx["image"].startswith("kitchen-platform-nginx")


def _location_block_spans(text: str) -> list[tuple[int, int]]:
    """Отдаёт (начало, конец) тела каждого `location ... { ... }` в конфиге.

    Разбор по глубине фигурных скобок, а не по отступам: у nginx-конфига
    нет формального грамматического разбора под рукой, а отступы —
    условность форматирования, а не синтаксис. В этом файле location не
    вложены друг в друга и фигурных скобок в строках/комментариях нет —
    для него этого достаточно.
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    while True:
        match = re.search(r"location\b[^{}]*\{", text[pos:])
        if not match:
            break
        start = pos + match.end()
        depth = 1
        cursor = start
        while depth > 0:
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
            cursor += 1
        spans.append((start, cursor - 1))
        pos = cursor
    return spans


def test_location_blocks_have_no_add_header() -> None:
    """add_header внутри location тихо отменяет заголовки безопасности server.

    Живая ловушка: 22.09.2026 при добавлении Cache-Control для статики её
    едва не наступили заново — естественным решением казалось дописать
    `add_header Cache-Control` прямо в `location /assets/`. Живого nginx в
    CI нет, поэтому проверка текстовая.
    """
    text = NGINX_CONF.read_text(encoding="utf-8")
    spans = _location_block_spans(text)
    assert spans, "в конфиге не нашлось ни одного location — разбор сломан или файл пуст"
    for start, end in spans:
        body = text[start:end]
        assert "add_header" not in body, (
            f"add_header внутри location (символы {start}-{end}) отменит заголовки "
            f"безопасности, унаследованные от server"
        )


def test_api_location_exists() -> None:
    """SPA-фолбэк не должен молча проглотить /api/."""
    text = NGINX_CONF.read_text(encoding="utf-8")
    assert re.search(r"location\s+/api/\s*\{", text), "location /api/ пропал из конфига"


def test_cache_control_header_is_at_server_level() -> None:
    """Cache-Control объявлен один раз на server, а не раскидан по location.

    Если строку унесут внутрь какого-нибудь location, там тихо пропадут
    HSTS и CSP (см. test_location_blocks_have_no_add_header), а вне этого
    location политика кэша перестанет действовать вовсе.
    """
    text = NGINX_CONF.read_text(encoding="utf-8")
    server_level = text
    for start, end in sorted(_location_block_spans(text), reverse=True):
        server_level = server_level[:start] + server_level[end:]
    assert "add_header Cache-Control" in server_level, (
        "add_header Cache-Control не найден на уровне server (после вырезания "
        "всех location) — либо пропал совсем, либо спрятан внутри location"
    )


def test_secrets_are_not_mounted_writable() -> None:
    """Ключ сервисного аккаунта монтируется только на чтение."""
    compose = _load(COMPOSE)
    for name, service in compose["services"].items():
        for volume in service.get("volumes") or []:
            if "service_account" in str(volume):
                assert str(volume).endswith(":ro"), f"«{name}»: ключ Google смонтирован на запись"


def _load_ci() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _frontend_setup_node_step() -> dict[str, Any]:
    steps = _load_ci()["jobs"]["frontend"]["steps"]
    step = next((s for s in steps if str(s.get("uses", "")).startswith("actions/setup-node")), None)
    assert step is not None, "в задаче frontend нет actions/setup-node"
    return step


def test_frontend_ci_job_exists() -> None:
    """Фронтенд обязан проверяться в блокирующем CI, а не только на машине разработчика."""
    ci = _load_ci()
    assert "frontend" in ci["jobs"], "в .github/workflows/ci.yml нет задачи frontend"


def test_frontend_ci_uses_node_not_older_than_required() -> None:
    """См. MIN_NODE_VERSION: старый Node роняет тесты ещё до их запуска."""
    version = str(_frontend_setup_node_step()["with"]["node-version"])

    if "." not in version:
        # Голая мажорная версия ('22') — setup-node ставит последнюю 22.x,
        # а она всегда новее 22.4, так что минор сверять не о чем.
        assert int(version) >= MIN_NODE_VERSION[0], (
            f"node-version «{version}»: мажорная версия ниже 22"
        )
        return

    parts = tuple(int(part) for part in version.split("."))
    assert parts >= MIN_NODE_VERSION, (
        f"node-version «{version}» ниже {'.'.join(map(str, MIN_NODE_VERSION))} — "
        f"на нём упадёт vitest из-за --no-experimental-webstorage в vite.config.ts"
    )


def test_frontend_ci_npm_cache_matches_lock_file() -> None:
    """Кэш npm привязан к package-lock.json, иначе он не инвалидируется при правке зависимостей."""
    with_ = _frontend_setup_node_step()["with"]
    assert with_.get("cache") == "npm"
    assert with_.get("cache-dependency-path") == "frontend/package-lock.json"


# ---------------------------------------------------------------------------
# Сертификат: где лежит и кто его читает
# ---------------------------------------------------------------------------
SETUP_TLS = REPO / "scripts" / "setup_tls.sh"


def test_nginx_reads_certificates_from_host_letsencrypt() -> None:
    """certbot в setup_tls.sh и в cron продления пишет в хостовый /etc/letsencrypt.

    Именованный том `letsencrypt` — другое место на диске: nginx не увидел
    бы там ни одного файла и не стартовал бы, а без него недоступен и
    путь acme-challenge, нужный для выпуска. Монтируется каталог целиком,
    а не live/<домен>: в live лежат симлинки на ../../archive.
    """
    compose = _load(COMPOSE)
    volumes = [str(v) for v in compose["services"]["nginx"].get("volumes") or []]

    assert "/etc/letsencrypt:/etc/letsencrypt:ro" in volumes, (
        f"nginx обязан читать хостовый /etc/letsencrypt только на чтение, сейчас: {volumes}"
    )
    assert "letsencrypt" not in (compose.get("volumes") or {}), (
        "именованный том letsencrypt расходится с тем, куда пишет certbot"
    )


def test_setup_tls_removes_stub_before_certbot() -> None:
    """certbot отказывается выпускать в live/<домен>, созданный не им.

    storage.new_lineage: «live directory exists for <домен>». Заглушку
    убираем после того, как nginx на ней поднялся, и до запуска certbot.
    """
    text = SETUP_TLS.read_text(encoding="utf-8")
    proverka = text.find("/.well-known/acme-challenge/$PROBA")
    ubrat = text.find('rm -rf "$LIVE" "/etc/letsencrypt/archive/$DOMAIN" "$RENEWAL"')
    certbot = text.find("certbot/certbot:latest certonly")

    assert proverka != -1, "в setup_tls.sh нет проверки, что nginx отдаёт acme-challenge"
    assert ubrat != -1, "в setup_tls.sh нет удаления заглушки (live, archive, renewal)"
    assert certbot != -1, "в setup_tls.sh нет запуска certbot"
    assert proverka < ubrat < certbot, (
        "порядок: nginx поднялся на заглушке и отдаёт acme-challenge → заглушка удалена → certbot"
    )
    assert "sozdat_zaglushku" in text[certbot:], (
        "при провале certbot заглушку нужно вернуть, иначе nginx останется без файлов"
    )


def test_setup_tls_does_not_excuse_dead_nginx() -> None:
    """Проверка acme-challenge не должна считать немой ответ нормой.

    Прежний текст «каталог проверки пуст — это нормально» печатался и при
    лежащем nginx, и certbot шёл в заведомо проваленную проверку.
    """
    text = SETUP_TLS.read_text(encoding="utf-8")

    assert "это нормально" not in text
    assert "/.well-known/acme-challenge/$PROBA" in text, (
        "nginx обязан отдать тестовый файл из acme-challenge до запуска certbot"
    )


# ---------------------------------------------------------------------------
# Образец .env
# ---------------------------------------------------------------------------
ENV_EXAMPLE = REPO / ".env.example"


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def test_env_example_sends_cookies_only_over_https() -> None:
    """.env на сервере создают копированием образца.

    С false куки с access-токеном уходят без Secure: до первого визита по
    HSTS любой http://…/api/… отдаёт их открытым текстом в кухонный Wi-Fi.
    Разработке true не мешает: http://localhost браузеры считают
    безопасным контекстом.
    """
    assert _env_example().get("SESSION_COOKIE_SECURE") == "true"


# ---------------------------------------------------------------------------
# Выкладка
# ---------------------------------------------------------------------------
DEPLOY = REPO / "scripts" / "deploy.sh"
FRONTEND_INDEX = REPO / "frontend" / "index.html"


def test_deploy_uses_only_services_that_exist() -> None:
    """`$COMPOSE exec/run <сервис>` для сервиса, которого нет в compose.

    Так было с `exec -T postgres`: база живёт в стеке Supabase, в нашем
    compose её нет, и выкладка останавливалась на шаге дампа — то есть не
    случалась вовсе.
    """
    text = DEPLOY.read_text(encoding="utf-8")
    services = set(_load(COMPOSE)["services"])
    used = re.findall(r"\$COMPOSE\s+(?:exec|run)\s+(?:-\S+\s+)*([a-z][\w-]*)", text)

    assert used, "разбор вызовов $COMPOSE exec/run сломан"
    for name in used:
        assert name in services, f"deploy.sh обращается к сервису «{name}», которого нет в compose"


def test_deploy_dumps_supabase_db_before_migrations() -> None:
    """Дамп всегда до миграций и через контейнер базы Supabase."""
    text = DEPLOY.read_text(encoding="utf-8")

    assert 'DB_CONTAINER="${DB_CONTAINER:-supabase-db}"' in text
    assert "проверьте DB_CONTAINER" in text, "нет внятной ошибки при ненайденном контейнере"
    dump = text.find('docker exec "$DB_CONTAINER" pg_dump')
    migrations = text.find("alembic upgrade head")
    assert dump != -1, "дамп не снимается из контейнера базы"
    assert dump < migrations, "дамп обязан сниматься ДО миграций"
    assert "set -Eeuo pipefail" in text, "без pipefail упавший pg_dump прячется за gzip"


def test_deploy_smoke_goes_through_nginx() -> None:
    """/healthz на 127.0.0.1:8080 проходит мимо nginx.

    Без проверки через nginx выкладка говорит «Готово» при лежащем сайте.
    """
    text = DEPLOY.read_text(encoding="utf-8")
    health = text.find('curl -fsS "$HEALTH_URL"')
    via_nginx = text.find('--resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/"')
    api = text.find('"https://$DOMAIN/api/me"')
    done = text.find("Готово. Версия")

    assert 'DOMAIN="${DOMAIN:-art.karppacho.ru}"' in text
    assert "--status running --services" in text, "смоук не проверяет, что nginx запущен"
    assert 'id="root"' in text
    assert health != -1 and via_nginx != -1 and api != -1
    assert health < via_nginx < done and api < done, "проверка nginx — после /healthz, до «Готово»"


def test_frontend_index_has_root_marker() -> None:
    """Смоук выкладки узнаёт index.html по id="root" — маркер обязан быть."""
    assert 'id="root"' in FRONTEND_INDEX.read_text(encoding="utf-8")


def _add_header_lines(name: str) -> list[str]:
    """Директивы add_header с данным заголовком, собранные в одну строку.

    Значение — либо слово, либо строка в кавычках: внутри кавычек бывают
    и точки с запятой (HSTS, CSP), и перевод строки (CSP записан в две).
    """
    text = NGINX_CONF.read_text(encoding="utf-8")
    pattern = rf'^\s*add_header\s+{re.escape(name)}\s+(?:"[^"]*"|[^\s;"]+)(?:\s+always)?\s*;'
    return [" ".join(m.group(0).split()) for m in re.finditer(pattern, text, re.MULTILINE)]


def test_cache_control_is_not_sent_on_errors() -> None:
    """Без `always`: nginx выдаёт такой заголовок только на 2xx и 3xx.

    С `always` «immutable на год» уходил бы и на 404 отсутствующего
    ассета. После отката на сборку с прежними хешами закэшированный на
    год 404 ломает страницу у шефа, и со стороны сервера это не починить.
    """
    lines = _add_header_lines("Cache-Control")
    assert lines, "add_header Cache-Control пропал из конфига"
    for line in lines:
        assert not line.rstrip(";").endswith(" always"), f"«{line}»: always у Cache-Control"


@pytest.mark.parametrize(
    "name",
    [
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "X-Frame-Options",
        "Content-Security-Policy",
    ],
)
def test_security_headers_are_sent_always(name: str) -> None:
    """Заголовкам безопасности `always` нужен: ответ с ошибкой — тоже страница."""
    lines = _add_header_lines(name)
    assert lines, f"add_header {name} пропал из конфига"
    for line in lines:
        assert line.rstrip(";").endswith(" always"), f"«{line}»: без always"
