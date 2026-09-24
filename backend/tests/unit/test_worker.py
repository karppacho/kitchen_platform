"""Цикл воркера: расписание, остановка и журнал — без базы и без сети."""

from __future__ import annotations

import logging
import threading

import pytest

from kitchen.sync.cycle import BookOutcome, CycleResult
from kitchen.worker.run import ChangeLog, run_periodically


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class StopAfter(threading.Event):
    """Остановка, которая запоминает паузы и срабатывает после n-й."""

    def __init__(self, waits: int) -> None:
        super().__init__()
        self.pauses: list[float | None] = []
        self._waits = waits

    def wait(self, timeout: float | None = None) -> bool:
        self.pauses.append(timeout)
        if len(self.pauses) >= self._waits:
            self.set()
        return self.is_set()


def test_pause_counts_from_cycle_start() -> None:
    """Цикл шёл 40 с — до следующего 260 с, а не 300: расписание не уплывает."""
    clock = FakeClock()
    stop = StopAfter(waits=1)

    def job() -> None:
        clock.now += 40

    run_periodically(job, 300, stop, clock)

    assert stop.pauses == [260]


def test_overlong_cycle_starts_next_at_once() -> None:
    clock = FakeClock()
    stop = StopAfter(waits=1)

    def job() -> None:
        clock.now += 400

    run_periodically(job, 300, stop, clock)

    assert stop.pauses == [0.0]


def test_stop_lets_current_cycle_finish() -> None:
    """SIGTERM посреди цикла: цикл доделывается, нового не начинается."""
    stop = threading.Event()
    finished: list[int] = []

    def job() -> None:
        stop.set()  # сигнал пришёл посреди переноса
        finished.append(1)

    run_periodically(job, 300, stop, FakeClock())

    assert finished == [1]


def test_failed_cycle_does_not_stop_worker(caplog: pytest.LogCaptureFixture) -> None:
    stop = StopAfter(waits=2)
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("база недоступна")

    with caplog.at_level(logging.ERROR, logger="kitchen.worker"):
        run_periodically(job, 300, stop, FakeClock())

    assert len(calls) == 2
    assert "база недоступна" in caplog.text


def _result(**outcomes: BookOutcome) -> CycleResult:
    return CycleResult(outcomes=dict(outcomes))


def test_changelog_writes_only_changes(caplog: pytest.LogCaptureFixture) -> None:
    """Одно и то же каждые пять минут в журнале прячет настоящие события."""
    logger = logging.getLogger("kitchen.worker.test")
    log = ChangeLog(logger)

    with caplog.at_level(logging.INFO, logger="kitchen.worker.test"):
        log.report(_result(kitchen=BookOutcome("imported")))
        log.report(_result(kitchen=BookOutcome("unchanged")))
        log.report(_result(kitchen=BookOutcome("failed", "Google не ответил")))
        log.report(_result(kitchen=BookOutcome("failed", "Google не ответил")))
        log.report(_result(kitchen=BookOutcome("failed", "листа «ТТК» нет в таблице")))
        log.report(_result(kitchen=BookOutcome("unchanged")))
        log.report(_result(kitchen=BookOutcome("unchanged")))

    assert [r.levelname for r in caplog.records] == ["INFO", "WARNING", "WARNING", "INFO"]
    assert "Google не ответил" in caplog.records[1].getMessage()
    assert "листа «ТТК» нет в таблице" in caplog.records[2].getMessage()


def test_changelog_puts_raw_error_next_to_problem(caplog: pytest.LogCaptureFixture) -> None:
    """Перевод для шефа сводит разные отказы к одной фразе: «доступ закрыт» — и
    нет доступа, и выключенный API. Чинят по исходному тексту — он в логе рядом."""
    logger = logging.getLogger("kitchen.worker.test")
    log = ChangeLog(logger)
    access = "доступ платформы к таблице закрыт — проверьте, что сервисному аккаунту открыт доступ"
    raw = "не открылась таблица: APIError: [403] Google Sheets API has not been used in project 1"

    with caplog.at_level(logging.WARNING, logger="kitchen.worker.test"):
        log.report(
            _result(
                kitchen=BookOutcome("failed", access, raw),
                ingredient_cards=BookOutcome("failed", "Google не ответил"),
            )
        )
        # Исходник той же беды от цикла к циклу разный (в тексте сетевой ошибки —
        # адрес объекта в памяти): новизна — по причине, иначе запись каждые 5 минут.
        log.report(
            _result(
                kitchen=BookOutcome("failed", access, f"{raw} (0x7f3a)"),
                ingredient_cards=BookOutcome("failed", "Google не ответил"),
            )
        )

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert access in messages[0]
    assert raw in messages[0]
    # Исходника нет — нет и хвоста: ни «None», ни пустых скобок.
    assert messages[1].endswith("не переносится — Google не ответил")


def test_changelog_skips_stale_cycle(caplog: pytest.LogCaptureFixture) -> None:
    """«Устарело» — цикл уступил более свежему ручному импорту и о книге ничего
    не узнал. Сочти его состоянием — и возврат книги в строй пропал бы из лога,
    а тот же сбой после него записался бы второй раз."""
    logger = logging.getLogger("kitchen.worker.test")
    log = ChangeLog(logger)

    with caplog.at_level(logging.INFO, logger="kitchen.worker.test"):
        log.report(_result(kitchen=BookOutcome("failed", "Google не ответил")))
        log.report(_result(kitchen=BookOutcome("stale")))
        log.report(_result(kitchen=BookOutcome("failed", "Google не ответил")))
        log.report(_result(kitchen=BookOutcome("stale")))
        log.report(_result(kitchen=BookOutcome("unchanged")))

    assert [r.levelname for r in caplog.records] == ["WARNING", "INFO"]
    assert "снова переносится" in caplog.records[1].getMessage()
