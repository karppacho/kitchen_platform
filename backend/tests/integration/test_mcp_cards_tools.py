"""Инструменты MCP для карточек на настоящей базе: черновик по этикетке и «Это он».

Сервер собирается вручную (``MCPServer`` + ``register_card_tools``), вызовы идут
клиентом SDK в памяти; пользователя подставляет ``McpDeps.subject``.

Что стерегут тесты:

* поля этикетки разбирает код (``label_fields_from_extraction``), а не число из
  «ответа модели»; неясное число — пустое поле и замечание;
* повторный вызов заменяет поля этикетки и не трогает фото и прочие шаги;
* отправляемый черновик не правится, как в вебе;
* ``eto_on`` — только роли «Сверки»; пара пишется в базу, в таблицы — ничего.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from mcp import Client
from mcp.server import MCPServer
from sqlalchemy import func, select, update

from kitchen.cards import drafts
from kitchen.config import Settings
from kitchen.db import models
from kitchen.domain.cards import label_fields_from_extraction
from kitchen.llm.label import LABEL_READING_RULES, LABEL_SYSTEM_PROMPT, LabelExtraction
from kitchen.mcp.deps import McpDeps
from kitchen.mcp.tools_cards import register_card_tools
from tests.integration.test_database import _url
from tests.integration.test_reconciliation_api import _sheets
from tests.integration.test_reference_flow import (
    _card,
    _import,
    _ingredient_id,
    _pair,
    _writes,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

COOK = uuid.UUID("11111111-1111-1111-1111-111111111111")
CHEF = uuid.UUID("33333333-3333-3333-3333-333333333333")

FIELDS = {
    "label_name": "Сыр Моцарелла",
    "manufacturer": "ООО Молоко",
    "composition": "молоко, соль",
    "proteins": "18 г",
    "fats": "20 г",
    "carbohydrates": "1 г",
    "kcal": "250 ккал / 1046 кДж",
    "nutrition_basis": "100 г",
    "shelf_life_period": "6 месяцев",
    "storage_conditions": "при t +2 до +6 °C",
}


def settings() -> Settings:
    return Settings(app_env="test", database_url=_url(), mcp_allowed_hosts=["kitchen.example"])  # type: ignore[arg-type]


def _roles(session: Session, profile: uuid.UUID, *codes: str) -> None:
    for code in codes:
        session.add(models.UserRole(profile_id=profile, role_code=code))


@pytest.fixture
def people(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        session.add(models.Profile(id=COOK, email="cook@example.com", display_name="Повар"))
        session.add(models.Profile(id=CHEF, email="chef@example.com", display_name="Шеф"))
        session.flush()
        _roles(session, COOK, "cook")
        _roles(session, CHEF, "chef")


def call(
    sessions: sessionmaker[Session], who: uuid.UUID, tool: str, arguments: dict[str, Any]
) -> tuple[bool, Any]:
    """Вызов инструмента клиентом в памяти: (isError, структурный ответ или текст)."""
    server = MCPServer("test")
    register_card_tools(server, McpDeps(sessions, settings(), subject=lambda: str(who)))

    async def run() -> tuple[bool, Any]:
        async with Client(server) as client:
            reply = await client.call_tool(tool, arguments)
        if reply.is_error:
            return True, reply.content[0].text  # type: ignore[union-attr]
        return False, reply.structured_content

    return asyncio.run(run())


def draft_of(sessions: sessionmaker[Session], owner: uuid.UUID) -> models.CardDraft:
    with sessions() as session:
        found = session.scalar(
            select(models.CardDraft).where(
                models.CardDraft.owner_id == owner, models.CardDraft.status == "active"
            )
        )
        assert found is not None
        return found


# ---------------------------------------------------------------------------
# chernovik_po_etiketke
# ---------------------------------------------------------------------------
def test_creates_draft_and_parses_fields_by_code(
    sessions: sessionmaker[Session], people: None
) -> None:
    failed, reply = call(sessions, COOK, "chernovik_po_etiketke", {"polya": FIELDS})

    assert not failed, reply
    draft = draft_of(sessions, COOK)
    assert (draft.label_name, draft.manufacturer, draft.composition) == (
        "Сыр Моцарелла",
        "ООО Молоко",
        "молоко, соль",
    )
    assert (draft.protein, draft.fat, draft.carbs, draft.kcal) == (
        Decimal(18),
        Decimal(20),
        Decimal(1),
        Decimal(250),
    )
    assert draft.shelf_life_sealed
    assert reply["chernovik_id"] == str(draft.id)
    assert Decimal(reply["prochitano"]["kcal"]) == 250
    assert reply["ssylka"] == "https://kitchen.example/cards"


def test_unclear_number_gets_same_outcome_as_recognition(
    sessions: sessionmaker[Session], people: None
) -> None:
    """«30|125 ккал» — результат ровно тот, что даёт разбор после распознавания
    (на текущем коде: что бы он ни решил — пусто или число, и его замечания).
    Свой разбор инструмент не заводит."""
    fields = {**FIELDS, "kcal": "30|125 ккал"}
    expected, expected_warnings = label_fields_from_extraction(
        LabelExtraction(**fields).model_dump()
    )

    failed, reply = call(sessions, COOK, "chernovik_po_etiketke", {"polya": fields})

    assert not failed, reply
    assert draft_of(sessions, COOK).kcal == expected["kcal"]
    notes = set(reply["zamechaniya"])
    assert set(expected_warnings) <= notes


def test_unreadable_number_is_empty_field_with_warning(
    sessions: sessionmaker[Session], people: None
) -> None:
    """Число, которое код прочитать не берётся, — пустое поле и замечание."""
    failed, reply = call(
        sessions, COOK, "chernovik_po_etiketke", {"polya": {**FIELDS, "kcal": "10-20 ккал"}}
    )

    assert not failed, reply
    assert draft_of(sessions, COOK).kcal is None
    assert reply["prochitano"]["kcal"] is None
    assert any("10-20" in note for note in reply["zamechaniya"])


def test_model_number_is_not_trusted_without_parsing(
    sessions: sessionmaker[Session], people: None
) -> None:
    """Число 'как есть' с единицами разбирается кодом: «12,5 г» → 12.5."""
    call(sessions, COOK, "chernovik_po_etiketke", {"polya": {**FIELDS, "proteins": "12,5 г"}})

    assert draft_of(sessions, COOK).protein == Decimal("12.5")


def test_too_long_field_is_refused_by_schema(sessions: sessionmaker[Session], people: None) -> None:
    failed, _ = call(
        sessions, COOK, "chernovik_po_etiketke", {"polya": {**FIELDS, "kcal": "1" * 101}}
    )

    assert failed
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(models.CardDraft)) == 0


def test_second_call_replaces_label_fields_keeps_photos_and_steps(
    sessions: sessionmaker[Session], people: None
) -> None:
    call(sessions, COOK, "chernovik_po_etiketke", {"polya": FIELDS})
    with sessions.begin() as session:
        session.execute(
            update(models.CardDraft).values(
                label_file_id="LABEL", package_file_id="PACK", step="photos", supplier="Метро"
            )
        )

    failed, _ = call(
        sessions,
        COOK,
        "chernovik_po_etiketke",
        {"polya": {"label_name": "Другой", "kcal": "100 ккал", "nutrition_basis": "100 г"}},
    )

    assert not failed
    draft = draft_of(sessions, COOK)
    assert draft.label_name == "Другой"
    assert draft.manufacturer == "", "поля этикетки заменяются целиком"
    assert (draft.label_file_id, draft.package_file_id) == ("LABEL", "PACK")
    assert (draft.step, draft.supplier) == ("photos", "Метро")
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(models.CardDraft)) == 1


def test_draft_being_submitted_is_refused(sessions: sessionmaker[Session], people: None) -> None:
    call(sessions, COOK, "chernovik_po_etiketke", {"polya": FIELDS})
    with sessions.begin() as session:
        session.execute(update(models.CardDraft).values(submit_started_at=func.now()))

    failed, text = call(sessions, COOK, "chernovik_po_etiketke", {"polya": {"label_name": "Х"}})

    assert failed
    assert drafts.WAIT_FOR_SUBMIT in text
    assert draft_of(sessions, COOK).label_name == "Сыр Моцарелла"


def test_submitted_draft_is_not_touched_new_one_started(
    sessions: sessionmaker[Session], people: None
) -> None:
    call(sessions, COOK, "chernovik_po_etiketke", {"polya": FIELDS})
    with sessions.begin() as session:
        session.execute(update(models.CardDraft).values(status="submitted"))

    failed, _ = call(sessions, COOK, "chernovik_po_etiketke", {"polya": {"label_name": "Новый"}})

    assert not failed
    with sessions() as session:
        old = session.scalar(select(models.CardDraft).where(models.CardDraft.status == "submitted"))
        assert old is not None
        assert old.label_name == "Сыр Моцарелла"
    assert draft_of(sessions, COOK).label_name == "Новый"


# ---------------------------------------------------------------------------
# eto_on
# ---------------------------------------------------------------------------
def test_eto_on_cook_is_refused(sessions: sessionmaker[Session], people: None) -> None:
    failed, text = call(sessions, COOK, "eto_on", {"card_id": 1, "ingredient_id": 1})

    assert failed
    assert "нужна роль" in text


def test_eto_on_writes_pair_to_database_only(sessions: sessionmaker[Session], people: None) -> None:
    sheets = _sheets()
    _import(sheets, sessions)
    card = _card(sessions, "Сахар")
    chosen = _ingredient_id(sessions, "98")
    opened = len(sheets.opened)

    failed, reply = call(sessions, CHEF, "eto_on", {"card_id": card.id, "ingredient_id": chosen})

    assert not failed, reply
    assert reply["ingredient_id"] == chosen
    assert reply["already"] is False
    assert _pair(_card(sessions, "Сахар")) == (chosen, "linked", True)
    assert _writes(sheets) == []
    assert len(sheets.opened) == opened
    with sessions() as session:
        assert session.scalars(select(models.SheetWrite)).all() == []


def test_eto_on_not_a_candidate_is_refusal(sessions: sessionmaker[Session], people: None) -> None:
    _import(_sheets(), sessions)
    card = _card(sessions, "Сахар")

    failed, text = call(
        sessions,
        CHEF,
        "eto_on",
        {"card_id": card.id, "ingredient_id": _ingredient_id(sessions, "129")},
    )

    assert failed
    assert _pair(_card(sessions, "Сахар")) == (None, "ambiguous", False)
    assert text


# ---------------------------------------------------------------------------
# Промпт
# ---------------------------------------------------------------------------
def test_prompt_uses_shared_reading_rules() -> None:
    server = MCPServer("test")
    register_card_tools(server, McpDeps(None, settings()))  # type: ignore[arg-type]

    async def run() -> str:
        async with Client(server) as client:
            got = await client.get_prompt("kartochka_po_etiketke")
        return got.messages[0].content.text  # type: ignore[union-attr]

    assert LABEL_READING_RULES in asyncio.run(run())
    assert LABEL_READING_RULES in LABEL_SYSTEM_PROMPT
