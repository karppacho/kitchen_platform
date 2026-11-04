"""Ответ `GET /api/sync`: насколько свежи данные на сайте.

Само устройство ответа живёт в слое приложения
(:mod:`kitchen.cards.sync_status`) — его читает и MCP. Здесь остаётся прежнее
место импорта для ручки и тестов.
"""

from __future__ import annotations

from kitchen.cards.sync_status import BookRow, SyncBook, SyncStatus, build_sync_status

__all__ = ["BookRow", "SyncBook", "SyncStatus", "build_sync_status"]
