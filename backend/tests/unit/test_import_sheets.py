"""Ручной импорт: один цикл с принудительным переносом — без базы и без сети."""

from __future__ import annotations

from types import SimpleNamespace

import import_sheets
import pytest

from kitchen.sync.cycle import BookOutcome, CycleResult
from kitchen.sync.importer import ImportResult

ACCESS = "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"


def _run(monkeypatch: pytest.MonkeyPatch, result: CycleResult) -> tuple[int, list[bool]]:
    """Код выхода `main()` на готовом итоге цикла и `force` каждого запуска.

    Настройки — заглушка: настоящие читали бы окружение и backend/.env, а
    скрипт лишь передаёт их подменённым `reader_from` и `make_session_factory`.
    """
    settings = SimpleNamespace(database_url="postgresql+psycopg://тест")
    forced: list[bool] = []

    class Cycle:
        def __init__(self, reader: object, sessions: object) -> None:
            assert (reader, sessions) == (("читатель", settings), ("сессии", settings.database_url))

        def run(self, *, force: bool = False) -> CycleResult:
            forced.append(force)
            return result

    monkeypatch.setattr(import_sheets, "load_settings", lambda: settings)
    monkeypatch.setattr(import_sheets, "reader_from", lambda given: ("читатель", given))
    monkeypatch.setattr(import_sheets, "make_session_factory", lambda url: ("сессии", url))
    monkeypatch.setattr(import_sheets, "SyncCycle", Cycle)
    return import_sheets.main(), forced


def test_manual_import_forces_transfer_and_shows_hidden_rows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Вручную переносят и без изменений — ради этого и запускают.

    Скрытые строки — своим разделом и раньше замечаний: это единственный след
    массового удаления, а замечаний «пустой id» в живом ING десятки.
    """
    imported = ImportResult(
        run_id=7,
        counts={"ингредиенты": 3},
        warnings=["ING строка 5: пустой id, пропущена"],
        presence=["ING: скрыто строк, которых больше нет в листе, — 2"],
    )
    outcomes = {"kitchen": BookOutcome("imported"), "ingredient_cards": BookOutcome("unchanged")}

    code, forced = _run(monkeypatch, CycleResult(outcomes=outcomes, imported={"kitchen": imported}))
    out = capsys.readouterr().out

    assert forced == [True]
    assert code == 0
    assert "ING: скрыто строк, которых больше нет в листе, — 2" in out
    assert out.index("СКРЫТО И ВОЗВРАЩЕНО (1)") < out.index("ЗАМЕЧАНИЯ (1)")
    assert "sync_runs.id = 7" in out


def test_manual_import_shows_every_transferred_book(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """У каждой книги свой перенос и своя запись журнала — и вывод показывает
    обе целиком: счётчики, скрытое, перемену пар карточек, замечания, номер
    прогона. Потерянный итог одной из книг — это непроверенный перенос."""
    kitchen = ImportResult(
        run_id=11,
        counts={"ингредиенты": 3},
        warnings=["ТТК строка 9: блюда «B404» нет в справочнике"],
        presence=["ING: скрыто строк, которых больше нет в листе, — 1"],
        links=["Карточки: потеряли пару в справочнике — 1"],
    )
    cards = ImportResult(
        run_id=12,
        counts={"карточки: linked": 2},
        warnings=["Карточки строка 5: «Томаты» уже была выше"],
        presence=["Карточки: вернулись в лист строки — 1"],
        links=["Карточки: получили пару в справочнике — 2"],
    )
    outcomes = {"kitchen": BookOutcome("imported"), "ingredient_cards": BookOutcome("imported")}
    result = CycleResult(
        outcomes=outcomes, imported={"kitchen": kitchen, "ingredient_cards": cards}
    )

    code, _ = _run(monkeypatch, result)
    out = capsys.readouterr().out

    assert code == 0
    lines = [
        "ПЕРЕНЕСЕНО: таблица кухни",
        "ингредиенты",
        "ING: скрыто строк, которых больше нет в листе, — 1",
        "Карточки: потеряли пару в справочнике — 1",
        "ТТК строка 9: блюда «B404» нет в справочнике",
        "sync_runs.id = 11",
        "ПЕРЕНЕСЕНО: карточки ингредиентов",
        "карточки: linked",
        "Карточки: вернулись в лист строки — 1",
        "Карточки: получили пару в справочнике — 2",
        "Карточки строка 5: «Томаты» уже была выше",
        "sync_runs.id = 12",
    ]
    positions = [out.find(line) for line in lines]
    assert -1 not in positions, [line for line in lines if line not in out]
    assert positions == sorted(positions), "каждая книга — своим блоком, кухня первой"


def test_manual_import_fails_when_book_not_transferred(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Книга не перенесена — код выхода 1, причина словами и исходный текст.

    Исходник на сайт не идёт, но ручной импорт и запускают, чтобы чинить: по
    одной переведённой причине закрытый доступ не отличить от выключенного API.
    """
    raw = "не открылась таблица: APIError: [403] Google Sheets API has not been used"
    outcomes = {
        "kitchen": BookOutcome("failed", ACCESS, raw),
        "ingredient_cards": BookOutcome("imported"),
    }
    imported = ImportResult(run_id=8, counts={"карточки": 2})

    code, _ = _run(
        monkeypatch, CycleResult(outcomes=outcomes, imported={"ingredient_cards": imported})
    )
    out = capsys.readouterr().out

    assert code == 1
    assert f"НЕ перенесена — {ACCESS}" in out
    assert raw in out
    assert "sync_runs.id = 8" in out, "перенесённая книга показана и при сбое другой"
