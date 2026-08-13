"""Gateway-WIDE engine routing: which engine serves each provider - the rented
Bifrost sidecar or our owned DirectEngine.

This is a PLATFORM decision, not a per-tenant one: the contract + governance are
identical either way, so the engine that translates a given provider's traffic is
fixed for the whole gateway (uniform across every client + workspace). It is
stored ONCE in GatewaySettings and cached for the request hot path.

Value shape (per provider): '' → rented (Bifrost) · 'direct' → owned · int 1-99 →
canary % to owned · {"direct_pct": N} → same.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

_SETTINGS_KEY = "engine_overrides"
_CACHE: dict[str, Any] = {}


async def load() -> dict[str, Any]:
    """Warm the in-memory cache from GatewaySettings (called on startup)."""
    from gateway.db.database import async_session
    from gateway.db.models import GatewaySettings
    try:
        async with async_session() as s:
            row = await s.get(GatewaySettings, _SETTINGS_KEY)
        parsed = json.loads(row.value) if (row and row.value) else {}
        _CACHE.clear()
        if isinstance(parsed, dict):
            _CACHE.update(parsed)
    except Exception:  # noqa: BLE001
        _CACHE.clear()
    return dict(_CACHE)


def get_overrides() -> dict[str, Any]:
    """Sync cached read for the request path (select_engine)."""
    return dict(_CACHE)


async def set_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    """Persist the gateway-wide engine routing + refresh the cache."""
    from gateway.db.database import async_session
    from gateway.db.models import GatewaySettings
    clean = {k: v for k, v in (overrides or {}).items()}
    payload = json.dumps(clean)
    async with async_session() as s:
        row = await s.get(GatewaySettings, _SETTINGS_KEY)
        if row:
            row.value = payload
            row.updated_at = datetime.utcnow()
        else:
            s.add(GatewaySettings(key=_SETTINGS_KEY, value=payload))
        await s.commit()
    _CACHE.clear()
    _CACHE.update(clean)
    return dict(_CACHE)
