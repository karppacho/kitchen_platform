"""Клиент polza.ai: фото и промпт → текст ответа, цена, токены, время.

polza.ai — OpenAI-совместимый шлюз, единственный путь к моделям: прямой
доступ к OpenAI и Anthropic из РФ закрыт. Запрос идёт через SDK OpenAI с
адресом polza.ai поверх нашего ``httpx.Client``. Пять вещей клиент решает
сам, а не SDK:

* **Повторы.** Встроенные повторы SDK выключены (``max_retries=0``): SDK
  повторил бы и 429, и таймаут ещё дважды с паузами — повар ждал бы
  минутами, а каждая попытка могла стоить денег. Свой повтор — ровно один и
  только там, где он может помочь: таймаут, обрыв связи, ответ 5xx.
* **Общий срок.** Таймаут чтения отсчитывается от последнего полученного
  байта, и попытка может идти дольше таймаута. Поэтому ещё одна попытка
  (повтор или запрос без режима JSON) делается, только если прошедшее время
  плюс таймаут не больше :data:`TOTAL_DEADLINE_SECONDS`: nginx держит запрос
  распознавания 180 с.
* **Режим JSON.** ``response_format={"type": "json_object"}``; модель, которая
  его не знает, отвечает 400 — тогда тот же запрос уходит без него. Ответ
  всё равно проверяет схема того, кто звал.
* **Стоимость.** polza.ai кладёт ``cost_rub`` в ``usage``. SDK разобрал бы
  тело через ``float``; клиент читает сырое тело сам, дробные числа — через
  ``Decimal``, с теми цифрами, что прислал polza.ai. Попытки, цена которых
  неизвестна (таймаут, обрыв после отправки, 5xx, ответ без цены), клиент
  считает: журнал вызовов добавляет за каждую оценку.
* **Таймауты** — свои, из настроек, на каждую фазу запроса. У SDK по
  умолчанию десять минут ожидания ответа.

Отказ — :class:`LlmError`: вид — для кода, текст — для человека (его видит
повар), цена и время — для журнала вызовов. Исключение SDK наружу не уходит
даже цепочкой: в нём запрос с заголовком ``Authorization: Bearer <ключ>``, и
любой ``logger.exception`` унёс бы ключ в лог.
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
    from collections.abc import Callable

    from openai.types.chat import ChatCompletionMessageParam
    from openai.types.shared_params import ResponseFormatJSONObject

    from kitchen.config import Settings

LlmErrorKind = Literal[
    "key", "no_money", "not_found", "rate", "unavailable", "bad_request", "bad_reply", "garbage"
]

TOTAL_DEADLINE_SECONDS = 170
"""Дольше этого вызов не затягивается повтором: nginx ждёт распознавание 180 с."""

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

_FREE = Decimal("0")
"""Цена попытки, до модели не дошедшей: отказ 4xx или запрос, не ушедший в сеть."""

_COST_CEILING = Decimal("100000000")
"""С этой цены — не цена: колонка журнала ``Numeric(12,4)`` её не вместит."""
_TOKENS_CEILING = 2**31 - 1
"""Больше токенов колонка ``Integer`` не вмещает."""

_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
"""Сбои до отправки запроса: соединения не было — и списывать не за что."""

_JSON_MODE: ResponseFormatJSONObject = {"type": "json_object"}


class LlmError(RuntimeError):
    """Модель не дала ответа, которым можно пользоваться.

    ``kind`` — что делать коду: ``key``, ``no_money``, ``not_found``,
    ``bad_request`` — чинит администратор; ``rate`` и ``unavailable`` — стоит
    попробовать позже; ``bad_reply`` — polza.ai прислал непонятное;
    ``garbage`` — ответ модели не прошёл схему. Текст исключения — для
    человека.

    ``status`` — код ответа polza.ai (``None`` — ответа не было); ``detail`` —
    подробность для журнала. Для журнала же — ``cost_rub``: известная цена
    последней попытки (``0`` — polza.ai отказал сразу или запрос не ушёл,
    ``None`` — неизвестна); ``unpriced_attempts`` — сколько попыток могли
    стоить денег, а сколько — неизвестно (последняя с ``cost_rub=None`` в их
    числе); ``tokens``, ``duration_ms``.
    """

    def __init__(
        self,
        kind: LlmErrorKind,
        message: str,
        *,
        status: int | None = None,
        detail: str = "",
        cost_rub: Decimal | None = None,
        unpriced_attempts: int = 0,
        tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: LlmErrorKind = kind
        self.status = status
        self.detail = detail
        self.cost_rub = cost_rub
        self.unpriced_attempts = unpriced_attempts
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
        unpriced_attempts: int = 0,
        tokens: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        super().__init__(
            "garbage",
            _GARBAGE,
            detail=detail,
            cost_rub=cost_rub,
            unpriced_attempts=unpriced_attempts,
            tokens=tokens,
            duration_ms=duration_ms,
        )

    def priced(self, reply: LlmReply) -> LlmGarbageError:
        """Та же ошибка с ценой, токенами и временем вызова, который её принёс."""
        return LlmGarbageError(
            self.detail,
            cost_rub=reply.cost_rub,
            unpriced_attempts=reply.unpriced_attempts,
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
    """Сколько списал polza.ai за ответившую попытку; ``None`` — не сообщил."""
    tokens: int | None
    duration_ms: int
    """Сколько ждал повар — вместе с повтором, если он был."""
    unpriced_attempts: int = 0
    """Попытки с неизвестной ценой: неудачные до ответа (таймаут, обрыв после
    отправки, 5xx) и сам ответ, если polza.ai не сообщил цену."""


def parse_json(text: str) -> object:
    """JSON, в котором дробные числа — ``Decimal`` с теми же цифрами.

    ``NaN`` и ``Infinity`` (Python их понимает, JSON — нет) — ``ValueError``:
    бесконечная цена или бесконечные ккал — не число.
    """
    return json.loads(text, parse_float=Decimal, parse_constant=_not_a_number)


def _not_a_number(name: str) -> object:
    raise ValueError(f"{name} — не число")


@dataclass(frozen=True, slots=True)
class _Attempt:
    """Чем кончилась одна попытка — без исключения SDK и его запроса с ключом."""

    body: str | None = None
    """Тело ответа 2xx."""
    status: int | None = None
    """Код отказа polza.ai."""
    maybe_sent: bool = False
    """Сбой связи, при котором запрос мог дойти до модели."""
    detail: str = ""


class PolzaClient:
    """Вызовы модели через polza.ai.

    Держит пул соединений: заводите один на процесс или закрывайте
    (:meth:`close`, ``with``). ``transport`` — транспорт httpx: в бою ``None``
    (сеть), в тестах — ``httpx.MockTransport``; ``clock`` — монотонные часы в
    наносекундах, для тестов общего срока. Ключ хранится только внутри SDK и не
    попадает ни в текст ошибок, ни в журнал.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if not api_key:
            raise ValueError("Не задан ключ polza.ai — POLZA_API_KEY")
        self.base_url = base_url
        self._clock = clock
        self._timeout_ns = round(timeout_seconds * 1_000_000_000)
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

    def close(self) -> None:
        """Закрыть соединения; дальше клиент не работает."""
        self._openai.close()

    def __enter__(self) -> PolzaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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
        started = self._clock()
        json_mode = True
        repeated = False
        unpriced = 0
        while True:
            attempt = self._attempt(model, messages, max_tokens, json_mode=json_mode)
            if attempt.body is not None:
                return _reply(attempt.body, model, self._elapsed_ms(started), unpriced)
            if attempt.status is None:
                unpriced += int(attempt.maybe_sent)
                if not repeated and self._time_for_another(started):
                    repeated = True
                    continue
                raise LlmError(
                    "unavailable",
                    _UNAVAILABLE,
                    detail=attempt.detail,
                    cost_rub=None if attempt.maybe_sent else _FREE,
                    unpriced_attempts=unpriced,
                    duration_ms=self._elapsed_ms(started),
                ) from None
            if attempt.status == 400 and json_mode and self._time_for_another(started):
                json_mode = False
                continue
            if _worth_repeating(attempt.status):
                unpriced += 1
                if not repeated and self._time_for_another(started):
                    repeated = True
                    continue
            raise _refusal(attempt.status, model, self._elapsed_ms(started), unpriced) from None

    def _attempt(
        self,
        model: str,
        messages: list[ChatCompletionMessageParam],
        max_tokens: int,
        *,
        json_mode: bool,
    ) -> _Attempt:
        """Один запрос; сырое тело ответа — чтобы цену не разобрал ``float``."""
        try:
            raw = self._openai.chat.completions.with_raw_response.create(
                model=model,
                messages=messages,
                temperature=0,
                max_tokens=max_tokens,
                response_format=_JSON_MODE if json_mode else openai.omit,
            )
            return _Attempt(body=raw.text)
        except openai.APIStatusError as error:
            return _Attempt(status=error.status_code)
        except openai.APIConnectionError as error:  # в том числе APITimeoutError
            cause = error.__cause__
            return _Attempt(
                maybe_sent=not isinstance(cause, _NOT_SENT),
                detail=type(cause or error).__name__,
            )

    def _time_for_another(self, started: int) -> bool:
        """Уложится ли ещё одна попытка целиком в общий срок."""
        spent = self._clock() - started
        return spent + self._timeout_ns <= TOTAL_DEADLINE_SECONDS * 1_000_000_000

    def _elapsed_ms(self, started: int) -> int:
        return (self._clock() - started) // 1_000_000


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


def _refusal(status: int, model: str, duration_ms: int, unpriced: int) -> LlmError:
    if _worth_repeating(status):
        # Модель могла успеть поработать — цена неизвестна, попытка в unpriced.
        return LlmError(
            "unavailable",
            _UNAVAILABLE,
            status=status,
            unpriced_attempts=unpriced,
            duration_ms=duration_ms,
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
    return LlmError(
        kind,
        message,
        status=status,
        cost_rub=_FREE,
        unpriced_attempts=unpriced,
        duration_ms=duration_ms,
    )


def _reply(body: str, model: str, duration_ms: int, unpriced: int) -> LlmReply:
    """Тело ответа chat/completions → :class:`LlmReply`.

    Ответ, который не разобрать, — тоже попытка с неизвестной ценой.
    """

    def bad(detail: str) -> LlmError:
        return LlmError(
            "bad_reply",
            _BAD_REPLY,
            detail=detail,
            unpriced_attempts=unpriced + 1,
            duration_ms=duration_ms,
        )

    try:
        payload = parse_json(body)
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict):
        raise bad("тело ответа — не JSON-объект") from None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise bad("в ответе нет choices") from None
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise bad("в ответе нет message") from None
    content = message.get("content")
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    tokens = usage.get("total_tokens")
    answered = payload.get("model")
    cost = _cost(usage.get("cost_rub"))
    return LlmReply(
        text=content if isinstance(content, str) else "",
        model=answered if isinstance(answered, str) and answered else model,
        cost_rub=cost,
        tokens=_tokens(tokens),
        duration_ms=duration_ms,
        unpriced_attempts=unpriced + int(cost is None),
    )


def _cost(value: object) -> Decimal | None:
    """``usage.cost_rub`` → рубли. Непонятное, отрицательное или нелепо большое
    — ``None``: не знаем, журнал посчитает по оценке."""
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
    if not number.is_finite() or not _FREE <= number < _COST_CEILING:
        return None
    return number


def _tokens(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= _TOKENS_CEILING else None
