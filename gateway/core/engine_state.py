"""Persisted 'active engine' for the swappable slot.

Whatever engine an operator activates must STICK - across page loads and across a
gateway restart - until they switch again. The live global default lives in
runtime._engine (in-process); this module is its durable backing store in
GatewaySettings, loaded once at startup and written on every swap. This is the
single source of truth for "which engine is serving right now".
"""
from __future__ import annotations

import json
from datetime import datetime

_KEY = "active_engine"
_CACHE: dict[str, str | None] = {"engine": None}


async def load() -> str | None:
    """Warm the cache from the DB at startup; returns the persisted engine or None."""
    from gateway.db.database import async_session
    from gateway.db.models import GatewaySettings
    try:
        async with async_session() as s:
            row = await s.get(GatewaySettings, _KEY)
        val = json.loads(row.value) if (row and row.value) else None
        _CACHE["engine"] = val if isinstance(val, str) else None
    except Exception:  # noqa: BLE001
        _CACHE["engine"] = None
    return _CACHE["engine"]


def get() -> str | None:
    return _CACHE["engine"]


async def set_active(engine: str) -> None:
    """Persist the active engine + refresh the cache."""
    from gateway.db.database import async_session
    from gateway.db.models import GatewaySettings
    payload = json.dumps(engine)
    async with async_session() as s:
        row = await s.get(GatewaySettings, _KEY)
        if row:
            row.value = payload
            row.updated_at = datetime.utcnow()
        else:
            s.add(GatewaySettings(key=_KEY, value=payload))
        await s.commit()
    _CACHE["engine"] = engine
