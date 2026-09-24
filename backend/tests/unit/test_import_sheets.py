"""Ручной импорт: один цикл с принудительным переносом — без базы и без сети."""

from __future__ import annotations

import import_sheets
import pytest

from kitchen.sync.cycle import BookOutcome, CycleResult
from kitchen.sync.importer import ImportResult

ACCESS = "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"


def _run(monkeypatch: pytest.MonkeyPatch, result: CycleResult) -> tuple[int, list[bool]]:
    """Код выхода `main()` на готовом итоге цикла и `force` каждого запуска."""
    forced: list[bool] = []

    class Cycle:
        def __init__(self, *_: object) -> None:
            pass

        def run(self, *, force: bool = False) -> CycleResult:
            forced.append(force)
            return result

    monkeypatch.setattr(import_sheets, "reader_from", lambda _settings: None)
    monkeypatch.setattr(import_sheets, "make_session_factory", lambda _url: None)
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
    outcomes = {"kitchen": BookOutcome("imported"), "ingredient_cards": BookOutcome("imported")}

    code, forced = _run(monkeypatch, CycleResult(outcomes=outcomes, imported=imported))
    out = capsys.readouterr().out

    assert forced == [True]
    assert code == 0
    assert "ING: скрыто строк, которых больше нет в листе, — 2" in out
    assert out.index("СКРЫТО И ВОЗВРАЩЕНО (1)") < out.index("ЗАМЕЧАНИЯ (1)")
    assert "sync_runs.id = 7" in out


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

    code, _ = _run(monkeypatch, CycleResult(outcomes=outcomes, imported=imported))
    out = capsys.readouterr().out

    assert code == 1
    assert f"НЕ перенесена — {ACCESS}" in out
    assert raw in out
