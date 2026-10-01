"""Распознавание этикетки: ``POST /api/cards/recognize/{id}`` на настоящей базе.

Модель — фальшивый polza.ai на уровне транспорта (``tests/fake_polza.py``),
Drive — фальшивка (``tests/fake_drive.py``): код шлёт им те же запросы, что
ушли бы наружу.

Главное, что стерегут эти тесты:

* модель только читает: поля черновика заполняет доменный разбор, срок
  годности считает код;
* каждый вызов модели — строка в журнале ``llm_calls``, и удачный, и нет, с
  моделью из настроек, а не из ответа шлюза;
* бюджет и лимит повара проверяются до вызова: исчерпаны — 429, и модель не
  зовётся вовсе;
* распознавание переживает сон телефона (результат — в черновике), но не
  ложится поверх заменённой этикетки и не запирает черновик навсегда, если
  процесс умер посреди вызова.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from kitchen.cards.recognize import STALE_AFTER
from kitchen.config import Settings
from kitchen.db import models
from kitchen.domain.cards import check_nutrients, label_fields_from_extraction
from kitchen.llm.label import LABEL_PROMPT_VERSION, LABEL_PURPOSE, label_reader_from_settings
from kitchen.llm.polza import TOTAL_DEADLINE_SECONDS
from kitchen.sync.drive import DriveClient
from kitchen.web import auth, cards
from kitchen.web.app import create_app
from tests.fake_drive import FOLDER_ID, FakeDrive
from tests.fake_polza import BASE_URL, KEY, MODEL, FakePolza, ok, refusal
from tests.integration.test_cards_drafts_api import (
    CSRF,
    JPEG,
    JPEG_2,
    current,
    patch,
    photo_url,
    put_photo,
    start,
    uploaded,
)
from tests.integration.test_database import _url

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration

COOK = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")

MODEL_IN_SETTINGS = "vision-test-model"
"""Модель из настроек. Шлюз отвечает своим именем (``MODEL`` фальшивки) — в
журнал идёт это, а не то: ответ шлюза — чужой текст."""

READING: dict[str, str | None] = {
    "label_name": "Соус томатный «Барбекю»",
    "manufacturer": "Фабрика соусов",
    "composition": "томатная паста, сахар, уксус, соль, специи",
    "proteins": "1,5 г",
    "fats": "0,5 г",
    "carbohydrates": "25 г",
    "kcal": "110 ккал / 460 кДж",
    "nutrition_basis": "100 г",
    "shelf_life_period": None,
    "manufactured_on": "15.06.25",
    "best_before": "15 июня 2026",
    "storage_conditions": "при t -18°C",
    "shelf_life_defrost": None,
    "shelf_life_after": "3 суток",
    "defrost_conditions": None,
}
"""Что модель прочла с этикетки: числа и даты — как напечатаны."""


# ---------------------------------------------------------------------------
# Окружение
# ---------------------------------------------------------------------------
@pytest.fixture
def drive() -> FakeDrive:
    return FakeDrive()


@pytest.fixture
def people(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as session:
        session.add(models.Profile(id=COOK, email="cook@example.com", display_name="Повар"))
        session.add(models.Profile(id=OTHER, email="cook2@example.com", display_name="Повар 2"))


def settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": _url(),
        "drive_cards_folder_id": FOLDER_ID,
        "polza_api_key": KEY,
        "polza_base_url": BASE_URL,
        "llm_vision_model": MODEL_IN_SETTINGS,
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def make_client(
    drive: FakeDrive,
    transport: httpx.BaseTransport | None,
    *,
    user: uuid.UUID | None = COOK,
    roles: tuple[str, ...] = ("cook",),
    config: Settings | None = None,
) -> TestClient:
    """Приложение с фальшивыми Drive и polza.ai.

    ``transport=None`` — чтец этикеток не подменяется: приложение собирает
    его само из настроек (так проверяется «ключа нет — не настроено»)."""
    config = config or settings()
    app = create_app(config)
    app.dependency_overrides[cards.get_drive] = lambda: DriveClient(
        drive.session, folder_id=FOLDER_ID, timeout=(5, 30)
    )
    if transport is not None:
        reader = label_reader_from_settings(config, transport=transport)
        app.dependency_overrides[cards.get_label_reader] = lambda: reader
    if user is not None:
        app.dependency_overrides[auth.current_user] = lambda: auth.CurrentUser(
            id=user, email="cook@example.com", display_name="Повар", roles=frozenset(roles)
        )
    return TestClient(app)


def answer(**changes: str | None) -> httpx.Response:
    """Ответ модели: ``READING`` с поправками."""
    return ok(content=json.dumps({**READING, **changes}, ensure_ascii=False))


def labelled(client: TestClient) -> dict[str, object]:
    """Черновик с поставщиком, названием и фото этикетки."""
    draft = start(client)
    assert patch(client, draft["id"], supplier="Метро", name="Соус Барбекю").status_code == 200
    assert put_photo(client, draft["id"]).status_code == 200
    return draft


def recognize(client: TestClient, draft_id: object) -> httpx.Response:
    return client.post(f"/api/cards/recognize/{draft_id}", headers=CSRF)


def journal(sessions: sessionmaker[Session]) -> list[models.LlmCall]:
    with sessions() as session:
        return list(session.scalars(select(models.LlmCall).order_by(models.LlmCall.id)))


def draft_row(sessions: sessionmaker[Session], draft_id: object) -> models.CardDraft:
    with sessions() as session:
        draft = session.get(models.CardDraft, uuid.UUID(str(draft_id)))
        assert draft is not None
        return draft


def set_running(sessions: sessionmaker[Session], draft_id: object, ago: timedelta) -> None:
    """Распознавание «идёт» с момента ``ago`` назад — по часам базы."""
    with sessions.begin() as session:
        session.execute(
            update(models.CardDraft)
            .where(models.CardDraft.id == uuid.UUID(str(draft_id)))
            .values(recognition_status="running", recognition_started_at=func.now() - ago)
        )


def media_requests(drive: FakeDrive) -> int:
    """Сколько раз из Drive скачивали содержимое файла."""
    return sum(1 for sent in drive.sent if sent.params.get("alt") == "media")


# ---------------------------------------------------------------------------
# Удачное распознавание
# ---------------------------------------------------------------------------
def test_fields_come_from_domain_parsing_and_code_counts_shelf_life(
    people: None, drive: FakeDrive
) -> None:
    polza = FakePolza(answer())
    client = make_client(drive, polza.transport())
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 200, reply.text
    body = reply.json()
    values, notes = label_fields_from_extraction(READING)
    assert body["recognition_status"] == "done"
    assert body["recognition_error"] is None
    for field in ("label_name", "manufacturer", "composition", "shelf_life_after"):
        assert body[field] == values[field]
    # КБЖУ разобрал код: «1,5 г» → 1.5, «110 ккал / 460 кДж» → 110; строками.
    assert (body["protein"], body["fat"], body["carbs"], body["kcal"]) == (
        "1.5",
        "0.5",
        "25",
        "110",
    )
    # Модель переписала даты как напечатаны; период и склонение — код.
    assert body["shelf_life_sealed"] == values["shelf_life_sealed"]
    assert body["shelf_life_sealed"].startswith("12 месяцев")
    assert body["warnings"] == list(notes)
    # Модели ушло фото этикетки из черновика и ничего больше.
    [request] = polza.bodies()
    image = request["messages"][1]["content"][0]["image_url"]["url"]  # type: ignore[index]
    assert image == "data:image/jpeg;base64," + base64.b64encode(JPEG).decode("ascii")
    assert request["model"] == MODEL_IN_SETTINGS


def test_result_lives_in_the_draft(people: None, drive: FakeDrive) -> None:
    """Телефон уснул посреди распознавания — результат не потерян: черновик
    отдаёт его при следующем открытии."""
    client = make_client(drive, FakePolza(answer()).transport())
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert current(client) == reply.json()


def test_success_is_journaled_with_model_from_settings(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    client = make_client(drive, FakePolza(answer()).transport())
    draft = labelled(client)

    assert recognize(client, draft["id"]).status_code == 200

    [call] = journal(sessions)
    assert call.ok is True
    assert call.error == ""
    assert call.model == MODEL_IN_SETTINGS, "модель — из настроек, а не из ответа шлюза"
    assert MODEL != MODEL_IN_SETTINGS
    assert (call.purpose, call.prompt_version, call.profile_id) == (
        LABEL_PURPOSE,
        LABEL_PROMPT_VERSION,
        COOK,
    )
    assert (call.cost_rub, call.unpriced_attempts, call.tokens) == (Decimal("0.0123"), 0, 1200)
    assert call.duration_ms is not None


def test_check_of_numbers_is_not_stored_but_shown(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """В черновике хранятся только замечания самой этикетки; проверка КБЖУ
    считается при выдаче — по числам, что стоят в черновике сейчас."""
    client = make_client(drive, FakePolza(answer(proteins="120", fats="<0,5")).transport())
    draft = labelled(client)

    body = recognize(client, draft["id"]).json()

    checks = list(check_nutrients(Decimal("120"), None, Decimal("25"), Decimal("110")))
    assert checks, "белки 120 г на 100 г — проверка это замечает"
    _, notes = label_fields_from_extraction({**READING, "proteins": "120", "fats": "<0,5"})
    label_notes = [note for note in notes if note not in checks]
    assert any("Жиры" in note for note in label_notes)
    assert draft_row(sessions, draft["id"]).recognition_warnings == label_notes
    assert body["warnings"] == label_notes + checks

    fixed = patch(client, draft["id"], protein="12").json()["warnings"]

    assert fixed == label_notes + list(
        check_nutrients(Decimal("12"), None, Decimal("25"), Decimal("110"))
    )


# ---------------------------------------------------------------------------
# Отказы модели
# ---------------------------------------------------------------------------
def test_refusal_is_journaled_and_cook_fills_fields_by_hand(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    client = make_client(drive, FakePolza(refusal(402, "Insufficient balance")).transport())
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 502
    detail = reply.json()["detail"]
    assert "кончились деньги" in detail
    assert "вручную" in detail
    [call] = journal(sessions)
    assert call.ok is False
    assert call.error.startswith("no_money 402")
    assert "Insufficient balance" in call.error
    assert call.cost_rub == Decimal("0")
    assert call.model == MODEL_IN_SETTINGS
    after = current(client)
    assert after is not None
    assert after["recognition_status"] == "failed"
    assert after["recognition_error"] == detail
    assert after["label_name"] == ""


def test_garbage_reply_is_502_and_its_price_is_journaled(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    client = make_client(drive, FakePolza(ok(content="Не могу прочитать этикетку")).transport())
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 502
    assert "вручную" in reply.json()["detail"]
    [call] = journal(sessions)
    assert (call.ok, call.cost_rub) == (False, Decimal("0.0123"))
    assert call.error.startswith("garbage")


def test_timeouts_are_journaled_with_unpriced_attempts(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Два таймаута: цена обеих попыток неизвестна — журнал это помнит, и
    бюджет посчитает их по оценке."""
    polza = FakePolza(httpx.ReadTimeout, httpx.ReadTimeout)
    client = make_client(drive, polza.transport())
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 502
    assert "не отвечает" in reply.json()["detail"]
    assert len(polza.requests) == 2
    [call] = journal(sessions)
    assert (call.ok, call.cost_rub, call.unpriced_attempts) == (False, None, 1)


# ---------------------------------------------------------------------------
# Бюджет и лимит — до вызова
# ---------------------------------------------------------------------------
def test_spent_budget_is_429_without_calling_the_model(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    with sessions.begin() as session:
        session.add(
            models.LlmCall(
                purpose=LABEL_PURPOSE,
                model=MODEL_IN_SETTINGS,
                prompt_version=LABEL_PROMPT_VERSION,
                ok=True,
                cost_rub=Decimal("300"),
            )
        )
    polza = FakePolza(answer())
    client = make_client(drive, polza.transport())
    draft = labelled(client)
    downloads = media_requests(drive)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 429
    assert "бюджет" in reply.json()["detail"]
    assert "вручную" in reply.json()["detail"]
    assert polza.requests == [], "модель не звали"
    assert media_requests(drive) == downloads, "и фото из Drive не качали"
    assert len(journal(sessions)) == 1
    assert current(client)["recognition_status"] is None  # type: ignore[index]


def test_cook_limit_is_429_without_calling_the_model(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    with sessions.begin() as session:
        for _ in range(2):
            session.add(
                models.LlmCall(
                    purpose=LABEL_PURPOSE,
                    model=MODEL_IN_SETTINGS,
                    prompt_version=LABEL_PROMPT_VERSION,
                    profile_id=COOK,
                    ok=True,
                    cost_rub=Decimal("0.01"),
                )
            )
    polza = FakePolza(answer())
    client = make_client(
        drive, polza.transport(), config=settings(llm_label_calls_per_user_daily=2)
    )
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 429
    assert "предел на день" in reply.json()["detail"]
    assert polza.requests == []


def test_no_polza_key_is_503_and_budget_untouched(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    client = make_client(drive, None, config=settings(polza_api_key=""))
    draft = labelled(client)
    downloads = media_requests(drive)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 503
    assert reply.json() == {"detail": "Распознавание не настроено — заполните поля вручную"}
    assert journal(sessions) == []
    assert media_requests(drive) == downloads


# ---------------------------------------------------------------------------
# Идёт, зависло, нет этикетки
# ---------------------------------------------------------------------------
def test_repeat_while_running_is_409(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    polza = FakePolza(answer())
    client = make_client(drive, polza.transport())
    draft = labelled(client)
    set_running(sessions, draft["id"], timedelta(seconds=5))

    reply = recognize(client, draft["id"])

    assert reply.status_code == 409
    assert "уже распознаётся" in reply.json()["detail"]
    assert polza.requests == []
    assert journal(sessions) == []


def test_running_left_by_dead_process_does_not_lock_the_draft(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Процесс умер посреди вызова — «идёт» осталось навсегда. Старше общего
    срока вызова с запасом — повтор разрешён, иначе черновик заперт."""
    assert timedelta(seconds=TOTAL_DEADLINE_SECONDS) < STALE_AFTER <= timedelta(minutes=5)
    client = make_client(drive, FakePolza(answer()).transport())
    draft = labelled(client)
    set_running(sessions, draft["id"], STALE_AFTER + timedelta(seconds=30))

    reply = recognize(client, draft["id"])

    assert reply.status_code == 200, reply.text
    assert reply.json()["recognition_status"] == "done"


def test_no_label_is_409(people: None, drive: FakeDrive) -> None:
    polza = FakePolza(answer())
    client = make_client(drive, polza.transport())
    draft = start(client)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 409
    assert "этикетк" in reply.json()["detail"]
    assert polza.requests == []


def test_drive_failure_is_502_without_calling_the_model(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    polza = FakePolza(answer())
    client = make_client(drive, polza.transport())
    draft = labelled(client)
    drive.fail_next(503, "backendError", method="GET")

    reply = recognize(client, draft["id"])

    assert reply.status_code == 502
    assert reply.json() == {"detail": "Хранилище фото недоступно — попробуйте позже"}
    assert polza.requests == []
    assert journal(sessions) == []
    assert current(client)["recognition_status"] is None  # type: ignore[index]


@pytest.mark.parametrize(
    ("meanwhile", "text"),
    [
        ("replaced", "заменили"),
        ("started", "уже распознаётся"),
        ("submitting", "Карточка отправляется"),
    ],
)
def test_change_while_label_was_downloading_is_409(
    people: None,
    drive: FakeDrive,
    sessions: sessionmaker[Session],
    meanwhile: str,
    text: str,
) -> None:
    """Пока фото скачивалось для модели, этикетку переснял сам повар,
    распознавание запустила вторая вкладка или карточку отправили. Модель не
    зовётся: читать скачанное незачем, а второй вызов — лишние деньги."""
    polza = FakePolza(answer())
    client = make_client(drive, polza.transport())
    draft = labelled(client)

    class ChangedMeanwhile(DriveClient):
        def download(self, file_id: str, *, max_bytes: int) -> bytes:
            content = super().download(file_id, max_bytes=max_bytes)
            if meanwhile == "replaced":
                with sessions.begin() as session:
                    record = session.get(models.CardDraft, uuid.UUID(str(draft["id"])))
                    assert record is not None
                    record.label_file_id = "another-label"
            elif meanwhile == "started":
                set_running(sessions, draft["id"], timedelta(0))
            else:
                with sessions.begin() as session:
                    session.execute(
                        update(models.CardDraft)
                        .where(models.CardDraft.id == uuid.UUID(str(draft["id"])))
                        .values(submit_started_at=func.now())
                    )
            return content

    client.app.dependency_overrides[cards.get_drive] = lambda: ChangedMeanwhile(  # type: ignore[attr-defined]
        drive.session, folder_id=FOLDER_ID, timeout=(5, 30)
    )

    reply = recognize(client, draft["id"])

    assert reply.status_code == 409
    assert text in reply.json()["detail"]
    assert polza.requests == []
    assert journal(sessions) == []


def test_unexpected_failure_is_journaled_and_releases_the_draft(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Поломка кода посреди вызова модели: деньги, возможно, списаны — строка
    журнала с неизвестной ценой; «идёт» снято, повар видит, что делать."""

    class BrokenReader:
        purpose = LABEL_PURPOSE
        model = MODEL_IN_SETTINGS
        prompt_version = LABEL_PROMPT_VERSION

        def read(self, jpeg: bytes) -> object:
            raise RuntimeError("поломка кода")

    client = make_client(drive, FakePolza(answer()).transport())
    client.app.dependency_overrides[cards.get_label_reader] = BrokenReader  # type: ignore[attr-defined]
    draft = labelled(client)

    with pytest.raises(RuntimeError, match="поломка кода"):
        recognize(client, draft["id"])

    [call] = journal(sessions)
    assert (call.ok, call.cost_rub, call.model) == (False, None, MODEL_IN_SETTINGS)
    assert call.error == "сбой распознавания: RuntimeError"
    after = current(client)
    assert after is not None
    assert after["recognition_status"] == "failed"
    assert "попробуйте ещё раз" in after["recognition_error"]


# ---------------------------------------------------------------------------
# Замена этикетки
# ---------------------------------------------------------------------------
def test_replacing_label_resets_recognition(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Новая этикетка — прежнее распознавание о другой: статус, данные и
    замечания этикетки уходят. Проверка КБЖУ остаётся — числа в черновике те же."""
    client = make_client(drive, FakePolza(answer(proteins="120", fats="<0,5")).transport())
    draft = labelled(client)
    recognized = recognize(client, draft["id"]).json()
    assert recognized["recognition_status"] == "done"

    reply = put_photo(client, draft["id"], content=JPEG_2)

    assert reply.status_code == 200
    body = reply.json()
    assert (body["recognition_status"], body["recognition_error"]) == (None, None)
    assert body["warnings"] == list(
        check_nutrients(Decimal("120"), None, Decimal("25"), Decimal("110"))
    )
    assert body["protein"] == "120", "поля повар уже видел и мог править — они остаются"
    record = draft_row(sessions, draft["id"])
    assert (record.recognition, record.recognition_warnings, record.recognition_started_at) == (
        None,
        [],
        None,
    )


def test_result_for_replaced_label_does_not_land(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Пока модель читала старую этикетку, повар её переснял: прочитанное о
    старой не ложится поверх новой. Деньги за вызов всё равно в журнале."""
    replaced: list[int] = []
    holder: dict[str, TestClient] = {}

    def model(request: httpx.Request) -> httpx.Response:
        client = holder["client"]
        draft_id = current(client)["id"]  # type: ignore[index]
        replaced.append(put_photo(client, draft_id, content=JPEG_2).status_code)
        return answer()

    client = make_client(drive, httpx.MockTransport(model))
    holder["client"] = client
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert replaced == [200]
    assert reply.status_code == 409
    assert "заменили" in reply.json()["detail"]
    after = current(client)
    assert after is not None
    assert (after["label_name"], after["protein"], after["recognition_status"]) == ("", None, None)
    assert len(uploaded(drive)) == 2
    [call] = journal(sessions)
    assert call.ok is True


def test_removing_label_resets_recognition(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Убрали этикетку — распознавание о фото, которого нет: сбрасывается, как
    при замене. Проверка КБЖУ остаётся — числа в черновике те же."""
    client = make_client(drive, FakePolza(answer(proteins="120", fats="<0,5")).transport())
    draft = labelled(client)
    assert recognize(client, draft["id"]).json()["recognition_status"] == "done"

    reply = client.delete(photo_url(draft["id"]), headers=CSRF)

    assert reply.status_code == 200
    body = reply.json()
    assert (body["recognition_status"], body["recognition_error"]) == (None, None)
    assert body["warnings"] == list(
        check_nutrients(Decimal("120"), None, Decimal("25"), Decimal("110"))
    )
    record = draft_row(sessions, draft["id"])
    assert (record.recognition, record.recognition_warnings) == (None, [])


def test_label_removed_while_model_reads_leaves_nothing_running(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Повар убрал этикетку, пока модель её читала: итог не ложится, и «идёт»
    не висит до предела — черновик сразу свободен."""
    holder: dict[str, TestClient] = {}

    def model(request: httpx.Request) -> httpx.Response:
        client = holder["client"]
        draft_id = current(client)["id"]  # type: ignore[index]
        assert client.delete(photo_url(draft_id), headers=CSRF).status_code == 200
        return answer()

    client = make_client(drive, httpx.MockTransport(model))
    holder["client"] = client
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 409
    assert "убрали" in reply.json()["detail"]
    record = draft_row(sessions, draft["id"])
    assert (record.recognition_status, record.label_name) == (None, "")


def test_result_for_changed_label_clears_its_own_running(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Этикетку сменили в обход сброса распознавания (замена и удаление фото
    сбрасывают его сами — это страховка): запуск, чей итог не лёг, снимает своё
    «идёт», а не оставляет его висеть до предела."""

    def model(request: httpx.Request) -> httpx.Response:
        with sessions.begin() as session:
            session.execute(
                update(models.CardDraft)
                .where(models.CardDraft.status == "active")
                .values(label_file_id="another-label")
            )
        return answer()

    client = make_client(drive, httpx.MockTransport(model))
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 409
    assert "заменили" in reply.json()["detail"]
    record = draft_row(sessions, draft["id"])
    assert (record.recognition_status, record.recognition_started_at) == (None, None)
    assert record.label_name == ""


def test_recognition_waits_while_card_is_being_sent(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Черновик уже уходит в лист: итог распознавания лёг бы в него и пропал.
    409 — и модель не зовётся, бюджет не тратится."""
    polza = FakePolza(answer())
    client = make_client(drive, polza.transport())
    draft = labelled(client)
    with sessions.begin() as session:
        session.execute(
            update(models.CardDraft)
            .where(models.CardDraft.id == uuid.UUID(str(draft["id"])))
            .values(submit_started_at=func.now())
        )

    reply = recognize(client, draft["id"])

    assert reply.status_code == 409
    assert reply.json() == {"detail": "Карточка отправляется — подождите"}
    assert polza.requests == []
    assert journal(sessions) == []


def test_cancel_during_recognition_is_404_and_journaled(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    holder: dict[str, TestClient] = {}

    def model(request: httpx.Request) -> httpx.Response:
        client = holder["client"]
        draft_id = current(client)["id"]  # type: ignore[index]
        assert client.delete(f"/api/cards/drafts/{draft_id}", headers=CSRF).status_code == 204
        return answer()

    client = make_client(drive, httpx.MockTransport(model))
    holder["client"] = client
    draft = labelled(client)

    reply = recognize(client, draft["id"])

    assert reply.status_code == 404
    assert len(journal(sessions)) == 1
    assert draft_row(sessions, draft["id"]).status == "cancelled"


# ---------------------------------------------------------------------------
# Вход, роли, чужой черновик
# ---------------------------------------------------------------------------
def test_without_login_is_401(people: None, drive: FakeDrive) -> None:
    client = make_client(drive, FakePolza(answer()).transport(), user=None)

    assert recognize(client, uuid.uuid4()).status_code == 401


def test_commerce_is_403(people: None, drive: FakeDrive) -> None:
    polza = FakePolza(answer())
    client = make_client(drive, polza.transport(), roles=("commerce",))

    assert recognize(client, uuid.uuid4()).status_code == 403
    assert polza.requests == []


@pytest.mark.parametrize("role", ["chef", "developer"])
def test_chef_and_developer_may_recognize(people: None, drive: FakeDrive, role: str) -> None:
    client = make_client(drive, FakePolza(answer()).transport(), roles=(role,))
    draft = labelled(client)

    assert recognize(client, draft["id"]).status_code == 200


def test_foreign_draft_is_404(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    polza = FakePolza(answer())
    mine = make_client(drive, polza.transport())
    draft = labelled(mine)
    other = make_client(drive, polza.transport(), user=OTHER)

    reply = recognize(other, draft["id"])

    assert reply.status_code == 404
    assert polza.requests == []
    assert journal(sessions) == []


def test_without_csrf_header_is_403(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    polza = FakePolza(answer())
    client = make_client(drive, polza.transport())
    draft = labelled(client)
    client.cookies.set(auth.ACCESS_COOKIE, "token-from-browser")

    reply = client.post(f"/api/cards/recognize/{draft['id']}")

    assert reply.status_code == 403
    assert polza.requests == []


def test_photo_url_of_recognized_draft_still_serves_label(people: None, drive: FakeDrive) -> None:
    client = make_client(drive, FakePolza(answer()).transport())
    draft = labelled(client)
    recognize(client, draft["id"])

    assert client.get(photo_url(draft["id"])).content == JPEG


def test_running_state_is_visible_while_model_reads(
    people: None, drive: FakeDrive, sessions: sessionmaker[Session]
) -> None:
    """Пока модель читает, черновик говорит «идёт» — фронтенд опрашивает его
    и показывает ожидание, а не пустые поля."""
    seen: list[object] = []

    def model(request: httpx.Request) -> httpx.Response:
        with sessions() as session:
            seen.append(
                session.scalar(
                    text("select recognition_status from card_drafts where status = 'active'")
                )
            )
        return answer()

    client = make_client(drive, httpx.MockTransport(model))
    draft = labelled(client)

    assert recognize(client, draft["id"]).status_code == 200
    assert seen == ["running"]
