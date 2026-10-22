"""Bounded, best-effort asynchronous PostgreSQL market mirror.

No durable delivery guarantee: dropped on queue saturation/process death.
SQLite remains authoritative; this facility never makes trading decisions.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

from .core import Market
from .postgres_market_shadow import mirror_market_postgres

LOG = logging.getLogger("ramon.postgres_market_shadow")


@dataclass(frozen=True)
class ShadowJob:
    dsn: str
    market: Market


class ShadowQueue:
    def __init__(self, capacity: int = 32, writer=None):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._queue: queue.Queue[ShadowJob] = queue.Queue(maxsize=capacity)
        self._writer = writer or mirror_market_postgres
        self._worker = threading.Thread(target=self._run, name="ramon-pg-shadow", daemon=True)
        self._worker.start()

    def submit(self, dsn: str, market: Market) -> bool:
        # Never block the caller, even while PostgreSQL is unavailable.
        try:
            self._queue.put_nowait(ShadowJob(dsn, market))
        except queue.Full:
            LOG.warning("M15_POSTGRES_SHADOW_DROPPED reason=queue_full")
            return False
        return True

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._writer(job.dsn, job.market)
            except Exception as exc:
                LOG.warning("M15_POSTGRES_SHADOW_FAILED type=%s", type(exc).__name__)
            finally:
                self._queue.task_done()


_singleton: ShadowQueue | None = None
_singleton_lock = threading.Lock()


def submit_shadow(dsn: str, market: Market) -> bool:
    global _singleton
    if not dsn:
        return False
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ShadowQueue()
    return _singleton.submit(dsn, market)
