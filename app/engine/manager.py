"""EngineManager: жизненный цикл фоновых задач движка."""
from __future__ import annotations

import asyncio

from .. import db
from ..logging_setup import get_logger
from .symposium import SymposiumEngine

log = get_logger("engine.manager")


class EngineManager:
    def __init__(self) -> None:
        self.engines: dict[int, SymposiumEngine] = {}
        self.tasks: dict[int, asyncio.Task] = {}

    def ensure(self, symposium_id: int) -> SymposiumEngine:
        eng = self.engines.get(symposium_id)
        if eng is None:
            eng = SymposiumEngine(symposium_id)
            self.engines[symposium_id] = eng
        task = self.tasks.get(symposium_id)
        if task is None or task.done():
            self.tasks[symposium_id] = asyncio.create_task(eng.run(), name=f"engine-{symposium_id}")
        return eng

    def wake(self, symposium_id: int) -> None:
        eng = self.engines.get(symposium_id)
        if eng:
            eng.wake()

    async def stop(self, symposium_id: int) -> None:
        eng = self.engines.pop(symposium_id, None)
        task = self.tasks.pop(symposium_id, None)
        if eng:
            eng._stopped = True
            eng.wake()
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def resume_active(self) -> None:
        """После рестарта сервера: вернуть движки живым симпозиумам."""
        for row in db.query_all("SELECT id, status FROM symposia WHERE status IN ('active', 'waiting_human')"):
            log.info("возобновление симпозиума %d (%s) после рестарта", row["id"], row["status"])
            self.ensure(row["id"])

    async def shutdown(self) -> None:
        for sid in list(self.tasks.keys()):
            await self.stop(sid)


manager = EngineManager()
