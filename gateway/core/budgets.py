"""Budget enforcement \u2014 hierarchical $ spend caps (WAVE 19 TRACK C1).

Three levels evaluated in order; **first violation wins**:

    Client.budgets.client_usd     \u2190 cross-workspace cap (the tenant root)
    Workspace.budgets.workspace_usd
    Workspace.budgets.user_usd  (or Client.budgets.user_usd as a default)

Plus per-model caps:

    Workspace.budgets.per_model = {"<model_substr>": <usd>, ...}

Each level's rolling spend is summed from RequestLog over the last 30 days
(default window). The pre-call check is bounded by a 1.5 s timeout so a slow
governance DB never blocks serving \u2014 we fail-open in that case (the bus drop
counter records the gap; reconciled by the normal RequestLog write on success).

Returns ``BudgetDecision(allowed, scope, cap, spend, message)`` so the chat/
embeddings handlers can stamp a clear 402 ``detail`` and the governance event
can carry the right scope label.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from gateway.db.database import async_session
from gateway.db.models import Client, RequestLog


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    scope: str = ""              # client | workspace | user | per_model | ""
    cap: float = 0.0
    spend: float = 0.0
    message: str = ""


# (client_id, workspace_id, user_id, model_substr) \u2192 (spend tuple, expiry)
_CACHE: dict[tuple, tuple[dict, float]] = {}
# Short TTL so consecutive requests inside a burst share a query, but any
# request more than ~0.5 s after the last one re-reads from DB.
_TTL = 0.5
_DB_TIMEOUT = 1.5
_DEFAULT_WINDOW_HOURS = 24 * 30


# ── Live spend delta (correctness fix for the cache-warmth bug) ──
# RequestLog writes are async via the governance bus, so the *next* request
# can arrive before the previous request's row is visible to a SQL SUM. The
# delta below records every just-completed completion's cost in memory; the
# budget check ADDS it to the DB-queried total so caps enforce on the very
# next request, not "eventually". Entries auto-expire after 60 s, by which
# point the DB write has long landed.
#
# Three keys per completion so the per-scope check picks up the right slice:
#   ("client",    client_id,    None,         None)
#   ("workspace", workspace_id, None,         None)
#   ("user",      workspace_id, user_id,      None)
#   ("per_model", workspace_id, None,         model_substr)
_LIVE_DELTA_TTL = 60.0
_LIVE_DELTA: dict[tuple, list[tuple[float, float]]] = {}


def _delta_for(key: tuple) -> float:
    import time as _t
    now = _t.monotonic()
    items = [(c, e) for c, e in _LIVE_DELTA.get(key, []) if e > now]
    _LIVE_DELTA[key] = items
    return sum(c for c, _ in items)


def add_live_spend(client_id: str | None, workspace_id: str, user_id: str | None,
                    cost_usd: float, provider_model_id: str | None) -> None:
    """Record a just-completed request's cost in the live-delta map so the
    next budget check sees it before the async Postgres write lands.

    Idempotent on duplicate calls (different keys, same cost): a single
    completion contributes to multiple per-scope deltas as designed."""
    if cost_usd <= 0:
        return
    import time as _t
    now = _t.monotonic()
    expiry = now + _LIVE_DELTA_TTL
    keys: list[tuple[str, str | None, str | None, str | None]] = [
        ("client",    client_id,    None,    None),
        ("workspace", workspace_id, None,    None),
        ("user",      workspace_id, user_id, None),
    ]
    if provider_model_id:
        keys.append(("per_model", workspace_id, None, provider_model_id))
    for k in keys:
        _LIVE_DELTA.setdefault(k, []).append((cost_usd, expiry))
    # Also invalidate any cached spend snapshot for this tenant so the next
    # check definitely re-reads instead of serving a stale 0 from cache.
    invalidate_spend_cache(client_id, workspace_id, user_id)


def invalidate_spend_cache(client_id: str | None, workspace_id: str,
                            user_id: str | None) -> None:
    """Drop every cached spend entry that overlaps the given tenant slice."""
    drop = []
    for k in list(_CACHE.keys()):
        cid, wid, uid, _ = k
        if (cid == client_id and wid == workspace_id) or \
           (wid == workspace_id and uid == user_id):
            drop.append(k)
    for k in drop:
        _CACHE.pop(k, None)


async def _query_spends(client_id: str | None, workspace_id: str, user_id: str | None,
                        model_substr: str | None, cutoff) -> dict:
    """Return {client, workspace, user, per_model} spend totals for the window.
    NULL cells are 0.0 (bounded by 1 query per dimension; SUM aggregates).
    """
    out = {"client": 0.0, "workspace": 0.0, "user": 0.0, "per_model": 0.0}
    async with async_session() as s:
        if client_id:
            v = await s.scalar(select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0))
                                .where(RequestLog.client_id == client_id,
                                       RequestLog.timestamp >= cutoff))
            out["client"] = float(v or 0.0)
        v = await s.scalar(select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0))
                            .where(RequestLog.workspace_id == workspace_id,
                                   RequestLog.timestamp >= cutoff))
        out["workspace"] = float(v or 0.0)
        if user_id:
            v = await s.scalar(select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0))
                                .where(RequestLog.workspace_id == workspace_id,
                                       RequestLog.user_id == user_id,
                                       RequestLog.timestamp >= cutoff))
            out["user"] = float(v or 0.0)
        if model_substr:
            v = await s.scalar(select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0))
                                .where(RequestLog.workspace_id == workspace_id,
                                       RequestLog.provider_model_id.ilike(f"%{model_substr}%"),
                                       RequestLog.timestamp >= cutoff))
            out["per_model"] = float(v or 0.0)
    return out


async def _client_caps(client_id: str | None) -> dict:
    """Look up the parent Client's budgets + propagate user_usd default."""
    if not client_id:
        return {}
    async with async_session() as s:
        row = await s.get(Client, client_id)
    return (row.budgets or {}) if row else {}


async def _spend(client_id: str | None, workspace_id: str, user_id: str | None,
                  model_substr: str | None) -> dict:
    """Return the per-scope spend totals (DB SUM + live-delta) for the budget
    check. Live-delta exists to close the cache-warmth window: a request that
    completes while its RequestLog row is still being asynchronously committed
    must still count toward the next request's budget check.
    """
    ck = (client_id, workspace_id, user_id, model_substr)
    hit = _CACHE.get(ck)
    if hit and hit[1] > time.monotonic():
        # Even on cache hit, fold in the latest live delta (the delta lives
        # outside the cache and accumulates per completion).
        base = dict(hit[0])
    else:
        cutoff = datetime.utcnow() - timedelta(hours=_DEFAULT_WINDOW_HOURS)
        try:
            base = await asyncio.wait_for(
                _query_spends(client_id, workspace_id, user_id, model_substr, cutoff),
                timeout=_DB_TIMEOUT)
        except (Exception, asyncio.TimeoutError):
            base = {"client": 0.0, "workspace": 0.0, "user": 0.0, "per_model": 0.0}
        _CACHE[ck] = (base, time.monotonic() + _TTL)
    # Add live deltas (recent completions not yet committed to Postgres)
    return {
        "client":    base["client"]    + _delta_for(("client",    client_id,    None,    None)),
        "workspace": base["workspace"] + _delta_for(("workspace", workspace_id, None,    None)),
        "user":      base["user"]      + _delta_for(("user",      workspace_id, user_id, None)),
        "per_model": base["per_model"] + (
            _delta_for(("per_model", workspace_id, None, model_substr)) if model_substr else 0.0
        ),
    }


def _per_model_match(per_model: dict, model_id: str) -> tuple[str, float] | None:
    """Return the matching (substr, cap) if any; first match wins."""
    if not isinstance(per_model, dict) or not model_id:
        return None
    for substr, cap in per_model.items():
        if substr and substr.lower() in model_id.lower():
            try:
                return substr, float(cap)
            except (TypeError, ValueError):
                continue
    return None


async def check_budget(client_id: str | None, workspace_id: str, user_id: str | None,
                       ws_budgets: dict, model_id: str | None = None) -> BudgetDecision:
    """Hierarchical budget check. Fires 80%/100% alerts as a side-effect.

    Resolution order \u2014 first violation wins:
      1. client (Client.budgets.client_usd)
      2. workspace (Workspace.budgets.workspace_usd)
      3. user (Workspace.budgets.user_usd, falling back to Client.budgets.user_usd)
      4. per_model (Workspace.budgets.per_model[<substr>])
    """
    ws_cap = ws_budgets.get("workspace_usd") if ws_budgets else None
    ws_user_cap = ws_budgets.get("user_usd") if ws_budgets else None
    per_model = (ws_budgets or {}).get("per_model") or {}
    pm_match = _per_model_match(per_model, model_id) if model_id else None

    client_budgets = await _client_caps(client_id)
    client_cap = client_budgets.get("client_usd")
    client_user_cap = client_budgets.get("user_usd")
    user_cap = ws_user_cap if ws_user_cap is not None else client_user_cap

    # Fast-path: nothing configured
    if not any([client_cap, ws_cap, user_cap, pm_match]):
        return BudgetDecision(allowed=True)

    spends = await _spend(client_id, workspace_id, user_id,
                           pm_match[0] if pm_match else None)

    # Alerts (side-effect; never block)
    if client_cap:
        _maybe_alert(client_id or workspace_id, "client", spends["client"], float(client_cap),
                     client_id=client_id)
    if ws_cap:
        _maybe_alert(workspace_id, "workspace", spends["workspace"], float(ws_cap),
                     client_id=client_id)
    if user_cap and user_id:
        _maybe_alert(workspace_id, "user", spends["user"], float(user_cap),
                     client_id=client_id, user_id=user_id)

    # Enforce in hierarchical order
    if client_cap is not None and spends["client"] >= float(client_cap):
        return BudgetDecision(False, "client", float(client_cap), spends["client"],
                              f"Client '{client_id}' monthly budget cap reached "
                              f"(${spends['client']:.4f} / ${float(client_cap):.2f}).")
    if ws_cap is not None and spends["workspace"] >= float(ws_cap):
        return BudgetDecision(False, "workspace", float(ws_cap), spends["workspace"],
                              f"Workspace '{workspace_id}' monthly budget cap reached "
                              f"(${spends['workspace']:.4f} / ${float(ws_cap):.2f}).")
    if user_cap is not None and user_id and spends["user"] >= float(user_cap):
        return BudgetDecision(False, "user", float(user_cap), spends["user"],
                              f"User '{user_id}' monthly budget cap reached "
                              f"(${spends['user']:.4f} / ${float(user_cap):.2f}).")
    if pm_match is not None and spends["per_model"] >= pm_match[1]:
        return BudgetDecision(False, "per_model", pm_match[1], spends["per_model"],
                              f"Per-model cap for '{pm_match[0]}' reached "
                              f"(${spends['per_model']:.4f} / ${pm_match[1]:.2f}).")
    return BudgetDecision(allowed=True)


# dedupe alerts per (workspace_or_client, scope, threshold) within a window
_ALERTED: dict[tuple, float] = {}
_ALERT_WINDOW = 3600.0


def _maybe_alert(target: str, scope: str, spend: float, cap: float,
                  client_id: str | None = None, user_id: str | None = None) -> None:
    if cap <= 0:
        return
    pct = spend / cap * 100
    for threshold in (100, 80):
        if pct >= threshold:
            k = (target, scope, threshold)
            now = time.monotonic()
            if _ALERTED.get(k, 0) > now:
                break
            _ALERTED[k] = now + _ALERT_WINDOW
            _fire_alert(target, scope, threshold, spend, cap, client_id, user_id)
            break


def _fire_alert(target: str, scope: str, threshold: int, spend: float, cap: float,
                 client_id: str | None, user_id: str | None) -> None:
    try:
        from gateway.governance.observer import BudgetAlertEvent
        from gateway.runtime import bus
        bus().emit(BudgetAlertEvent(workspace_id=target, scope=scope,
                                    threshold=threshold, spend_usd=spend, cap_usd=cap,
                                    client_id=client_id, user_id=user_id))
    except Exception:  # noqa: BLE001
        pass
    from gateway.config import settings
    if settings.budget_webhook_url:
        async def _post():
            import httpx
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(settings.budget_webhook_url, json={
                    "text": f":rotating_light: budget {threshold}% \u2014 {target} ({scope}): "
                            f"${spend:.2f}/${cap:.2f}"})
        # Only schedule the task when there is a running loop. Tests that drive
        # check_budget via asyncio.run() create+close fresh loops each call;
        # creating a task in one of those loops without it being current would
        # raise "Task got Future attached to a different loop".
        try:
            asyncio.get_running_loop().create_task(_post())
        except RuntimeError:
            pass
