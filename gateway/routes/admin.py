"""Admin + analytics endpoints for the dashboard.

All routes here are gated behind admin auth (admin session, admin token, or admin
key). Workspace analytics + cost data are tenant-sensitive and must never be
world-readable - this used to be the case during development and was caught
during pre-judging review.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import case, func, select

from gateway.core.security import require_admin
from gateway.db.database import async_session
from gateway.db.models import AuditLog, GuardrailViolation, RequestLog, Workspace

# All /admin/* analytics + listing endpoints require admin auth (mirrors the
# /admin CRUD router). The dependency runs on every request to this router.
router = APIRouter(tags=["admin-analytics"], dependencies=[Depends(require_admin)])


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


@router.get("/admin/workspaces")
async def list_workspaces():
    async with async_session() as s:
        rows = (await s.scalars(select(Workspace))).all()
    return {"workspaces": [
        {"workspace_id": w.workspace_id, "client_id": w.client_id,
         "name": w.name, "display_name": w.display_name or w.name,
         "chat_models": w.chat_models, "embedding_models": w.embedding_models,
         "default_chat_alias": w.default_chat_alias,
         "guardrails": w.guardrails, "quotas": w.quotas, "budgets": w.budgets,
         "rate_limits": getattr(w, "rate_limits", None) or {},
         "engine_overrides": getattr(w, "engine_overrides", None) or {}}
        for w in rows
    ]}


@router.get("/admin/stats")
async def stats():
    cutoff = datetime.utcnow() - timedelta(hours=24)
    async with async_session() as s:
        rows = (await s.execute(
            select(
                RequestLog.workspace_id, RequestLog.provider,
                func.count(RequestLog.id).label("requests"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.coalesce(func.avg(RequestLog.latency_ms), 0.0).label("avg_latency_ms"),
            ).where(RequestLog.timestamp >= cutoff)
             .group_by(RequestLog.workspace_id, RequestLog.provider)
        )).all()
    return {"stats": [
        {"workspace_id": r.workspace_id, "provider": r.provider, "requests": r.requests,
         "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
         "cost_usd": round(r.cost_usd, 6), "avg_latency_ms": round(r.avg_latency_ms, 2)}
        for r in rows
    ]}


def _filter_conds(workspace, component, user, model, provider, status,
                   from_=None, to=None, client=None, include_synthetic=False, use_case=None):
    """Shared analytics filter set. Returns a list of SQLAlchemy where-clauses.

    Substring matching for `model` and `user` (admins rarely know the exact
    string); exact match for enum-shaped fields (workspace, component, provider,
    status, client). The `model` filter matches BOTH the workspace alias
    (`model_alias`) and the upstream provider model id (`provider_model_id`)
    \u2014 fixes the bug where the dropdown showed provider model ids but the
    server only filtered on alias, causing every selection to return zero rows.

    By default we exclude rows tagged ``source='synthetic'`` so the analytics
    don't conflate the seeded demo back-fill with real live traffic. Pass
    ``include_synthetic=True`` (UI: "Include synthetic data" toggle) when an
    operator explicitly wants the historical back-fill mixed in.
    """
    conds = []
    if not include_synthetic:
        conds.append(RequestLog.source == "live")
    if from_ is not None:
        conds.append(RequestLog.timestamp >= from_)
    if to is not None:
        conds.append(RequestLog.timestamp <= to)
    if client:
        conds.append(RequestLog.client_id == client)
    if workspace:
        conds.append(RequestLog.workspace_id == workspace)
    if component:
        conds.append(RequestLog.component == component)
    if user:
        # ILIKE substring \u2014 admins shouldn't have to remember a JWT sub verbatim
        conds.append(RequestLog.user_id.ilike(f"%{user}%"))
    if model:
        # Match either the alias OR the upstream provider model id
        conds.append(
            (RequestLog.model_alias.ilike(f"%{model}%"))
            | (RequestLog.provider_model_id.ilike(f"%{model}%"))
        )
    if provider:
        conds.append(RequestLog.provider == provider)
    if status:
        conds.append(RequestLog.status == status)
    if use_case:
        conds.append(RequestLog.use_case == use_case)
    return conds


@router.get("/admin/cost")
async def cost(group_by: str = "workspace",
               client: str | None = None,
               workspace: str | None = None, component: str | None = None,
               user: str | None = None, model: str | None = None,
               provider: str | None = None, status: str | None = None,
               use_case: str | None = None,
               from_: str | None = Query(None, alias="from"), to: str | None = None,
               include_synthetic: bool = False):
    col_map = {
        "client": RequestLog.client_id,
        "workspace": RequestLog.workspace_id,
        "user": RequestLog.user_id,
        "use_case": RequestLog.use_case,
        "model": RequestLog.model_alias,
        "provider": RequestLog.provider,
        "key": RequestLog.key_id,
        "component": RequestLog.component,
    }
    if group_by not in col_map:
        raise HTTPException(400, detail={"error": {
            "type": "invalid_request_error", "code": "unknown_group_by",
            "message": f"group_by must be one of {', '.join(col_map)}",
            "param": "group_by",
        }})
    col = col_map[group_by]
    conds = _filter_conds(workspace, component, user, model, provider, status,
                          _parse_dt(from_), _parse_dt(to), client=client,
                          include_synthetic=include_synthetic, use_case=use_case)
    async with async_session() as s:
        rows = (await s.execute(
            select(col.label("key"),
                   func.count(RequestLog.id).label("requests"),
                   func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                   func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                   func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                   func.coalesce(func.avg(RequestLog.latency_ms), 0.0).label("avg_latency_ms"))
            .where(*conds).group_by(col).order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0.0).desc())
        )).all()
    return {"group_by": group_by, "rows": [
        {"key": r.key, "requests": r.requests, "input_tokens": r.input_tokens,
         "output_tokens": r.output_tokens, "cost_usd": round(r.cost_usd, 6),
         "avg_latency_ms": round(r.avg_latency_ms, 2)} for r in rows
    ]}


@router.get("/admin/usage/timeseries")
async def usage_timeseries(
    client: str | None = None,
    workspace: str | None = None, component: str | None = None,
    user: str | None = None, model: str | None = None,
    provider: str | None = None, status: str | None = None,
    use_case: str | None = None,
    granularity: str = "day",
    from_: str | None = Query(None, alias="from"), to: str | None = None,
    include_synthetic: bool = False,
):
    """Time-bucketed usage for charts: success/error split + cache hits + latency
    percentiles, all filterable. granularity = hour|day."""
    gran = "hour" if granularity == "hour" else "day"
    default_back = timedelta(hours=48) if gran == "hour" else timedelta(days=45)
    start = _parse_dt(from_) or (datetime.utcnow() - default_back)
    end = _parse_dt(to) or datetime.utcnow()
    bucket = func.date_trunc(gran, RequestLog.timestamp).label("bucket")
    conds = _filter_conds(workspace, component, user, model, provider, status,
                          start, end, client=client,
                          include_synthetic=include_synthetic, use_case=use_case)

    def pct(q: float):
        return func.coalesce(
            func.percentile_cont(q).within_group(RequestLog.latency_ms.asc()), 0.0)

    async with async_session() as s:
        rows = (await s.execute(
            select(
                bucket,
                func.count(RequestLog.id).label("requests"),
                func.coalesce(func.sum(case((RequestLog.status == "success", 1), else_=0)), 0).label("success"),
                func.coalesce(func.sum(case((RequestLog.status == "error", 1), else_=0)), 0).label("errors"),
                func.coalesce(func.sum(case((RequestLog.event_kind == "cache_hit", 1), else_=0)), 0).label("cache_hits"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(case(
                    (RequestLog.event_kind == "cache_hit", RequestLog.input_tokens), else_=0)), 0).label("cached_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.coalesce(func.avg(RequestLog.latency_ms), 0.0).label("avg_latency_ms"),
                pct(0.5).label("p50"),
                pct(0.9).label("p90"),
                pct(0.95).label("p95"),
                pct(0.99).label("p99"),
            ).where(*conds).group_by(bucket).order_by(bucket)
        )).all()
    return {
        "granularity": gran,
        "filters": {"workspace": workspace, "component": component, "user": user,
                    "model": model, "provider": provider, "status": status},
        "from": start.isoformat(), "to": end.isoformat(),
        "points": [
            {"bucket": r.bucket.isoformat() if r.bucket else None,
             "requests": r.requests, "success": r.success, "errors": r.errors,
             "cache_hits": r.cache_hits,
             "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
             "cached_tokens": r.cached_tokens,
             "cost_usd": round(r.cost_usd, 6),
             "avg_latency_ms": round(r.avg_latency_ms, 2),
             "p50_latency_ms": round(float(r.p50 or 0), 2),
             "p90_latency_ms": round(float(r.p90 or 0), 2),
             "p95_latency_ms": round(float(r.p95 or 0), 2),
             "p99_latency_ms": round(float(r.p99 or 0), 2)}
            for r in rows]}


@router.get("/admin/usage/breakdown")
async def usage_breakdown(
    dim: str = "model", granularity: str = "day", limit: int = 8,
    client: str | None = None,
    workspace: str | None = None, component: str | None = None,
    user: str | None = None, model: str | None = None,
    provider: str | None = None, status: str | None = None,
    use_case: str | None = None,
    from_: str | None = Query(None, alias="from"), to: str | None = None,
    include_synthetic: bool = False,
):
    """Time-series broken down by a dimension (top-N + 'other'). One key per
    series so Recharts can pivot directly. Default metric: cost_usd."""
    dim_map = {"client": RequestLog.client_id,
               "model": RequestLog.model_alias, "provider": RequestLog.provider,
               "workspace": RequestLog.workspace_id, "component": RequestLog.component,
               "user": RequestLog.user_id, "use_case": RequestLog.use_case}
    if dim not in dim_map:
        raise HTTPException(400, detail={"error": {
            "type": "invalid_request_error", "code": "unknown_dim",
            "message": f"dim must be one of {', '.join(dim_map)}",
            "param": "dim",
        }})
    dim_col = dim_map[dim]
    gran = "hour" if granularity == "hour" else "day"
    default_back = timedelta(hours=48) if gran == "hour" else timedelta(days=45)
    start = _parse_dt(from_) or (datetime.utcnow() - default_back)
    end = _parse_dt(to) or datetime.utcnow()
    conds = _filter_conds(workspace, component, user, model, provider, status,
                          start, end, client=client,
                          include_synthetic=include_synthetic, use_case=use_case)
    bucket = func.date_trunc(gran, RequestLog.timestamp).label("bucket")
    async with async_session() as s:
        top = (await s.execute(
            select(dim_col, func.coalesce(func.sum(RequestLog.cost_usd), 0).label("c"))
            .where(*conds).group_by(dim_col)
            .order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0).desc()).limit(limit)
        )).all()
        top_set = {r[0] for r in top if r[0] is not None}
        rows = (await s.execute(
            select(bucket, dim_col,
                   func.coalesce(func.sum(RequestLog.cost_usd), 0).label("cost_usd"))
            .where(*conds).group_by(bucket, dim_col).order_by(bucket)
        )).all()
    out: dict[str, dict] = {}
    other_used = False
    for r in rows:
        b = r.bucket.isoformat() if r.bucket else "?"
        d = out.setdefault(b, {"bucket": b})
        v = r[1] or "(none)"
        key = str(v) if v in top_set else "_other"
        if key == "_other":
            other_used = True
        d[key] = round(d.get(key, 0.0) + float(r.cost_usd), 6)
    series = [str(v) for v in top_set] + (["_other"] if other_used else [])
    return {"dim": dim, "granularity": gran, "metric": "cost_usd",
            "series": series, "points": list(out.values())}


@router.get("/admin/models")
async def models_metadata():
    """Capability metadata: every (provider, model) configured across workspaces,
    with context window, synced pricing, and which workspaces/aliases use it."""
    from gateway.core.pricing import price_for, price_source
    from gateway.db.models import ModelCatalog
    async with async_session() as s:
        wss = (await s.scalars(select(Workspace))).all()
        cat_rows = (await s.scalars(select(ModelCatalog))).all()
    catalog: dict[tuple[str, str], dict] = {}
    for w in wss:
        label = w.display_name or w.name or w.workspace_id
        for kind, models in (("chat", w.chat_models or {}), ("embedding", w.embedding_models or {})):
            for alias, targets in models.items():
                tlist = targets if isinstance(targets, list) else [targets]
                for i, t in enumerate(tlist):
                    key = (t["provider"], t["model_id"])
                    inp, out = price_for(t["model_id"])
                    entry = catalog.setdefault(key, {
                        "provider": t["provider"], "model_id": t["model_id"], "kind": kind,
                        "context_window": t.get("context_window"),
                        "input_per_1k": inp, "output_per_1k": out,
                        "price_source": price_source(t["model_id"]), "used_by": []})
                    entry["used_by"].append({"workspace": w.workspace_id, "display_name": label,
                                             "alias": alias, "role": "primary" if i == 0 else "fallback"})
    # Union in the full seeded catalog so the Custom Pricing page is a COMPLETE
    # synced-price reference (not just the handful of currently-configured models).
    # Prices always come live from the synced table so this reflects the source.
    for r in cat_rows:
        key = (r.provider, r.model_id)
        if key in catalog:
            continue
        inp, out = price_for(r.model_id)
        catalog[key] = {
            "provider": r.provider, "model_id": r.model_id,
            "kind": "embedding" if not r.supports_streaming and not r.supports_tools else "chat",
            "context_window": r.context_window,
            "input_per_1k": inp, "output_per_1k": out,
            "price_source": price_source(r.model_id), "used_by": []}
    return {"models": sorted(catalog.values(), key=lambda m: (m["provider"], m["model_id"]))}


@router.get("/admin/guardrails")
async def guardrail_log():
    async with async_session() as s:
        rows = (await s.scalars(
            select(GuardrailViolation).order_by(GuardrailViolation.timestamp.desc()).limit(100)
        )).all()
    return {"violations": [
        {"timestamp": v.timestamp.isoformat(), "workspace_id": v.workspace_id,
         "rule": v.rule, "detector": v.detector, "action": v.action,
         "stage": v.stage, "excerpt": v.excerpt} for v in rows
    ]}


@router.get("/admin/parity")
async def parity():
    """Capability-comparison matrix as JSON (drives the dashboard panel)."""
    from gateway.core.parity import matrix
    return matrix()


@router.post("/admin/parity/run")
async def parity_run(payload: dict = Body(default_factory=dict)):
    """Live shadow parity: run one prompt through BOTH engines (Bifrost + Direct)
    and return a scored side-by-side. Proves an engine swap is safe, on demand.

    Body: {workspace_id, provider, model_id, prompt|messages, max_tokens?, temperature?}
    """
    from gateway.core.parity_run import run_parity

    provider = payload.get("provider")
    model_id = payload.get("model_id") or payload.get("model")
    workspace_id = payload.get("workspace_id") or ""
    if not provider or not model_id:
        raise HTTPException(status_code=422, detail={"error": {
            "message": "provider and model_id are required", "type": "invalid_request_error"}})
    messages = payload.get("messages")
    if not messages:
        prompt = payload.get("prompt") or "In one short sentence, what is an LLM gateway?"
        messages = [{"role": "user", "content": prompt}]
    try:
        max_tokens = int(payload.get("max_tokens", 256))
        temperature = float(payload.get("temperature", 0.0))
        samples = max(1, min(int(payload.get("samples", 3)), 7))
    except (TypeError, ValueError):
        max_tokens, temperature, samples = 256, 0.0, 3
    return await run_parity(workspace_id=workspace_id, provider=provider, model_id=model_id,
                            messages=messages, max_tokens=max_tokens, temperature=temperature,
                            samples=samples)


@router.get("/admin/audit")
async def audit_log(limit: int = 100):
    """Immutable admin-action audit trail (who/what/when)."""
    async with async_session() as s:
        rows = (await s.scalars(select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit))).all()
    return {"audit": [
        {"timestamp": a.timestamp.isoformat(), "principal": a.principal, "action": a.action,
         "target": a.target, "detail": a.detail} for a in rows
    ]}


@router.get("/admin/health")
async def admin_health(force: bool = False):
    """Engine, per-provider reachability, circuit-breaker state, and infra status."""
    from gateway.core import provider_health as ph
    from gateway.runtime import engine
    from gateway.config import settings
    eng = engine()
    snap = await ph.snapshot(force=force)
    return {
        "engine": eng.name,
        "engine_healthy": await eng.healthcheck(),
        "kafka": await _kafka_health(),
        "redis": await _redis_health(),
        **snap,
    }


async def _kafka_health() -> dict:
    """Real Kafka status: 'disabled' if no brokers configured (in-process bus
    still works), 'up'/'down' based on a bounded metadata probe otherwise."""
    from gateway.config import settings
    if not settings.kafka_brokers:
        return {"state": "disabled", "detail": "in-process governance bus", "topic": settings.kafka_topic}
    try:
        import asyncio
        from aiokafka import AIOKafkaProducer
        prod = AIOKafkaProducer(bootstrap_servers=settings.kafka_brokers)
        await asyncio.wait_for(prod.start(), timeout=2.0)
        await prod.stop()
        return {"state": "up", "detail": settings.kafka_brokers, "topic": settings.kafka_topic}
    except Exception as e:  # noqa: BLE001
        return {"state": "down", "detail": str(e)[:80], "topic": settings.kafka_topic}


async def _redis_health() -> dict:
    """Real Redis status: 'disabled' if no REDIS_URL (in-memory limiter used),
    'up'/'down' based on a bounded PING otherwise."""
    from gateway.config import settings
    if not settings.redis_url:
        return {"state": "disabled", "detail": "in-memory rate-limit/cache"}
    try:
        import asyncio
        from gateway.core.redis_rate_limit import _client
        cli = _client()
        await asyncio.wait_for(cli.ping(), timeout=2.0)
        return {"state": "up", "detail": settings.redis_url}
    except Exception as e:  # noqa: BLE001
        return {"state": "down", "detail": str(e)[:80]}


@router.get("/admin/request-logs")
async def request_logs(
    client: str | None = None,
    workspace: str | None = None,
    user: str | None = None,
    component: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    status: str | None = None,
    use_case: str | None = None,
    request_id: str | None = None,
    from_: str | None = Query(None, alias="from"),   # unified with cost/timeseries (client sends `from`)
    to: str | None = None,
    sort_by: str = "timestamp",   # timestamp | latency_ms | cost_usd | input_tokens | output_tokens
    sort_dir: str = "desc",       # asc | desc
    limit: int = 200,
    offset: int = 0,
    include_synthetic: bool = False,
):
    """WAVE 19 TRACK D3 \u2014 searchable RequestLog viewer.

    Powers the in-app Request Logs page. All filters are optional and AND-ed.
    Returns rows sorted by timestamp DESC, plus a `total` count for pagination.
    `from_` / `to` are ISO-8601 timestamps (e.g. ``2026-06-09T00:00:00``).

    Default scope is ``source='live'`` \u2014 the seeded synthetic backfill
    (`source='synthetic'`) is hidden unless ``include_synthetic=true`` is
    passed. This stops the operator from seeing nonsense numbers where the
    legacy 184k synthetic rows dominate the live demo traffic.
    """
    from datetime import datetime as _dt

    def _parse_iso(s: str | None):
        if not s:
            return None
        try:
            return _dt.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    conds = []
    if not include_synthetic:
        conds.append(RequestLog.source == "live")
    if client:    conds.append(RequestLog.client_id == client)
    if workspace: conds.append(RequestLog.workspace_id == workspace)
    if component: conds.append(RequestLog.component == component)
    if provider:  conds.append(RequestLog.provider == provider)
    if status:    conds.append(RequestLog.status == status)
    if use_case:  conds.append(RequestLog.use_case.ilike(f"%{use_case}%"))
    # Free-text fields: substring (ILIKE) so the operator doesn't have to
    # remember the exact JWT sub / model id / request id verbatim.
    if user:
        conds.append(RequestLog.user_id.ilike(f"%{user}%"))
    if model:
        conds.append(
            (RequestLog.model_alias.ilike(f"%{model}%"))
            | (RequestLog.provider_model_id.ilike(f"%{model}%"))
        )
    if request_id:
        conds.append(RequestLog.request_id.ilike(f"%{request_id}%"))
    if from_:
        d = _parse_iso(from_)
        if d: conds.append(RequestLog.timestamp >= d)
    if to:
        d = _parse_iso(to)
        if d: conds.append(RequestLog.timestamp <= d)

    async with async_session() as s:
        # total count
        total = await s.scalar(
            select(func.count(RequestLog.id)).where(*conds) if conds
            else select(func.count(RequestLog.id))
        ) or 0
        # Sortable columns (whitelist to prevent injection)
        _SORTABLE = {
            "timestamp": RequestLog.timestamp,
            "latency_ms": RequestLog.latency_ms,
            "cost_usd": RequestLog.cost_usd,
            "input_tokens": RequestLog.input_tokens,
            "output_tokens": RequestLog.output_tokens,
        }
        sort_col = _SORTABLE.get(sort_by, RequestLog.timestamp)
        ordering = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
        # page
        q = (select(RequestLog).order_by(ordering)
                                .limit(min(max(1, limit), 1000))
                                .offset(max(0, offset)))
        if conds:
            q = q.where(*conds)
        rows = (await s.scalars(q)).all()

        # WAVE 26: join guardrail_violations for this page so the admin row
        # detail can show "what rule fired + masked excerpt" without N+1.
        from gateway.db.models import GuardrailViolation
        req_ids = [r.request_id for r in rows]
        gv_by_req: dict[str, list[dict]] = {}
        if req_ids:
            gv_rows = (await s.scalars(
                select(GuardrailViolation).where(GuardrailViolation.request_id.in_(req_ids))
            )).all()
            for gv in gv_rows:
                gv_by_req.setdefault(gv.request_id, []).append({
                    "rule": gv.rule,
                    "detector": gv.detector,
                    "action": gv.action,
                    "stage": gv.stage,
                    "excerpt": gv.excerpt,
                    "severity": gv.severity,
                    "timestamp": gv.timestamp.isoformat() if gv.timestamp else None,
                })

    return {
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "rows": [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "request_id": r.request_id,
                "client_id": r.client_id,
                "workspace_id": r.workspace_id,
                "user_id": r.user_id,
                "component": r.component,
                "provider": r.provider,
                "model_alias": r.model_alias,
                "provider_model_id": r.provider_model_id,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": r.cost_usd,
                "latency_ms": r.latency_ms,
                "stream": r.stream,
                "status": r.status,
                "error_type": r.error_type,
                "error_detail": r.error_detail,           # WAVE 26 - structured why-context
                "guardrail_violations": gv_by_req.get(r.request_id, []),
                "call_kind": r.call_kind,
                "event_kind": r.event_kind,
                "engine": r.engine,
                "use_case": r.use_case,
            }
            for r in rows
        ],
    }


@router.get("/admin/request-logs/facets")
async def request_logs_facets(
    client: str | None = None, workspace: str | None = None,
    component: str | None = None, provider: str | None = None,
    model: str | None = None, user: str | None = None,
    status: str | None = None, use_case: str | None = None,
    include_synthetic: bool = False,
):
    """Hierarchical, cascading filter options for the Request Logs / Analytics /
    Routing filter bars, sourced from the actual database.

    Every facet is scoped by ALL the OTHER currently-selected filters (classic
    faceted search): picking a client narrows workspaces to that client; picking
    a workspace narrows components / use-cases / providers / models / users to
    only those seen in that workspace; and so on. A facet is never scoped by
    ITSELF, so you can always re-pick within it.

    When nothing is scoped (All clients, no workspace), the enum catalogues
    (PROVIDERS, KNOWN_COMPONENTS) are unioned in so the dropdowns show the full
    space of possibilities; once any scope is applied, the facets become purely
    data-driven (only values that actually exist under the selection).
    """
    from gateway.core.admin_validation import PROVIDERS
    from gateway.db.models import Client, Workspace
    from gateway.db.seed import KNOWN_COMPONENTS

    scoped = bool(client or workspace or component or provider or model or user or use_case)

    def _conds(exclude: str | None):
        """Row conditions from every filter EXCEPT `exclude` (so a facet is
        scoped by its siblings, not itself)."""
        c = []
        if not include_synthetic:
            c.append(RequestLog.source == "live")
        if client and exclude != "client":
            c.append(RequestLog.client_id == client)
        if workspace and exclude != "workspace":
            c.append(RequestLog.workspace_id == workspace)
        if component and exclude != "component":
            c.append(RequestLog.component == component)
        if provider and exclude != "provider":
            c.append(RequestLog.provider == provider)
        if status and exclude != "status":
            c.append(RequestLog.status == status)
        if use_case and exclude != "use_case":
            c.append(RequestLog.use_case == use_case)
        if user and exclude != "user":
            c.append(RequestLog.user_id.ilike(f"%{user}%"))
        if model and exclude != "model":
            c.append((RequestLog.model_alias.ilike(f"%{model}%"))
                     | (RequestLog.provider_model_id.ilike(f"%{model}%")))
        return c

    async def _distinct(col, exclude: str | None, *, limit: int = 200):
        async with async_session() as s2:
            q = select(col).distinct().where(col.isnot(None))
            conds = _conds(exclude)
            if conds:
                q = q.where(*conds)
            return [v for v in (await s2.scalars(q.limit(limit))).all() if v is not None]

    async with async_session() as s:
        clients = (await s.scalars(select(Client).order_by(Client.client_id))).all()
        # Workspaces cascade off the client: All clients → all workspaces (of all
        # clients); a specific client → only its workspaces.
        ws_q = select(Workspace).order_by(Workspace.workspace_id)
        if client:
            ws_q = ws_q.where(Workspace.client_id == client)
        workspaces = (await s.scalars(ws_q)).all()

    (seen_users, seen_models, seen_providers, seen_statuses,
     seen_kinds, seen_use_cases, seen_components) = await asyncio.gather(
        _distinct(RequestLog.user_id, "user", limit=500),
        _distinct(RequestLog.model_alias, "model"),
        _distinct(RequestLog.provider, "provider"),
        _distinct(RequestLog.status, "status", limit=20),
        _distinct(RequestLog.event_kind, None, limit=20),
        _distinct(RequestLog.use_case, "use_case"),
        _distinct(RequestLog.component, "component"),
    )

    # Scoped → data-driven only; unscoped → union the enum catalogues so a fresh
    # page shows the full space of providers/components.
    components = sorted(seen_components) if scoped else sorted(set(KNOWN_COMPONENTS) | set(seen_components))
    providers = sorted(seen_providers) if scoped else sorted(set(PROVIDERS) | set(seen_providers))

    return {
        "clients": [{"client_id": c.client_id, "name": c.name or c.client_id} for c in clients],
        "workspaces": [
            {"workspace_id": w.workspace_id, "client_id": w.client_id,
             "display_name": w.display_name or w.name or w.workspace_id}
            for w in workspaces
        ],
        "components": components,
        "providers": providers,
        "statuses": sorted(set(seen_statuses) | {"success", "error"}),
        "event_kinds": sorted(set(seen_kinds) | {
            "completion", "error", "cache_hit", "fallback",
            "rate_limited", "guardrail_block", "request_start",
        }),
        "users": sorted(seen_users)[:500],
        "models": sorted(seen_models),
        "use_cases": sorted(seen_use_cases),
    }
