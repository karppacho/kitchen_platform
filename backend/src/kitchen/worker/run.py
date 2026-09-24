"""Воркер: синхронизация «лист → база» раз в SYNC_INTERVAL_SECONDS.

Отдельным процессом, а не внутри сайта: в kitchen_bot планировщик жил в
процессе бота, и падение одного уносило другое.

* Пауза считается от начала цикла — расписание не уплывает на длительность
  переноса.
* Сигнал остановки не рвёт цикл посередине: текущий доделывается, нового нет.
* Упавший цикл не останавливает воркер: база или Google вернутся к
  следующему.
* В журнал — только смена состояния книги, а не одно и то же каждые пять
  минут. У сбоя рядом с причиной — исходный текст ошибки: сайт его не
  показывает, а чинят по нему.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import TYPE_CHECKING

from kitchen.config import load_settings
from kitchen.db.session import make_session_factory
from kitchen.sync.cycle import SyncCycle, reader_from

if TYPE_CHECKING:
    from collections.abc import Callable

    from kitchen.sync.cycle import BookOutcome, CycleResult

log = logging.getLogger("kitchen.worker")


def run_periodically(
    job: Callable[[], None],
    interval: float,
    stop: threading.Event,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    while not stop.is_set():
        started = clock()
        try:
            job()
        except Exception:
            log.exception("цикл синхронизации упал — следующая попытка по расписанию")
        stop.wait(max(0.0, interval - (clock() - started)))


class ChangeLog:
    """Пишет в журнал смену состояния книги, а не каждый цикл."""

    def __init__(self, logger: logging.Logger = log) -> None:
        self._logger = logger
        self._last: dict[str, BookOutcome] = {}

    def report(self, result: CycleResult) -> None:
        for book, outcome in result.outcomes.items():
            if outcome.action == "stale":
                # Цикл уступил более свежему ручному импорту и о книге ничего
                # не узнал: точкой отсчёта остаётся прошлое состояние. Иначе
                # возврат книги в строй пропал бы из лога, а тот же сбой после
                # «устарело» записался бы второй раз.
                continue
            previous = self._last.get(book)
            if outcome.action == "imported":
                self._logger.info("%s: перенесены изменения", book)
            elif outcome.action == "failed" and (
                previous is None or previous.problem != outcome.problem
            ):
                # Исходный текст ошибки — сюда, а на сайт нет: по переведённой
                # причине закрытый доступ не отличить от выключенного API.
                # Новизна же — по причине: исходник той же беды от цикла к
                # циклу разный, и по нему запись шла бы каждые пять минут.
                raw = f" (исходная ошибка: {outcome.details})" if outcome.details else ""
                self._logger.warning("%s: не переносится — %s%s", book, outcome.problem, raw)
            elif (
                outcome.action == "unchanged"
                and previous is not None
                and previous.action == "failed"
            ):
                self._logger.info("%s: снова переносится", book)
            self._last[book] = outcome


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = load_settings()
    cycle = SyncCycle(reader_from(settings), make_session_factory(settings.database_url))
    changes = ChangeLog()
    stop = threading.Event()
    # docker stop шлёт SIGTERM: даём текущему циклу закончиться, а не рвём его.
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    log.info("воркер запущен, цикл раз в %s с", settings.sync_interval_seconds)
    run_periodically(lambda: changes.report(cycle.run()), settings.sync_interval_seconds, stop)
    log.info("воркер остановлен")
    return 0
