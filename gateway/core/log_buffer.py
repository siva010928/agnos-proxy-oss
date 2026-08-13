"""In-memory ring buffer of gateway log lines + live broadcast, for the dashboard's
'real logs' panels (Engine Slot swap evidence + Live Attack). Two sources feed it:

  1. a stdlib logging.Handler on the root logger  → ambient logs (uvicorn/httpx/sqla)
  2. explicit log_buffer.record(...) calls        → guaranteed evidence lines we want
     the audience to see (the real engine-swap DB write, the injected SQL + timing).

Admin-gated SSE streams the snapshot + live tail. Nothing here changes behaviour;
it only observes. Values that could be sensitive are masked by the callers.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

_MAX = 800
_buf: deque[dict] = deque(maxlen=_MAX)
_subs: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None
_seq = 0


def _fanout(rec: dict) -> None:
    for q in list(_subs):
        try:
            q.put_nowait(rec)
        except Exception:  # noqa: BLE001 - a slow/full subscriber must never block logging
            pass


def record(msg: str, level: str = "INFO", source: str = "gateway") -> None:
    """Append one line to the ring buffer and broadcast it to live subscribers.
    Safe to call from any thread (hands off to the event loop when needed)."""
    global _seq
    _seq += 1
    rec = {"seq": _seq, "ts": time.time(), "level": level, "source": source, "msg": msg}
    _buf.append(rec)
    if _loop is not None and _loop.is_running():
        try:
            _loop.call_soon_threadsafe(_fanout, rec)
            return
        except Exception:  # noqa: BLE001
            pass
    _fanout(rec)


def snapshot(limit: int = 150) -> list[dict]:
    return list(_buf)[-limit:]


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    _subs.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subs.discard(q)


class BufferHandler(logging.Handler):
    def emit(self, r: logging.LogRecord) -> None:
        try:
            record(self.format(r), level=r.levelname, source=r.name)
        except Exception:  # noqa: BLE001
            pass


def install() -> None:
    """Attach the buffer handler to the root logger (idempotent). Called at startup
    so the capturing loop reference is the running event loop."""
    global _loop
    try:
        _loop = asyncio.get_event_loop()
    except Exception:  # noqa: BLE001
        _loop = None
    root = logging.getLogger()
    if any(isinstance(h, BufferHandler) for h in root.handlers):
        return
    h = BufferHandler()
    h.setFormatter(logging.Formatter("%(name)s %(message)s"))
    h.setLevel(logging.INFO)
    root.addHandler(h)
    # let ambient request/DB logs flow into the tail without spamming DEBUG
    for name in ("httpx", "uvicorn.access", "sqlalchemy.engine.Engine"):
        try:
            logging.getLogger(name).setLevel(logging.INFO)
        except Exception:  # noqa: BLE001
            pass
    record("log tail attached - streaming real gateway logs", source="gateway.logs")
