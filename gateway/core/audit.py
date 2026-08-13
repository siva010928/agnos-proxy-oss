"""Immutable admin-action audit log helper."""
from __future__ import annotations

import asyncio

from gateway.db.database import async_session
from gateway.db.models import AuditLog


async def record(principal: str, action: str, target: str = "", detail: dict | None = None) -> None:
    async def _do():
        async with async_session() as s:
            s.add(AuditLog(principal=principal, action=action, target=target, detail=detail or {}))
            await s.commit()
    try:
        await asyncio.wait_for(_do(), timeout=3.0)
    except Exception:  # noqa: BLE001 - audit must never break the admin action
        pass
