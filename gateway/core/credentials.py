"""Resolve per-workspace provider credentials (decrypted) + Bifrost key name.

Fail-open under DB outage: a long-lived stale cache + bounded DB timeout keep
the hot path serving when Postgres is paused/down.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from gateway.db.database import async_session
from gateway.db.models import WorkspaceProviderConfig
from gateway.secrets.store import cipher


@dataclass
class ProviderCredential:
    provider: str
    credentials: dict[str, Any]
    config: dict[str, Any]
    bifrost_key_name: str | None


# TTL cache: (workspace, provider) -> (ProviderCredential, expiry); + long-lived stale
_CACHE: dict[tuple[str, str], tuple[ProviderCredential, float]] = {}
_STALE: dict[tuple[str, str], ProviderCredential] = {}
_TTL = 60.0
_DB_TIMEOUT = 2.0


def invalidate(workspace_id: str | None = None) -> None:
    """Drop cached decrypted credentials so a rotated/updated/deleted provider key
    takes effect on the next request. Clears BOTH the TTL cache and the long-lived
    fail-open stale copy (otherwise a rotate coinciding with a DB outage could still
    serve the old key). Called by the provider add/update/delete admin routes."""
    if workspace_id is None:
        _CACHE.clear()
        _STALE.clear()
    else:
        for k in [k for k in _CACHE if k[0] == workspace_id]:
            _CACHE.pop(k, None)
        for k in [k for k in _STALE if k[0] == workspace_id]:
            _STALE.pop(k, None)


async def _load(workspace_id: str, provider: str) -> ProviderCredential | None:
    async with async_session() as s:
        row = await s.scalar(
            select(WorkspaceProviderConfig).where(
                WorkspaceProviderConfig.workspace_id == workspace_id,
                WorkspaceProviderConfig.provider == provider,
            )
        )
    if row is None:
        return None
    return ProviderCredential(
        provider=provider,
        credentials=cipher().decrypt(row.encrypted_credentials),
        config=row.config or {},
        bifrost_key_name=row.bifrost_key_name,
    )


async def get_provider_credential(workspace_id: str, provider: str) -> ProviderCredential | None:
    ck = (workspace_id, provider)
    hit = _CACHE.get(ck)
    if hit and hit[1] > time.monotonic():
        return hit[0]
    try:
        cred = await asyncio.wait_for(_load(workspace_id, provider), timeout=_DB_TIMEOUT)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - DB down → fail-open to stale
        if hit:
            return hit[0]
        return _STALE.get(ck)
    if cred is None:
        return None
    _CACHE[ck] = (cred, time.monotonic() + _TTL)
    _STALE[ck] = cred
    return cred
