"""Фальшивый polza.ai: обработчик для ``httpx.MockTransport``.

Клиент polza.ai ходит через SDK OpenAI поверх ``httpx.Client``; фальшивка
встаёт вместо сети на уровне транспорта. Код отправляет те же байты, что
ушли бы на polza.ai, — в тесте видны тело запроса, заголовки и таймауты.

Ответ собирается сырым JSON-текстом: стоимость ``usage.cost_rub`` приходит
теми цифрами, что написаны в тесте, без пересборки через ``float`` — иначе
тест на ``Decimal`` проверял бы сам себя.
"""

from __future__ import annotations

import json

import httpx

KEY = "pza-NE-NASTOYASHCHII-KLYUCH"
BASE_URL = "https://polza.test/api/v1"
MODEL = "qwen/qwen3.6-plus"
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) + b"\xff\xd9"

USAGE = (
    '{"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200, "cost_rub": 0.0123}'
)

Answer = httpx.Response | type[httpx.TransportError]


def ok(content: str | None = '{"label_name": "Сыр"}', usage: str | None = USAGE) -> httpx.Response:
    """Ответ polza.ai. ``usage`` — сырой JSON или ``None`` (ключа нет вовсе)."""
    message = json.dumps({"role": "assistant", "content": content}, ensure_ascii=False)
    usage_part = "" if usage is None else f', "usage": {usage}'
    body = (
        f'{{"id": "gen-1", "object": "chat.completion", "model": "{MODEL}", '
        f'"choices": [{{"index": 0, "message": {message}, "finish_reason": "stop"}}]'
        f"{usage_part}}}"
    )
    return httpx.Response(200, content=body.encode(), headers={"content-type": "application/json"})


def refusal(status: int, message: str = "refused") -> httpx.Response:
    """Отказ polza.ai с кодом ``status``."""
    return httpx.Response(status, json={"error": {"message": message, "code": status}})


class FakePolza:
    """Сервер polza.ai: отвечает заказанным по очереди, последний ответ — на все
    остальные запросы. Класс исключения вместо ответа — сбой сети на этом
    запросе (таймаут, обрыв)."""

    def __init__(self, *answers: Answer) -> None:
        assert answers, "фальшивке нужен хотя бы один ответ"
        self._answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(answer, type):
            raise answer("polza.test не ответил", request=request)
        return answer

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def bodies(self) -> list[dict[str, object]]:
        """Тела запросов — как их разобрал бы polza.ai."""
        return [json.loads(request.content) for request in self.requests]
