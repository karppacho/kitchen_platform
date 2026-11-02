"""Ручной импорт: один цикл с принудительным переносом — без базы и без сети."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import import_sheets
import pytest

from kitchen.sync.cycle import SHIFT_PROBLEM_START, BookOutcome, CycleResult
from kitchen.sync.importer import ImportResult

if TYPE_CHECKING:
    from collections.abc import Sequence

ACCESS = "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"
SHIFTED = f"{SHIFT_PROBLEM_START}: у 74 id название пришло от другого id — …"
SHIFT_DETAILS = "стр. 5, id 4: «Сырная котлета» → «Фритюрное масло» (было у id 5); …"


def _run(
    monkeypatch: pytest.MonkeyPatch, result: CycleResult, argv: Sequence[str] = ()
) -> tuple[int, list[dict[str, object]]]:
    """Код выхода `main(argv)` на готовом итоге цикла и параметры каждого запуска.

    Настройки — заглушка: настоящие читали бы окружение и backend/.env, а
    скрипт лишь передаёт их подменённым `reader_from` и `make_session_factory`.
    Параметры запуска — как их получил цикл: без флага `accept_shift` не
    передаётся вовсе или передаётся `None` — для цикла это одно и то же.
    """
    settings = SimpleNamespace(database_url="postgresql+psycopg://тест")
    calls: list[dict[str, object]] = []

    class Cycle:
        def __init__(self, reader: object, sessions: object) -> None:
            assert (reader, sessions) == (("читатель", settings), ("сессии", settings.database_url))

        def run(self, *, force: bool = False, accept_shift: int | None = None) -> CycleResult:
            calls.append({"force": force, "accept_shift": accept_shift})
            return result

    monkeypatch.setattr(import_sheets, "load_settings", lambda: settings)
    monkeypatch.setattr(import_sheets, "reader_from", lambda given: ("читатель", given))
    monkeypatch.setattr(import_sheets, "make_session_factory", lambda url: ("сессии", url))
    monkeypatch.setattr(import_sheets, "SyncCycle", Cycle)
    return import_sheets.main(list(argv)), calls


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

    code, calls = _run(monkeypatch, CycleResult(outcomes=outcomes, imported={"kitchen": imported}))
    out = capsys.readouterr().out

    assert calls == [{"force": True, "accept_shift": None}], "без флага сдвиг не принимается"
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
    assert "--accept-shift" not in out, "обход предлагается только при сдвиге строк ING"


# ---------------------------------------------------------------------------
# Ручной обход сдвига строк ING: --accept-shift N
# ---------------------------------------------------------------------------


def test_accept_shift_passes_the_exact_number_to_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = {"kitchen": BookOutcome("unchanged"), "ingredient_cards": BookOutcome("unchanged")}

    code, calls = _run(monkeypatch, CycleResult(outcomes=outcomes), ["--accept-shift", "74"])

    assert calls == [{"force": True, "accept_shift": 74}]
    assert code == 0


@pytest.mark.parametrize("given", ["0", "-1", "abc", "7.5"])
def test_accept_shift_takes_only_a_whole_number_from_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], given: str
) -> None:
    """Сдвиг без единого id не бывает: 0, отрицательное и не число — ошибка
    разбора (код 2), и цикл не запускается вовсе."""
    outcomes = {"kitchen": BookOutcome("unchanged"), "ingredient_cards": BookOutcome("unchanged")}

    with pytest.raises(SystemExit) as stopped:
        _run(monkeypatch, CycleResult(outcomes=outcomes), ["--accept-shift", given])

    assert stopped.value.code == 2
    assert "--accept-shift" in capsys.readouterr().err


def test_accept_shift_is_not_run_on_bad_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ошибка разбора — до настроек и цикла: неверный флаг не переносит ничего."""
    started: list[str] = []
    monkeypatch.setattr(import_sheets, "load_settings", lambda: started.append("настройки"))

    with pytest.raises(SystemExit):
        import_sheets.main(["--accept-shift", "0"])

    assert started == []


def test_shift_refusal_shows_the_number_for_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Сдвиг держит кухню — вывод даёт полный список и число для флага: в
    тексте причины два числа, и id в обоих видах посчитан там дважды."""
    outcomes = {
        "kitchen": BookOutcome("failed", SHIFTED, SHIFT_DETAILS, shift_ids=74),
        "ingredient_cards": BookOutcome("unchanged"),
    }

    code, _ = _run(monkeypatch, CycleResult(outcomes=outcomes))
    out = capsys.readouterr().out

    assert code == 1
    assert f"подробности: {SHIFT_DETAILS}" in out
    hint = f"  {'':24} для обхода (только если лист ING верен, а база старше): --accept-shift 74\n"
    assert hint in out
    assert out.index("подробности:") < out.index("для обхода"), "сначала список, потом флаг"


def test_accepted_shift_is_shown_with_full_list(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Сдвиг принят — на экране, сколько id принято, и полный список: по нему
    сверяют, что приняли."""
    outcomes = {
        "kitchen": BookOutcome(
            "imported", details=SHIFT_DETAILS, shift_ids=74, shift_accepted=True
        ),
        "ingredient_cards": BookOutcome("unchanged"),
    }
    imported = ImportResult(run_id=9, counts={"ингредиенты": 74})

    code, _ = _run(
        monkeypatch,
        CycleResult(outcomes=outcomes, imported={"kitchen": imported}),
        ["--accept-shift", "74"],
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "таблица кухни" in out and "перенесена" in out
    assert f"  {'':24} сдвиг принят вручную: 74 id\n" in out
    assert f"  {'':24} подробности: {SHIFT_DETAILS}\n" in out
    assert out.index("сдвиг принят вручную") < out.index("подробности:")
    assert "для обхода" not in out
    assert "sync_runs.id = 9" in out
