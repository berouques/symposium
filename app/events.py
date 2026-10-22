"""In-process event bus feeding SSE connections."""
from __future__ import annotations

import asyncio
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = {}

    def subscribe(self, symposium_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.setdefault(symposium_id, set()).add(q)
        return q

    def unsubscribe(self, symposium_id: int, q: asyncio.Queue) -> None:
        subs = self._subs.get(symposium_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subs.pop(symposium_id, None)

    def publish(self, symposium_id: int, event: dict[str, Any]) -> None:
        for q in list(self._subs.get(symposium_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow consumer: drop event; client refetches on reconnect


bus = EventBus()
