"""Страницы входа и согласия для OAuth: чистый HTML без внешних файлов.

Всё, что пришло снаружи (название клиента, текст ошибки, номер запроса), идёт
через ``html.escape``. Скриптов нет вовсе, стили встроены — поэтому
политика безопасности страниц запрещает всё, кроме встроенных стилей.
"""

from __future__ import annotations

from html import escape

# Политика для всех страниц. Нет ``form-action``: Chrome применяет его и к
# переадресации после отправки формы, а мы после согласия уходим на адрес
# клиента (claude.ai, chatgpt.com, localhost).
CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"

_STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:26rem;margin:3rem auto;padding:0 1rem}"
    "label{display:block;margin:.8rem 0 .2rem}input{width:100%;padding:.5rem;box-sizing:border-box}"
    "button{margin-top:1rem;padding:.6rem 1.2rem;font-size:1rem}"
    ".err{color:#b00020;margin:.8rem 0}.small{color:#555;font-size:.85rem}"
)


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><h1>{escape(title)}</h1>{body}</body></html>"
    )


def _hidden(name: str, value: str) -> str:
    return f'<input type="hidden" name="{name}" value="{escape(value, quote=True)}">'


def login_page(*, txn: str, csrf: str, client_name: str, error: str | None, host: str) -> str:
    message = f'<p class="err">{escape(error)}</p>' if error else ""
    return _page(
        "Вход на платформу кухни",
        f"<p>Приложение «{escape(client_name)}» просит доступ. После входа вернётесь на "
        f"{escape(host)}.</p>{message}"
        '<form method="post" action="/oauth/login">'
        f"{_hidden('step', 'login')}{_hidden('txn', txn)}{_hidden('csrf', csrf)}"
        '<label for="email">Почта</label>'
        '<input id="email" name="email" type="email" autocomplete="username" required>'
        '<label for="password">Пароль</label>'
        '<input id="password" name="password" type="password" '
        'autocomplete="current-password" required>'
        '<button type="submit">Войти</button></form>',
    )


def consent_page(*, txn: str, csrf: str, client_name: str, person: str, host: str) -> str:
    def form(step: str, label: str) -> str:
        return (
            '<form method="post" action="/oauth/login" style="display:inline">'
            f"{_hidden('step', step)}{_hidden('txn', txn)}{_hidden('csrf', csrf)}"
            f'<button type="submit">{label}</button></form> '
        )

    return _page(
        "Разрешить доступ?",
        f"<p>Подключить «{escape(client_name)}» к платформе от имени {escape(person)}: "
        "читать справочник и блюда, создавать черновики карточек.</p>"
        f'<p class="small">После ответа вы вернётесь на {escape(host)}. '
        "Права — такие же, как у вас на сайте.</p>"
        f"{form('consent', 'Разрешить')}{form('deny', 'Отказать')}",
    )


def error_page(message: str) -> str:
    return _page("Запрос не выполнен", f"<p>{escape(message)}</p>")
