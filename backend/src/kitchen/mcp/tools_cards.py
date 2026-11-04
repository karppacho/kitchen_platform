"""Инструменты карточек и «Сверки» (задача M4): черновик по этикетке и «Это он».

Оба пишут только в базу и только существующими путями слоя ``cards``:
черновик уходит в таблицу не отсюда, а кнопкой «Отправить» в веб-мастере;
«Это он» — как ручка ``confirm`` (таблицу не трогает). Новых путей записи
нет (правило 1). Без ``readOnlyHint``: оба инструмента меняют базу.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import TYPE_CHECKING

from mcp.server.mcpserver.exceptions import ToolError

from kitchen.cards import drafts, reference, submit
from kitchen.cards.label_text import fill_from_label_text
from kitchen.domain.cards import LABEL_FIELDS
from kitchen.llm.label import LABEL_READING_RULES, LabelExtraction

if TYPE_CHECKING:
    from mcp.server import MCPServer

    from kitchen.mcp.deps import McpDeps

log = logging.getLogger(__name__)

CARDS_ROLES = ("cook", "chef", "developer")
"""Роли раздела «Новый ингредиент» (как ``CardsUserDep`` сайта)."""
RECONCILER_ROLES = ("chef", "commerce", "developer")
"""Роли «Сверки» (как ``ReconcilerDep`` сайта)."""

WIZARD_PATH = "/cards"

LABEL_PROMPT = f"""\
Прочитай фотографию этикетки пищевого продукта и передай прочитанное платформе \
инструментом chernovik_po_etiketke: аргумент polya — JSON-объект с полями ниже. \
Платформа сама разберёт числа и сроки и положит их в черновик карточки; фото и \
отправка — в веб-мастере «Новый ингредиент».

{LABEL_READING_RULES}

Поля, которых нет на этикетке, просто не передавай. После вызова перескажи человеку \
замечания из ответа и дай ссылку на черновик."""


@contextmanager
def _refusals() -> Iterator[None]:
    """Отказ слоя ``cards`` — человеку тем же текстом, что видит сайт."""
    try:
        yield
    except drafts.CardsError as error:
        raise ToolError(str(error)) from error


def _web_link(deps: McpDeps) -> str:
    """Ссылка на мастер: домен платформы берём из допустимых хостов MCP."""
    for host in deps.settings.mcp_allowed_hosts:
        local = host.startswith(("localhost", "127."))
        return f"{'http' if local else 'https'}://{host}{WIZARD_PATH}"
    return WIZARD_PATH


def _shown(value: str | Decimal | None) -> str | None:
    """Прочитанное для ответа: число — строкой без экспоненты, пусто — ``None``."""
    if isinstance(value, Decimal):
        return format(value, "f")
    return value or None


def register_card_tools(server: MCPServer, deps: McpDeps) -> None:
    """Зарегистрировать инструменты записи (без ``readOnlyHint``) на ``server``."""

    @server.prompt(name="kartochka_po_etiketke", title="Карточка по этикетке")
    def kartochka_po_etiketke() -> str:
        return LABEL_PROMPT

    @server.tool(title="Черновик карточки по этикетке")
    def chernovik_po_etiketke(polya: LabelExtraction) -> dict[str, object]:
        """Положить в черновик карточки пользователя то, что прочитано с этикетки.

        Поля — строки как напечатано (белки, жиры, углеводы, ккал, срок, даты);
        числа и сроки разбирает платформа, не пересчитывай их. Заменяются только
        поля этикетки, фото и остальное в черновике не меняются. В ответе —
        прочитанное, замечания и ссылка на черновик в веб-мастере: фото и
        отправка в таблицу делаются там.
        """
        user = deps.user(*CARDS_ROLES)
        window = submit.submit_window(deps.settings)
        with _refusals(), deps.sessions() as session:
            draft, created = fill_from_label_text(
                session, user.id, polya.model_dump(), submit_window=window
            )
            values = {field: _shown(getattr(draft, field)) for field in LABEL_FIELDS}
            notes = drafts.draft_warnings(draft)
            draft_id = str(draft.id)
        return {
            "chernovik_id": draft_id,
            "sozdan_novyi": created,
            "prochitano": values,
            "zamechaniya": notes,
            "ssylka": _web_link(deps),
        }

    @server.tool(title="Это он")
    def eto_on(card_id: int, ingredient_id: int) -> dict[str, object]:
        """Подтвердить пару «карточка — ингредиент справочника» на «Сверке».

        Только база: таблицы не меняются. Ингредиент должен быть среди
        кандидатов карточки (id берутся из «Сверки»).
        """
        user = deps.user(*RECONCILER_ROLES)
        with _refusals(), deps.sessions() as session:
            pair = reference.confirm_pair(session, card_id, ingredient_id)
        if not pair.already:
            log.info(
                "«Это он» (MCP): карточка %s — ингредиент %s (id %s), подтвердил профиль %s",
                card_id,
                pair.ingredient_id,
                pair.legacy_id,
                user.id,
            )
        return {
            "card_id": pair.card_id,
            "ingredient_id": pair.ingredient_id,
            "legacy_id": pair.legacy_id,
            "name": pair.name,
            "already": pair.already,
            "message": pair.message,
        }
