"""Per-provider reachability + circuit-breaker exposure.

Probes each distinct configured provider with a minimal 1-token chat through the
active BackendEngine, so /health/providers reports *real* upstream reachability
(not just "configured"). Results are cached (TTL) so dashboard polling does not
spam providers; a probe runs on-demand when the cache is stale.

Descoped providers (openai, azure) are skipped by default. Cost per probe is a
single 1-token completion (fractions of a cent).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from sqlalchemy import select

from gateway.core.credentials import get_provider_credential
from gateway.core.registry import ResolvedTarget
from gateway.db.database import async_session
from gateway.db.models import WorkspaceProviderConfig, Workspace
from gateway.runtime import engine, select_engine

# providers we don't actively probe (descoped / no valid creds)
SKIP = {"openai", "azure"}

_TTL = 60.0
_cache: dict[str, Any] = {"checked_at": 0.0, "providers": {}}
_lock = asyncio.Lock()


def _model_from_aliases(aliases: Any) -> str | None:
    """A representative model id from a provider config's `aliases` (dict/str/list)."""
    try:
        if isinstance(aliases, dict) and aliases:
            v = next(iter(aliases.values()), None)
            return v if isinstance(v, str) else None
        if isinstance(aliases, str) and aliases:
            return aliases
        if isinstance(aliases, (list, tuple)) and aliases and isinstance(aliases[0], str):
            return aliases[0]
    except Exception:  # noqa: BLE001
        return None
    return None


def _model_from_chat_models(ws: Any, provider: str) -> str | None:
    """A representative model id for `provider` from a workspace's chat/embedding
    model maps - the actual source of truth (provider-config `aliases` is often
    empty in prod, which left the probe with NO targets -> 'probing providers…'
    forever)."""
    if ws is None:
        return None
    for attr in ("chat_models", "embedding_models"):
        models = getattr(ws, attr, None) or {}
        if not isinstance(models, dict):
            continue
        for spec in models.values():
            targets = spec if isinstance(spec, list) else [spec]
            for t in targets:
                if isinstance(t, dict) and t.get("provider") == provider and isinstance(t.get("model_id"), str):
                    return t["model_id"]
    return None


async def _distinct_provider_targets() -> dict[str, tuple[str, str]]:
    """provider -> (workspace_id, model_id) - one representative config per provider.

    Resolution order per provider: the provider config's `aliases`, else the
    workspace's chat/embedding model map. Without the second source, a workspace
    whose provider config has no `aliases` yields no probe target and the UI is
    stuck on 'probing providers…'."""
    out: dict[str, tuple[str, str]] = {}
    async with async_session() as s:
        rows = (await s.scalars(select(WorkspaceProviderConfig))).all()
        ws_ids = {r.workspace_id for r in rows}
        ws_map: dict[str, Any] = {}
        if ws_ids:
            wss = (await s.scalars(select(Workspace).where(Workspace.workspace_id.in_(ws_ids)))).all()
            ws_map = {w.workspace_id: w for w in wss}
    for r in rows:
        if r.provider in SKIP or r.provider in out:
            continue
        model_id = _model_from_aliases((r.config or {}).get("aliases")) \
            or _model_from_chat_models(ws_map.get(r.workspace_id), r.provider)
        if model_id:
            out[r.provider] = (r.workspace_id, model_id)
    return out


async def _probe_one(provider: str, workspace_id: str, model_id: str) -> dict[str, Any]:
    # workspace_id is required so the LiteLLM engine selects the workspace's SYNCED
    # model by its scoped name (ws-{ws}--{provider}--{model}); without it the engine
    # falls back to the bare model id, which the proxy rejects as "Invalid model name".
    target = ResolvedTarget(provider=provider, model_id=model_id,
                            context_window=100_000, workspace_id=workspace_id)
    cred = await get_provider_credential(workspace_id, provider)
    if cred:
        target.credentials = cred.credentials
        target.config = cred.config
        target.bifrost_key_name = cred.bifrost_key_name
    body = {"model": model_id, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    t0 = time.monotonic()
    try:
        # Route per-provider using the GATEWAY-WIDE engine routing (same setting the
        # request path uses): direct-only providers (e.g. vertex_ai) always resolve
        # to our DirectEngine, never the rented Bifrost (which can't serve them).
        from gateway.core.engine_routing import get_overrides
        eng = select_engine(get_overrides(), provider)
        result = await asyncio.wait_for(eng.chat(body, target), timeout=15)
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if result.ok:
            return {"reachable": True, "latency_ms": latency_ms, "model_id": model_id}
        err = (result.body.get("error") or {}).get("message", "unknown")
        return {"reachable": False, "latency_ms": latency_ms, "model_id": model_id, "error": err}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "model_id": model_id, "error": str(exc)}


def _breaker_state() -> dict[str, Any]:
    from gateway.core.fallback import breaker
    out: dict[str, Any] = {}
    for key, b in breaker._b.items():  # noqa: SLF001
        out[key] = {"fails": b.fails, "open": breaker.is_open(*key.split(":", 1)) if ":" in key else False}
    return out


async def snapshot(force: bool = False) -> dict[str, Any]:
    """Return cached per-provider health, refreshing via live probe when stale."""
    if not force and (time.monotonic() - _cache["checked_at"]) < _TTL and _cache["providers"]:
        return {"providers": _cache["providers"], "breakers": _breaker_state(),
                "checked_at": _cache["checked_at"], "cached": True}
    async with _lock:
        # re-check after acquiring the lock (another caller may have refreshed)
        if not force and (time.monotonic() - _cache["checked_at"]) < _TTL and _cache["providers"]:
            return {"providers": _cache["providers"], "breakers": _breaker_state(),
                    "checked_at": _cache["checked_at"], "cached": True}
        targets = await _distinct_provider_targets()
        results = await asyncio.gather(
            *(_probe_one(p, ws, mid) for p, (ws, mid) in targets.items()),
            return_exceptions=True,
        )
        providers: dict[str, Any] = {}
        for (p, _), res in zip(targets.items(), results):
            providers[p] = res if isinstance(res, dict) else {"reachable": False, "error": str(res)}
        _cache["providers"] = providers
        _cache["checked_at"] = time.monotonic()
    return {"providers": _cache["providers"], "breakers": _breaker_state(),
            "checked_at": _cache["checked_at"], "cached": False}
