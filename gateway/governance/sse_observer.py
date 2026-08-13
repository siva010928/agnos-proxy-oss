"""SSE observer - keeps a bounded history + fans events to live dashboard listeners."""
from __future__ import annotations

import asyncio
import dataclasses
import time
from collections import deque

from gateway.governance.observer import GovernanceEvent, GovernanceObserver


def event_to_dict(event: GovernanceEvent) -> dict:
    d = dataclasses.asdict(event)
    d["event_kind"] = type(event).__name__.replace("Event", "")
    d["ts_ms"] = int(time.time() * 1000)
    return d


class SseObserver(GovernanceObserver):
    def __init__(self, history: int = 200):
        self._listeners: list[asyncio.Queue[dict]] = []
        self._history: deque[dict] = deque(maxlen=history)

    async def on_event(self, event: GovernanceEvent) -> None:
        payload = event_to_dict(event)
        self._history.append(payload)
        for q in list(self._listeners):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def register(self) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)
        for past in self._history:
            try:
                q.put_nowait(past)
            except asyncio.QueueFull:
                break
        self._listeners.append(q)
        return q

    def unregister(self, q: asyncio.Queue[dict]) -> None:
        if q in self._listeners:
            self._listeners.remove(q)
