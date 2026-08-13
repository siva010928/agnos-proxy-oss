"""Workspace authentication - SHA-256 key lookup with in-memory LRU cache.

Fail-open under governance-DB outage: once a key has been resolved, it is kept
in a long-lived *stale* cache so in-flight traffic keeps authenticating even if
Postgres is paused/down. The DB query is bounded by a short timeout so the hot
path never hangs on a dead datastore.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select

from gateway.db.database import async_session
from gateway.db.models import ApiKey, Workspace


@dataclass
class WorkspaceContext:
    workspace_id: str
    name: str
    chat_models: dict[str, Any]
    embedding_models: dict[str, Any]
    default_chat_alias: str | None
    guardrails: dict[str, Any]
    quotas: dict[str, Any]
    budgets: dict[str, Any]
    # WAVE 19 tenancy + governance:
    client_id: str | None = None      # parent Client (NovaTech) for hierarchical budgets
    rate_limits: dict[str, Any] = field(default_factory=dict)   # workspace-wide RPM/TPM
    roles: list[str] = field(default_factory=lambda: ["member"])
    key_id: int | None = None
    user_id: str | None = None     # from JWT sub (or attribution fallback)
    component: str | None = None   # resolved logical component (X-Gateway-Component)
    auth_method: str = "api_key"   # api_key | jwt
    engine_overrides: dict[str, Any] = field(default_factory=dict)  # per-provider engine insourcing


def hash_key(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


# tiny TTL LRU: sha256 -> (WorkspaceContext, expiry); plus a long-lived stale map
_CACHE: dict[str, tuple[WorkspaceContext, float]] = {}
_STALE: dict[str, WorkspaceContext] = {}
_TTL = 30.0
_DB_TIMEOUT = 2.0   # hot-path DB budget; fail-open to stale cache beyond this

# Components are write-once (workspace_id, name) rows. Once we've registered a
# pair we never need to touch the DB again - so remember what we've seen and
# skip the per-request SELECT/commit entirely. This keeps the auth stage off the
# database on the steady-state hot path.
_SEEN_COMPONENTS: set[tuple[str, str]] = set()


def _extract_bearer(auth_header: str | None) -> str:
    if not auth_header:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail={"error": {"message": "Missing Authorization header (Bearer <workspace-key-or-JWT>).",
                                              "type": "authentication_error"}})
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return auth_header.strip()


def _ctx_from_workspace(ws, roles, key_id=None, user_id=None, auth_method="api_key") -> WorkspaceContext:
    return WorkspaceContext(
        workspace_id=ws.workspace_id, name=ws.name,
        chat_models=ws.chat_models or {}, embedding_models=ws.embedding_models or {},
        default_chat_alias=ws.default_chat_alias, guardrails=ws.guardrails or {},
        quotas=ws.quotas or {}, budgets=ws.budgets or {},
        client_id=getattr(ws, "client_id", None),
        rate_limits=getattr(ws, "rate_limits", None) or {},
        roles=list(roles or ["member"]), key_id=key_id, user_id=user_id, auth_method=auth_method,
        engine_overrides=getattr(ws, "engine_overrides", None) or {},
    )


async def _load(digest: str) -> WorkspaceContext:
    async with async_session() as s:
        key_row = await s.scalar(
            select(ApiKey).where(ApiKey.sha256 == digest, ApiKey.disabled == False)  # noqa: E712
        )
        if key_row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                detail={"error": {"message": "Invalid API key.",
                                                  "type": "authentication_error"}})
        # enforce expiry if set
        if key_row.expires_at is not None and key_row.expires_at < __import__("datetime").datetime.utcnow():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                detail={"error": {"message": "API key expired.", "type": "authentication_error"}})
        ws = await s.scalar(select(Workspace).where(Workspace.workspace_id == key_row.workspace_id))
        if ws is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                detail={"error": {"message": "Workspace not found.",
                                                  "type": "authentication_error"}})
    return _ctx_from_workspace(ws, key_row.roles, key_id=key_row.id, auth_method="api_key")


async def resolve_workspace(auth_header: str | None) -> WorkspaceContext:
    api_key = _extract_bearer(auth_header)
    digest = hash_key(api_key)

    cached = _CACHE.get(digest)
    if cached and cached[1] > time.time():
        return cached[0]

    try:
        ctx = await asyncio.wait_for(_load(digest), timeout=_DB_TIMEOUT)
    except HTTPException:
        raise
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - DB slow/down → fail-open to stale
        if cached:
            return cached[0]
        if digest in _STALE:
            return _STALE[digest]
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"error": {"message": "Auth backend temporarily unavailable.",
                                              "type": "service_unavailable"}})
    _CACHE[digest] = (ctx, time.time() + _TTL)
    _STALE[digest] = ctx
    return ctx


async def _resolve_jwt(token: str) -> WorkspaceContext:
    from gateway.config import settings
    from gateway.core.security import decode_bearer_jwt
    claims = decode_bearer_jwt(token)
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail={"error": {"message": "Invalid or unverifiable JWT.",
                                              "type": "authentication_error"}})
    ws_id = claims.get(settings.jwt_workspace_claim) or claims.get("workspace_id")
    if not ws_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail={"error": {"message": "JWT missing workspace claim.",
                                              "type": "authentication_error"}})
    roles = claims.get("roles") or claims.get("scope", "").split() or ["member"]
    user_id = claims.get("sub")
    component = claims.get(settings.jwt_component_claim)
    async with async_session() as s:
        ws = await s.scalar(select(Workspace).where(Workspace.workspace_id == ws_id))
    if ws is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail={"error": {"message": f"Workspace '{ws_id}' not found.",
                                              "type": "authentication_error"}})
    ctx = _ctx_from_workspace(ws, roles, user_id=user_id, auth_method="jwt")
    ctx.component = component
    return ctx


async def _overlay_component(ctx: WorkspaceContext, component: str | None) -> None:
    """Set the component attribution on the context + auto-register it.

    WAVE 20 TRACK 1: components carry ZERO config. No per-(workspace,component)
    override exists. The only role of this function is:
      1. Set ``ctx.component`` so every governance event + RequestLog stamps it.
      2. Auto-register a lightweight (workspace_id, name, first_seen) row so the
         facets endpoint + Analytics filters discover it without an admin manually
         creating it. The upsert is fire-and-forget \u2014 if it fails, attribution
         still works; the facet just won't see the new name until the next success.
    """
    if not component:
        return
    ctx.component = component
    # Fast path: we've already registered this (workspace, component) - no DB.
    seen_key = (ctx.workspace_id, component)
    if seen_key in _SEEN_COMPONENTS:
        return
    # Auto-register (lightweight upsert; never blocks the hot path)
    from gateway.db.models import Component
    try:
        async with async_session() as s:
            existing = await asyncio.wait_for(s.scalar(
                select(Component).where(Component.workspace_id == ctx.workspace_id,
                                        Component.name == component)), timeout=_DB_TIMEOUT)
            if existing is None:
                s.add(Component(workspace_id=ctx.workspace_id, name=component))
                await s.commit()
        _SEEN_COMPONENTS.add(seen_key)   # only cache on a clean DB round-trip
    except Exception:  # noqa: BLE001 - auto-reg must never break the hot path
        pass


async def resolve_principal(auth_header: str | None, headers=None) -> WorkspaceContext:
    """Unified entry: API key OR workspace JWT → effective (component-merged) context.

    Component precedence: X-Gateway-Component header > JWT component claim.
    (API-key→component binding can be added later; header wins regardless.)
    """
    from gateway.core.security import looks_like_jwt
    token = _extract_bearer(auth_header)
    if looks_like_jwt(token):
        ctx = await _resolve_jwt(token)
    else:
        ctx = await resolve_workspace(auth_header)
    header_component = None
    if headers is not None:
        header_component = headers.get("x-gateway-component")
    component = header_component or ctx.component
    await _overlay_component(ctx, component)
    return ctx


def invalidate_cache() -> None:
    _CACHE.clear()
    _SEEN_COMPONENTS.clear()
