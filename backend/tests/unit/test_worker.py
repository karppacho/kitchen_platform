"""Цикл воркера: расписание, остановка и журнал — без базы и без сети."""

from __future__ import annotations

import logging
import signal
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from psycopg.errors import ConnectionTimeout, InvalidPassword
from sqlalchemy.exc import OperationalError

from kitchen.sync.cycle import BookOutcome, CycleResult
from kitchen.worker import run
from kitchen.worker.run import ChangeLog, run_periodically

if TYPE_CHECKING:
    from collections.abc import Callable


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


def _db_error(cause: Exception) -> OperationalError:
    """Сбой базы, каким его отдаёт SQLAlchemy: снаружи всегда OperationalError,
    настоящий тип — в причине (`raise … from …`)."""
    error = OperationalError("select pg_advisory_xact_lock(:key)", {}, cause)
    error.__cause__ = cause
    return error


def test_same_failure_traced_once_and_recovery_noted(caplog: pytest.LogCaptureFixture) -> None:
    """База лежит час — в логе одна трасса, а не двенадцать, и видно, когда цикл
    снова пошёл: в лог — смена состояния, а не одно и то же каждые 5 минут."""
    stop = StopAfter(waits=4)
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        if len(calls) <= 3:
            # Текст от раза к разу разный, вид сбоя — тот же.
            raise _db_error(ConnectionTimeout(f"connection timeout expired ({len(calls)})"))

    with caplog.at_level(logging.INFO, logger="kitchen.worker"):
        run_periodically(job, 300, stop, FakeClock())

    assert [r.levelname for r in caplog.records] == ["ERROR", "INFO"]
    assert caplog.records[0].exc_info is not None
    assert "снова проходит" in caplog.records[1].getMessage()


def test_new_kind_of_failure_gets_new_trace(caplog: pytest.LogCaptureFixture) -> None:
    """Сбой сменился — новая трасса. Отказ соединения и неверный пароль оба
    приходят OperationalError и различаются только причиной: по внешнему типу
    смена прошла бы молча, и лог врал бы, что база всё ещё не отвечает."""
    stop = StopAfter(waits=4)
    failures = iter(
        [
            _db_error(ConnectionTimeout("connection timeout expired")),
            _db_error(ConnectionTimeout("connection timeout expired")),
            _db_error(InvalidPassword("password authentication failed")),
            KeyError("ошибка в нашем коде"),
        ]
    )

    def job() -> None:
        raise next(failures)

    with caplog.at_level(logging.INFO, logger="kitchen.worker"):
        run_periodically(job, 300, stop, FakeClock())

    traced = [r.exc_info[1] for r in caplog.records if r.exc_info]
    assert [r.levelname for r in caplog.records] == ["ERROR", "ERROR", "ERROR"]
    assert [type(e).__name__ for e in traced] == [
        "OperationalError",
        "OperationalError",
        "KeyError",
    ]
    assert isinstance(traced[1].__cause__, InvalidPassword)


def test_failure_after_recovery_traced_again(caplog: pytest.LogCaptureFixture) -> None:
    """Возврат в строй закрывает эпизод: следующий сбой — снова с трассой, даже
    того же вида."""
    stop = StopAfter(waits=3)
    fails = iter([True, False, True])

    def job() -> None:
        if next(fails):
            raise _db_error(ConnectionTimeout("connection timeout expired"))

    with caplog.at_level(logging.INFO, logger="kitchen.worker"):
        run_periodically(job, 300, stop, FakeClock())

    assert [r.levelname for r in caplog.records] == ["ERROR", "INFO", "ERROR"]


def test_cyclic_cause_chain_does_not_hang(caplog: pytest.LogCaptureFixture) -> None:
    """Петля в цепочке причин (a from b, b from a): вид сбоя всё равно считается.
    Зависни воркер на ней — он молчал бы вечно, а контейнер числился живым."""
    first, second = RuntimeError("первая"), ValueError("вторая")
    first.__cause__, second.__cause__ = second, first
    stop = StopAfter(waits=1)

    def job() -> None:
        raise first

    with caplog.at_level(logging.ERROR, logger="kitchen.worker"):
        run_periodically(job, 300, stop, FakeClock())

    assert [r.levelname for r in caplog.records] == ["ERROR"]


def test_main_wires_cycle_interval_and_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Воркер переносит только изменения (без force) раз в SYNC_INTERVAL_SECONDS.
    SIGTERM и первый Ctrl+C — мягкая остановка; второй Ctrl+C — обычный
    KeyboardInterrupt, иначе зависший цикл на машине разработчика не прервать."""
    settings = SimpleNamespace(database_url="postgresql+psycopg://тест", sync_interval_seconds=120)
    seen: dict[str, object] = {}

    class Cycle:
        def __init__(self, reader: object, sessions: object) -> None:
            seen["parts"] = (reader, sessions)

        def run(self, *, force: bool = False) -> CycleResult:
            seen["force"] = force
            return CycleResult()

    def once(job: Callable[[], None], interval: float, stop: threading.Event) -> None:
        job()
        seen.update(interval=interval, stop=stop)

    monkeypatch.setattr(run, "load_settings", lambda: settings)
    monkeypatch.setattr(run, "reader_from", lambda given: ("читатель", given))
    monkeypatch.setattr(run, "make_session_factory", lambda url: ("сессии", url))
    monkeypatch.setattr(run, "SyncCycle", Cycle)
    monkeypatch.setattr(run, "run_periodically", once)
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        assert run.main() == 0
        stop = seen["stop"]
        assert isinstance(stop, threading.Event)
        assert seen["parts"] == (("читатель", settings), ("сессии", settings.database_url))
        assert seen["force"] is False
        assert seen["interval"] == 120
        assert not stop.is_set()

        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert stop.is_set()

        stop.clear()
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert stop.is_set()
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    finally:
        for sig, handler in before.items():
            signal.signal(sig, handler)


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


def test_changelog_recovery_with_changes_is_written(caplog: pytest.LogCaptureFixture) -> None:
    """Книга вернулась в строй сразу с изменениями: «перенесены изменения» само
    по себе не говорит, что сбой кончился."""
    logger = logging.getLogger("kitchen.worker.test")
    log = ChangeLog(logger)

    with caplog.at_level(logging.INFO, logger="kitchen.worker.test"):
        log.report(_result(kitchen=BookOutcome("failed", "Google не ответил")))
        log.report(_result(kitchen=BookOutcome("imported")))

    assert [r.getMessage() for r in caplog.records][1:] == [
        "kitchen: снова переносится",
        "kitchen: перенесены изменения",
    ]
