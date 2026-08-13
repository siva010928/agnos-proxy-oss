"""Required-header enforcement (WAVE 19 TRACK C3).

Some governance setups require every request to carry specific identifying
headers (most commonly ``X-Gateway-Component`` so attribution is unambiguous).
The required list lives on the parent ``Client.required_headers`` so it can
vary per tenant.

Returns ``None`` if the headers are present, else a list of FastAPI-shaped
error dicts ready for ``raise_if(...)`` (HTTP 400 ``missing_required_header``).
"""
from __future__ import annotations

import time
from typing import Iterable

from sqlalchemy import select

from gateway.db.database import async_session
from gateway.db.models import Client


# Client.required_headers changes rarely but was being read from the DB on EVERY
# request (a measurable chunk of hot-path latency). Cache it per client with a
# short TTL so the steady state stays off the database.
_CACHE: dict[str, tuple[list[str], float]] = {}
_TTL = 30.0


def invalidate_required_headers_cache() -> None:
    _CACHE.clear()


async def required_headers_for(client_id: str | None) -> list[str]:
    """Look up the parent Client's required_headers list (empty when no client)."""
    if not client_id:
        return []
    cached = _CACHE.get(client_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    async with async_session() as s:
        row = await s.get(Client, client_id)
    headers = list((row.required_headers if row else None) or [])
    _CACHE[client_id] = (headers, time.monotonic() + _TTL)
    return headers


def missing_required_headers(present: dict, required: Iterable[str]) -> list[str]:
    """Return the list of required headers that are missing or empty."""
    # case-insensitive lookup
    lower_present = {k.lower(): (v or "").strip() for k, v in present.items()}
    missing: list[str] = []
    for name in required:
        if not lower_present.get(name.lower()):
            missing.append(name)
    return missing


def required_headers_error(missing: list[str]) -> dict:
    """Build the 400 OpenAI-clean error body for missing required headers."""
    name = ", ".join(missing)
    return {
        "error": {
            "type": "missing_required_header",
            "code": "missing_required_header",
            "message": (
                f"Required header(s) missing: {name}. "
                f"This client's governance configuration mandates them on every request."
            ),
            "param": missing[0] if missing else None,
        }
    }
