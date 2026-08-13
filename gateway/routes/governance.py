"""POST /governance/events - external producer ingest.

External services (other components on the platform, sidecars, batch jobs) can
POST a governance event in the same envelope shape the gateway publishes to
Kafka. Ingested events flow through the SAME `bus().emit()` path as
auto-emitted events, so they reach Postgres + Prometheus + SSE + Kafka with
identical semantics.

Auth: workspace API key OR platform admin token.
- Workspace key:  payload.workspace_id MUST equal the auth context's workspace.
- Admin token:    payload.workspace_id is unrestricted (cross-tenant tooling).

Schema: {event_kind, correlation_id?, idempotency_key?, payload: {...}}
- event_kind ∈ {completion, request_start, error, guardrail_block, fallback,
                rate_limited, cache_hit, budget_alert}
- payload shape per kind documented inline below.

Response: 202 Accepted with the correlation_id (echoed or server-generated).
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from gateway.core.auth import resolve_principal
from gateway.core.security import is_admin_request
from gateway.governance.observer import (
    BudgetAlertEvent,
    CacheHitEvent,
    FallbackEvent,
    GovernanceEvent,
    GuardrailDecisionEvent,
    RateLimitedEvent,
    RequestErrorEvent,
    RequestStartEvent,
    RequestSuccessEvent,
)
from gateway.runtime import bus

router = APIRouter()

# Allowed event_kind values, mirroring kafka_observer._envelope mappings.
ALLOWED_KINDS: tuple[str, ...] = (
    "completion", "request_start", "error", "guardrail_block",
    "fallback", "rate_limited", "cache_hit", "budget_alert",
)


class IngestIn(BaseModel):
    event_kind: str
    correlation_id: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any]


def _required(payload: dict, fields: tuple[str, ...], kind: str) -> list[dict]:
    """Return FastAPI-shaped error dicts for missing required fields."""
    errors: list[dict] = []
    for f in fields:
        v = payload.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            errors.append({
                "loc": ["body", "payload", f],
                "msg": f"required for event_kind '{kind}'",
                "type": "value_error",
            })
    return errors


def _build_event(kind: str, correlation_id: str, payload: dict) -> GovernanceEvent:
    """Build a typed dataclass from the ingest payload. Raises HTTPException 422
    on missing/invalid required fields. Mirrors the producer-side envelope."""
    p = payload
    cid = correlation_id

    if kind == "completion":
        errs = _required(p, ("workspace_id", "model", "provider"), kind)
        if errs:
            raise HTTPException(422, detail=errs)
        return RequestSuccessEvent(
            request_id=cid,
            workspace_id=p["workspace_id"],
            user_id=p.get("user_id"),
            use_case=p.get("use_case"),
            engine=p.get("engine", "external"),
            provider=p["provider"],
            model_alias=p["model"],
            provider_model_id=p.get("provider_model_id", p["model"]),
            input_tokens=int(p.get("input_tokens") or 0),
            output_tokens=int(p.get("output_tokens") or 0),
            cost_usd=float(p.get("cost_usd") or 0.0),
            latency_ms=float(p.get("latency_ms") or 0.0),
            call_kind=(p.get("metadata") or {}).get("call_kind", "chat"),
            stream=bool((p.get("metadata") or {}).get("stream", False)),
            attempt=int((p.get("metadata") or {}).get("attempt", 1)),
            component=p.get("component"),
        )

    if kind == "request_start":
        errs = _required(p, ("workspace_id", "model", "provider"), kind)
        if errs:
            raise HTTPException(422, detail=errs)
        return RequestStartEvent(
            request_id=cid,
            workspace_id=p["workspace_id"],
            user_id=p.get("user_id"),
            use_case=p.get("use_case"),
            model_alias=p["model"],
            provider=p["provider"],
            provider_model_id=p.get("provider_model_id", p["model"]),
            engine=p.get("engine", "external"),
            stream=bool((p.get("metadata") or {}).get("stream", False)),
            has_tools=bool((p.get("metadata") or {}).get("has_tools", False)),
            call_kind=(p.get("metadata") or {}).get("call_kind", "chat"),
        )

    if kind == "error":
        errs = _required(p, ("workspace_id", "provider", "model"), kind)
        if errs:
            raise HTTPException(422, detail=errs)
        return RequestErrorEvent(
            request_id=cid,
            workspace_id=p["workspace_id"],
            provider=p["provider"],
            model_alias=p["model"],
            error_type=p.get("error_type") or "unknown",
        )

    if kind == "guardrail_block":
        errs = _required(p, ("workspace_id", "model", "rule"), kind)
        if errs:
            raise HTTPException(422, detail=errs)
        return GuardrailDecisionEvent(
            request_id=cid,
            workspace_id=p["workspace_id"],
            model_alias=p["model"],
            rule=p["rule"],
            detector=p.get("detector", "external"),
            action=p.get("action", "block"),
            stage=p.get("stage", "input"),
        )

    if kind == "fallback":
        errs = _required(p, ("workspace_id", "model", "from_provider", "to_provider"), kind)
        if errs:
            raise HTTPException(422, detail=errs)
        return FallbackEvent(
            request_id=cid,
            workspace_id=p["workspace_id"],
            model_alias=p["model"],
            from_provider=p["from_provider"],
            to_provider=p["to_provider"],
            reason=p.get("reason", "external"),
        )

    if kind == "rate_limited":
        errs = _required(p, ("workspace_id", "model", "limit_type"), kind)
        if errs:
            raise HTTPException(422, detail=errs)
        if p["limit_type"] not in ("rpm", "tpm", "budget"):
            raise HTTPException(422, detail=[{
                "loc": ["body", "payload", "limit_type"],
                "msg": "limit_type must be one of rpm, tpm, budget",
                "type": "value_error",
            }])
        return RateLimitedEvent(
            request_id=cid,
            workspace_id=p["workspace_id"],
            model_alias=p["model"],
            limit_type=p["limit_type"],
        )

    if kind == "cache_hit":
        errs = _required(p, ("workspace_id", "model", "provider"), kind)
        if errs:
            raise HTTPException(422, detail=errs)
        return CacheHitEvent(
            request_id=cid,
            workspace_id=p["workspace_id"],
            user_id=p.get("user_id"),
            use_case=p.get("use_case"),
            model_alias=p["model"],
            provider=p["provider"],
            input_tokens=int(p.get("input_tokens") or 0),
            output_tokens=int(p.get("output_tokens") or 0),
            cost_saved_usd=float(p.get("cost_saved_usd") or 0.0),
            component=p.get("component"),
        )

    if kind == "budget_alert":
        errs = _required(p, ("workspace_id", "scope"), kind)
        if errs:
            raise HTTPException(422, detail=errs)
        return BudgetAlertEvent(
            workspace_id=p["workspace_id"],
            scope=p["scope"],
            threshold=int(p.get("threshold") or 0),
            spend_usd=float(p.get("spend_usd") or 0.0),
            cap_usd=float(p.get("cap_usd") or 0.0),
        )

    raise HTTPException(422, detail=[{
        "loc": ["body", "event_kind"],
        "msg": f"event_kind must be one of {', '.join(ALLOWED_KINDS)}",
        "type": "value_error",
    }])


@router.post("/governance/events", status_code=202)
async def ingest_governance_event(
    request: Request,
    body: IngestIn,
    authorization: str | None = Header(None),
):
    """Ingest a governance event from an external producer. Echoes correlation_id."""
    if body.event_kind not in ALLOWED_KINDS:
        raise HTTPException(422, detail=[{
            "loc": ["body", "event_kind"],
            "msg": f"event_kind must be one of {', '.join(ALLOWED_KINDS)}",
            "type": "value_error",
        }])

    # Auth: admin token allows any workspace; bearer key restricts to its workspace.
    is_admin = await is_admin_request(request)
    auth_workspace: str | None = None
    if not is_admin:
        # Resolve the workspace from the bearer key
        try:
            ctx = await resolve_principal(authorization, dict(request.headers))
        except Exception:
            raise HTTPException(401, "Invalid or missing credentials")
        auth_workspace = ctx.workspace_id

    payload_ws = body.payload.get("workspace_id")
    if not isinstance(payload_ws, str) or not payload_ws.strip():
        raise HTTPException(422, detail=[{
            "loc": ["body", "payload", "workspace_id"],
            "msg": "workspace_id is required in payload",
            "type": "value_error",
        }])

    if auth_workspace and payload_ws != auth_workspace:
        raise HTTPException(403, detail=(
            f"workspace key for '{auth_workspace}' cannot emit an event tagged "
            f"with workspace_id='{payload_ws}'"
        ))

    correlation_id = body.correlation_id or f"ext-{uuid.uuid4().hex[:12]}"
    event = _build_event(body.event_kind, correlation_id, body.payload)
    bus().emit(event)

    return {
        "ok": True,
        "correlation_id": correlation_id,
        "idempotency_key": body.idempotency_key or str(uuid.uuid4()),
        "event_kind": body.event_kind,
    }
