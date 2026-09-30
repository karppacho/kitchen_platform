"""Клиент polza.ai — без сети: вместо сервера ``httpx.MockTransport``.

Проверяется то, что стоит денег или времени повара: что уходит в запрос
(модель, фото, ``temperature 0``, режим JSON), сколько раз клиент стучится
при отказе (повторы SDK выключены, свой повтор — ровно один и только там,
где он может помочь) и как читается стоимость — ``Decimal``, а не ``float``.
"""

from __future__ import annotations

import base64
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import pytest

from kitchen.config import Settings
from kitchen.llm.polza import LlmError, PolzaClient, polza_from_settings
from tests.fake_polza import BASE_URL, JPEG, KEY, MODEL, FakePolza, ok, refusal

if TYPE_CHECKING:
    from kitchen.llm.polza import LlmReply

SYSTEM = "Ты читаешь этикетку."
PROMPT = "Перепиши поля и верни JSON."


def _client(fake: FakePolza, *, timeout: float = 60) -> PolzaClient:
    return PolzaClient(
        api_key=KEY, base_url=BASE_URL, timeout_seconds=timeout, transport=fake.transport()
    )


def _read(fake: FakePolza) -> LlmReply:
    return _client(fake).vision_json(
        model=MODEL, system=SYSTEM, prompt=PROMPT, jpeg=JPEG, max_tokens=3000
    )


# ---------------------------------------------------------------------------
# Запрос
# ---------------------------------------------------------------------------
def test_request_carries_model_photo_temperature_and_json_mode() -> None:
    """Модель — из настроек, фото — ``data:image/jpeg;base64``, ``temperature 0``
    (одна этикетка — один ответ, а не лотерея), режим JSON включён."""
    fake = FakePolza(ok())

    reply = _read(fake)

    [request] = fake.requests
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    [body] = fake.bodies()
    assert body["model"] == MODEL
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 3000
    system, user = body["messages"]
    assert system == {"role": "system", "content": SYSTEM}
    assert user["role"] == "user"
    [image] = [part for part in user["content"] if part["type"] == "image_url"]
    url = image["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.removeprefix("data:image/jpeg;base64,")) == JPEG
    assert {"type": "text", "text": PROMPT} in user["content"]
    assert reply.text == '{"label_name": "Сыр"}'


def test_timeouts_are_our_own() -> None:
    """Таймаут — из настроек на каждой фазе запроса, а не десять минут SDK."""
    fake = FakePolza(ok())

    _client(fake, timeout=45).vision_json(
        model=MODEL, system=SYSTEM, prompt=PROMPT, jpeg=JPEG, max_tokens=100
    )

    [request] = fake.requests
    assert request.extensions["timeout"] == {"connect": 45, "read": 45, "write": 45, "pool": 45}


# ---------------------------------------------------------------------------
# Повторы
# ---------------------------------------------------------------------------
def test_json_mode_refused_repeats_without_it() -> None:
    """Модель не знает ``response_format`` — 400; повтор тот же, но без него.
    Ответ всё равно проверит схема."""
    fake = FakePolza(refusal(400, "response_format is not supported"), ok())

    reply = _read(fake)

    first, second = fake.bodies()
    assert first["response_format"] == {"type": "json_object"}
    assert "response_format" not in second
    assert second["temperature"] == 0
    assert second["model"] == MODEL
    assert reply.text == '{"label_name": "Сыр"}'


def test_bad_request_without_json_mode_is_an_error() -> None:
    fake = FakePolza(refusal(400))

    with pytest.raises(LlmError) as caught:
        _read(fake)

    assert caught.value.kind == "bad_request"
    assert len(fake.requests) == 2


def test_timeout_is_repeated_exactly_once() -> None:
    """Таймаут — ровно два запроса: встроенные повторы SDK выключены, свой — один.
    Иначе повар ждал бы минутами, а каждая попытка могла стоить денег."""
    fake = FakePolza(httpx.ReadTimeout)

    with pytest.raises(LlmError) as caught:
        _read(fake)

    assert caught.value.kind == "unavailable"
    assert caught.value.cost_rub is None, "модель могла успеть поработать — цена неизвестна"
    assert len(fake.requests) == 2


def test_timeout_then_answer() -> None:
    fake = FakePolza(httpx.ReadTimeout, ok())

    reply = _read(fake)

    assert reply.text == '{"label_name": "Сыр"}'
    assert len(fake.requests) == 2


def test_no_connection_is_repeated_once() -> None:
    fake = FakePolza(httpx.ConnectError)

    with pytest.raises(LlmError) as caught:
        _read(fake)

    assert caught.value.kind == "unavailable"
    assert len(fake.requests) == 2


def test_server_error_is_repeated_once() -> None:
    fake = FakePolza(refusal(503))

    with pytest.raises(LlmError) as caught:
        _read(fake)

    assert caught.value.kind == "unavailable"
    assert caught.value.status == 503
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, "key"),
        (403, "key"),
        (402, "no_money"),
        (404, "not_found"),
        (429, "rate"),
    ],
)
def test_refusals_are_not_repeated(status: int, kind: str) -> None:
    """Ключ не принят, денег нет, модели нет, «слишком часто» — повтор не
    поможет: ровно один запрос. polza.ai отказал до работы модели — цена 0."""
    fake = FakePolza(refusal(status))

    with pytest.raises(LlmError) as caught:
        _read(fake)

    assert len(fake.requests) == 1
    assert caught.value.kind == kind
    assert caught.value.status == status
    assert caught.value.cost_rub == Decimal("0")
    assert caught.value.duration_ms is not None


def test_error_texts_are_for_people_and_never_show_the_key() -> None:
    for answer in (refusal(401), refusal(402), refusal(404), refusal(429), refusal(503)):
        with pytest.raises(LlmError) as caught:
            _read(FakePolza(answer))
        text = str(caught.value)
        assert KEY not in text
        assert KEY not in repr(caught.value)
        assert KEY not in caught.value.describe()
        assert text.endswith("."), text
        assert any("а" <= char <= "я" for char in text.lower()), text


# ---------------------------------------------------------------------------
# Ответ: стоимость, токены, модель
# ---------------------------------------------------------------------------
def test_cost_rub_is_exact_decimal() -> None:
    """Стоимость из ``usage.cost_rub`` — ``Decimal`` ровно с теми цифрами, что
    прислал polza.ai. Через ``float`` хвост потерялся бы."""
    usage = '{"total_tokens": 1200, "cost_rub": 0.123456789012345678}'

    reply = _read(FakePolza(ok(usage=usage)))

    assert isinstance(reply.cost_rub, Decimal)
    assert reply.cost_rub == Decimal("0.123456789012345678")


def test_whole_cost_is_decimal_too() -> None:
    reply = _read(FakePolza(ok(usage='{"total_tokens": 10, "cost_rub": 2}')))

    assert isinstance(reply.cost_rub, Decimal)
    assert reply.cost_rub == Decimal("2")


@pytest.mark.parametrize(
    "usage",
    [
        None,
        '{"total_tokens": 10}',
        '{"total_tokens": 10, "cost_rub": null}',
        '{"total_tokens": 10, "cost_rub": "дёшево"}',
        '{"total_tokens": 10, "cost_rub": -1}',
        '{"total_tokens": 10, "cost_rub": true}',
    ],
)
def test_no_cost_is_none(usage: str | None) -> None:
    """Нет стоимости или она непонятна — ``None``: «не знаем», а не ноль.
    Бюджет посчитает такой вызов по оценке."""
    reply = _read(FakePolza(ok(usage=usage)))

    assert reply.cost_rub is None


def test_tokens_model_and_duration() -> None:
    reply = _read(FakePolza(ok()))

    assert reply.tokens == 1200
    assert reply.model == MODEL
    assert isinstance(reply.duration_ms, int)
    assert reply.duration_ms >= 0


def test_empty_content_is_empty_text() -> None:
    """Пустой ответ — не сбой связи; что это мусор, решит разбор ответа."""
    reply = _read(FakePolza(ok(content=None)))

    assert reply.text == ""


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, content=b"<html>502 Bad Gateway</html>"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"index": 0, "finish_reason": "stop"}]}),
        httpx.Response(200, json={"error": {"message": "internal"}}),
        httpx.Response(200, json=[1, 2]),
    ],
)
def test_unreadable_reply_is_bad_reply(answer: httpx.Response) -> None:
    fake = FakePolza(answer)

    with pytest.raises(LlmError) as caught:
        _read(fake)

    assert caught.value.kind == "bad_reply"
    assert len(fake.requests) == 1


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
def test_no_key_no_client() -> None:
    """Ключ не задан — клиента нет: распознавание «не настроено», запросов нет."""
    settings = Settings(_env_file=None, polza_api_key="")

    assert polza_from_settings(settings) is None
    with pytest.raises(ValueError, match="POLZA_API_KEY"):
        PolzaClient(api_key="", base_url=BASE_URL, timeout_seconds=60)


def test_client_from_settings() -> None:
    fake = FakePolza(ok())
    settings = Settings(
        _env_file=None,
        polza_api_key=KEY,
        polza_base_url=BASE_URL,
        llm_vision_timeout_seconds=33,
    )

    client = polza_from_settings(settings, transport=fake.transport())

    assert client is not None
    assert client.base_url == BASE_URL
    client.vision_json(model=MODEL, system=SYSTEM, prompt=PROMPT, jpeg=JPEG, max_tokens=10)
    [request] = fake.requests
    assert str(request.url).startswith(BASE_URL)
    assert request.extensions["timeout"]["read"] == 33
