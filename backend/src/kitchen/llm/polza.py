"""Клиент polza.ai: фото и промпт → текст ответа, цена, токены, время.

polza.ai — OpenAI-совместимый шлюз, единственный путь к моделям: прямой
доступ к OpenAI и Anthropic из РФ закрыт. Запрос идёт через SDK OpenAI с
адресом polza.ai поверх нашего ``httpx.Client``. Четыре вещи клиент решает
сам, а не SDK:

* **Повторы.** Встроенные повторы SDK выключены (``max_retries=0``): SDK
  повторил бы и 429, и таймаут ещё дважды с паузами — повар ждал бы
  минутами, а каждая попытка могла стоить денег. Свой повтор — ровно один и
  только там, где он может помочь: таймаут, обрыв связи, ответ 5xx.
* **Режим JSON.** ``response_format={"type": "json_object"}``; модель, которая
  его не знает, отвечает 400 — тогда тот же запрос уходит без него. Ответ
  всё равно проверяет схема того, кто звал.
* **Стоимость.** polza.ai кладёт ``cost_rub`` в ``usage``. SDK разобрал бы
  тело через ``float``; клиент читает сырое тело сам, дробные числа — через
  ``Decimal``, с теми цифрами, что прислал polza.ai.
* **Таймауты** — свои, из настроек, на каждую фазу запроса. У SDK по
  умолчанию десять минут ожидания ответа.

Отказ — :class:`LlmError`: вид — для кода, текст — для человека (его видит
повар), цена и время — для журнала вызовов.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal

import httpx
import openai

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam
    from openai.types.shared_params import ResponseFormatJSONObject

    from kitchen.config import Settings

LlmErrorKind = Literal[
    "key", "no_money", "not_found", "rate", "unavailable", "bad_request", "bad_reply", "garbage"
]

_KEY = "polza.ai не принял ключ платформы. Сообщите администратору."
_NO_MONEY = "На счёте polza.ai кончились деньги. Сообщите администратору."
_NOT_FOUND = "polza.ai не знает модель «{model}». Сообщите администратору."
_RATE = "polza.ai просит подождать: слишком много запросов. Попробуйте через минуту."
_UNAVAILABLE = "polza.ai не отвечает. Попробуйте ещё раз через минуту."
_BAD_REQUEST = "polza.ai отклонил запрос. Сообщите администратору."
_BAD_REPLY = (
    "polza.ai ответил непонятно. Попробуйте ещё раз; если повторится — сообщите администратору."
)
_GARBAGE = "Модель ответила непонятно. Попробуйте ещё раз или заполните поля вручную."

_REFUSED_COST = Decimal("0")
"""Цена отказа 4xx: polza.ai отказал до работы модели, списывать не за что."""

_JSON_MODE: ResponseFormatJSONObject = {"type": "json_object"}


class LlmError(RuntimeError):
    """Модель не дала ответа, которым можно пользоваться.

    ``kind`` — что делать коду: ``key``, ``no_money``, ``not_found``,
    ``bad_request`` — чинит администратор; ``rate`` и ``unavailable`` — стоит
    попробовать позже; ``bad_reply`` — polza.ai прислал непонятное;
    ``garbage`` — ответ модели не прошёл схему. Текст исключения — для
    человека.

    ``status`` — код ответа polza.ai (``None`` — ответа не было); ``detail`` —
    подробность для журнала. ``cost_rub``, ``tokens``, ``duration_ms`` — что
    известно о вызове для журнала: ``cost_rub=None`` — цена неизвестна
    (таймаут: модель могла поработать), ``0`` — polza.ai отказал сразу.
    """

    def __init__(
        self,
        kind: LlmErrorKind,
        message: str,
        *,
        status: int | None = None,
        detail: str = "",
        cost_rub: Decimal | None = None,
        tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: LlmErrorKind = kind
        self.status = status
        self.detail = detail
        self.cost_rub = cost_rub
        self.tokens = tokens
        self.duration_ms = duration_ms

    def describe(self) -> str:
        """Строка для журнала вызовов: вид, код ответа, подробность."""
        head = self.kind if self.status is None else f"{self.kind} {self.status}"
        return f"{head}: {self.detail or self}"


class LlmGarbageError(LlmError):
    """Ответ пришёл, но это не то, что просили: не JSON-объект, не та схема,
    слишком длинно. Деньги за него списаны — цену несёт :meth:`priced`."""

    def __init__(
        self,
        detail: str,
        *,
        cost_rub: Decimal | None = None,
        tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        super().__init__(
            "garbage",
            _GARBAGE,
            detail=detail,
            cost_rub=cost_rub,
            tokens=tokens,
            duration_ms=duration_ms,
        )

    def priced(self, reply: LlmReply) -> LlmGarbageError:
        """Та же ошибка с ценой, токенами и временем вызова, который её принёс."""
        return LlmGarbageError(
            self.detail,
            cost_rub=reply.cost_rub,
            tokens=reply.tokens,
            duration_ms=reply.duration_ms,
        )


@dataclass(frozen=True, slots=True)
class LlmReply:
    """Ответ модели и всё, что нужно журналу вызовов."""

    text: str
    """Текст ответа как есть; пустой — модель промолчала."""
    model: str
    cost_rub: Decimal | None
    """Сколько списал polza.ai; ``None`` — не сообщил."""
    tokens: int | None
    duration_ms: int
    """Сколько ждал повар — вместе с повтором, если он был."""


def parse_json(text: str) -> object:
    """JSON, в котором дробные числа — ``Decimal`` с теми же цифрами.

    ``NaN`` и ``Infinity`` (Python их понимает, JSON — нет) — ``ValueError``:
    бесконечная цена или бесконечные ккал — не число.
    """
    return json.loads(text, parse_float=Decimal, parse_constant=_not_a_number)


def _not_a_number(name: str) -> object:
    raise ValueError(f"{name} — не число")


class PolzaClient:
    """Вызовы модели через polza.ai.

    ``transport`` — транспорт httpx: в бою ``None`` (сеть), в тестах —
    ``httpx.MockTransport``. Ключ хранится только внутри SDK и не попадает
    ни в текст ошибок, ни в журнал.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Не задан ключ polza.ai — POLZA_API_KEY")
        self.base_url = base_url
        self._openai = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=float(timeout_seconds),
            # SDK принимает и httpx.Client (проверяет при создании), но
            # подписан под свой httpx2. Наш httpx — объявленная зависимость, и
            # в тестах тот же клиент ходит через httpx.MockTransport.
            http_client=httpx.Client(transport=transport),  # type: ignore[arg-type]
        )

    def vision_json(
        self, *, model: str, system: str, prompt: str, jpeg: bytes, max_tokens: int
    ) -> LlmReply:
        """Одно фото JPEG и промпт → ответ модели, ожидаемо JSON-объект.

        ``temperature 0``: одна и та же этикетка — один и тот же ответ.
        Разбирать ответ — дело того, кто звал: клиент не знает схемы.
        """
        image = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image}},
                    {"type": "text", "text": prompt},
                ],
            },
        ]
        started = time.monotonic_ns()
        json_mode = True
        repeated = False
        while True:
            try:
                body = self._send(model, messages, max_tokens, json_mode=json_mode)
            except openai.APIConnectionError as error:  # в том числе APITimeoutError
                if not repeated:
                    repeated = True
                    continue
                raise LlmError(
                    "unavailable",
                    _UNAVAILABLE,
                    detail=type(error).__name__,
                    duration_ms=_since(started),
                ) from error
            except openai.APIStatusError as error:
                status = error.status_code
                if status == 400 and json_mode:
                    json_mode = False
                    continue
                if _worth_repeating(status) and not repeated:
                    repeated = True
                    continue
                raise _refusal(status, model, _since(started)) from error
            return _reply(body, model, _since(started))

    def _send(
        self,
        model: str,
        messages: list[ChatCompletionMessageParam],
        max_tokens: int,
        *,
        json_mode: bool,
    ) -> str:
        """Один запрос; сырое тело ответа — чтобы цену не разобрал ``float``."""
        raw = self._openai.chat.completions.with_raw_response.create(
            model=model,
            messages=messages,
            temperature=0,
            max_tokens=max_tokens,
            response_format=_JSON_MODE if json_mode else openai.omit,
        )
        return raw.text


def polza_from_settings(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> PolzaClient | None:
    """Клиент боевого polza.ai из настроек; ``None`` — ключ не задан."""
    key = settings.polza_api_key.get_secret_value()
    if not key:
        return None
    return PolzaClient(
        api_key=key,
        base_url=settings.polza_base_url,
        timeout_seconds=settings.llm_vision_timeout_seconds,
        transport=transport,
    )


def _worth_repeating(status: int) -> bool:
    """Сервер не успел или сломался — второй раз может получиться."""
    return status >= 500 or status == 408


def _refusal(status: int, model: str, duration_ms: int) -> LlmError:
    if _worth_repeating(status):
        return LlmError(
            "unavailable", _UNAVAILABLE, status=status, detail="", duration_ms=duration_ms
        )
    kind: LlmErrorKind
    if status in (401, 403):
        kind, message = "key", _KEY
    elif status == 402:
        kind, message = "no_money", _NO_MONEY
    elif status == 404:
        kind, message = "not_found", _NOT_FOUND.format(model=model)
    elif status == 429:
        kind, message = "rate", _RATE
    else:
        kind, message = "bad_request", _BAD_REQUEST
    return LlmError(kind, message, status=status, cost_rub=_REFUSED_COST, duration_ms=duration_ms)


def _reply(body: str, model: str, duration_ms: int) -> LlmReply:
    """Тело ответа chat/completions → :class:`LlmReply`."""

    def bad(detail: str) -> LlmError:
        return LlmError("bad_reply", _BAD_REPLY, detail=detail, duration_ms=duration_ms)

    try:
        payload = parse_json(body)
    except (ValueError, RecursionError) as error:
        raise bad("тело ответа — не JSON") from error
    if not isinstance(payload, dict):
        raise bad("тело ответа — не объект")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise bad("в ответе нет choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise bad("в ответе нет message")
    content = message.get("content")
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    tokens = usage.get("total_tokens")
    answered = payload.get("model")
    return LlmReply(
        text=content if isinstance(content, str) else "",
        model=answered if isinstance(answered, str) and answered else model,
        cost_rub=_cost(usage.get("cost_rub")),
        tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        duration_ms=duration_ms,
    )


def _cost(value: object) -> Decimal | None:
    """``usage.cost_rub`` → рубли. Непонятное или отрицательное — ``None``: не знаем."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal | int):
        number = Decimal(value)
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip())
        except InvalidOperation:
            return None
    else:
        return None
    if not number.is_finite() or number < 0:
        return None
    return number


def _since(started_ns: int) -> int:
    return (time.monotonic_ns() - started_ns) // 1_000_000
