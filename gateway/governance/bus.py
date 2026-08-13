"""Governance bus - fan-out to observers via a bounded queue + worker each.

No raw create_task per event (no leaks). Overflow policy: drop-oldest, counted.
"""
from __future__ import annotations

import asyncio

from gateway.governance.observer import GovernanceEvent, GovernanceObserver


class _ObserverWorker:
    def __init__(self, observer: GovernanceObserver, maxsize: int = 2000):
        self.observer = observer
        self.queue: asyncio.Queue[GovernanceEvent] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.observer.start()
        self._task = asyncio.create_task(self._run())

    async def drain(self, timeout: float = 5.0) -> int:
        """Wait (bounded) for the worker to process buffered events. Returns the
        number of events still pending if the drain timed out."""
        import time as _t
        deadline = _t.monotonic() + timeout
        while not self.queue.empty() and _t.monotonic() < deadline:
            await asyncio.sleep(0.02)
        return self.queue.qsize()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.observer.stop()

    def submit(self, event: GovernanceEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()       # drop oldest
                self.dropped += 1
                try:
                    from gateway.core import metrics as _M
                    _M.GOV_DROPPED.inc()
                except Exception:
                    pass
                self.queue.put_nowait(event)
            except Exception:
                pass

    async def _run(self) -> None:
        while True:
            event = await self.queue.get()
            try:
                await self.observer.on_event(event)
            except Exception as exc:  # noqa: BLE001 - one observer must not break others
                print(f"[governance] {type(self.observer).__name__} failed: {exc}")


class GovernanceBus:
    def __init__(self, observers: list[GovernanceObserver]):
        self._workers = [_ObserverWorker(o) for o in observers]

    async def start(self) -> None:
        for w in self._workers:
            await w.start()

    async def drain(self, timeout: float = 5.0) -> dict[str, int]:
        """Drain all observer queues before shutdown. Returns {observer: pending}."""
        pending = {}
        results = await asyncio.gather(*(w.drain(timeout) for w in self._workers))
        for w, n in zip(self._workers, results):
            pending[type(w.observer).__name__] = n
        return pending

    async def stop(self) -> None:
        for w in self._workers:
            await w.stop()

    def emit(self, event: GovernanceEvent) -> None:
        for w in self._workers:
            w.submit(event)
