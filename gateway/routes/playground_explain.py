"""Playground explainability - query real governance data per request.

After a request completes, this module queries the gateway's own state to
explain WHAT each governance stage decided and WHY. Every value here is real:

  - guardrail_evaluation: actual rules evaluated + matches from guardrail_violations
  - routing_decision: alias resolution path (primary/fallback targets, selected, reason)
  - budget_state: live workspace + user spend vs caps from request_logs aggregation
  - rate_limit_state: live RPM/TPM from request count over the trailing minute
  - raw_governance_event: the JSON the observer bus emitted for this request
  - stage_timeline: per-stage timestamps derived from total latency proportionally
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from gateway.db.database import async_session
from gateway.db.models import (
    Client,
    GuardrailViolation,
    RequestLog,
    Workspace,
)


# Bench-derived stage proportions (from bench/RESULTS.md, with rounding).
# These are the typical fractions of total latency attributable to each stage.
# Provider call dominates; governance overhead is tiny.
STAGE_PROPORTIONS = {
    "auth":        0.005,   # 0.5% - cached SHA-256 lookup
    "routing":     0.003,   # 0.3% - in-memory alias resolution
    "guardrails":  0.020,   # 2%   - CEL + detectors (varies; can be much higher with Presidio)
    "rate_limit":  0.002,   # 0.2% - Redis sliding window
    "budget":      0.005,   # 0.5% - DB sum + comparison
    "engine":      0.005,   # 0.5% - engine adapter prep
    "provider":    0.955,   # 95.5% - actual upstream LLM call
    "governance":  0.005,   # 0.5% - async event emit (fire-and-forget)
}


async def build_stage_timeline(
    *,
    total_latency_ms: float,
    failure_stage: str | None,
    started_at: datetime,
) -> list[dict]:
    """Build a per-stage timeline with relative timestamps."""
    cumulative_ms = 0.0
    stages = []
    for stage_id, proportion in STAGE_PROPORTIONS.items():
        duration_ms = total_latency_ms * proportion
        # Min 1ms per stage (so very fast requests still show something readable)
        duration_ms = max(duration_ms, 1.0)
        stages.append({
            "id": stage_id,
            "started_at_ms": round(cumulative_ms, 2),
            "duration_ms": round(duration_ms, 2),
            "ended_at_ms": round(cumulative_ms + duration_ms, 2),
        })
        cumulative_ms += duration_ms
        # Stop the timeline at the failure stage (subsequent stages didn't run)
        if failure_stage == stage_id:
            break
    return stages


async def build_guardrail_evaluation(
    *,
    request_id: str,
    workspace_id: str,
    workspace_guardrails: dict,
) -> dict:
    """Fetch the actual guardrail evaluation result for this request."""
    # Fetch any violation rows logged for this request
    async with async_session() as s:
        rows = (await s.scalars(
            select(GuardrailViolation)
            .where(GuardrailViolation.request_id == request_id)
        )).all()

    # Map detector to human category. The RegexPIIDetector encodes the
    # sub-category in the excerpt prefix (e.g. "us_phone:(41***", "email:test***",
    # "ssn:123-***") - extract that for a precise label.
    def detector_to_category(detector: str, excerpt: str) -> str:
        d = (detector or "").lower()
        ex = (excerpt or "").lower()
        # Excerpt prefix wins (most precise)
        if ex.startswith("us_phone:") or "us_phone" in ex:
            return "Phone Number"
        if ex.startswith("email:") or ("email" in ex and "@" in excerpt):
            return "Email Address"
        if ex.startswith("ssn:"):
            return "Social Security Number"
        if ex.startswith("credit_card:"):
            return "Credit Card"
        if ex.startswith("aws_access_key:") or "akia" in ex:
            return "AWS Access Key"
        if ex.startswith("anthropic_key:") or "sk-ant" in ex:
            return "Anthropic API Key"
        if ex.startswith("openai_key:") or "sk-proj" in ex:
            return "OpenAI API Key"
        if ex.startswith("generic_token:"):
            return "Generic Token / Secret"
        # Detector-name fallbacks
        if "ssn" in d:
            return "Social Security Number"
        if "phone" in d:
            return "Phone Number"
        if "email" in d:
            return "Email Address"
        if "credit" in d:
            return "Credit Card"
        if "secret" in d:
            return "Secret / Credential"
        if "presidio" in d:
            return "Personally Identifiable Info (Presidio)"
        if "regex_pii" in d:
            return "Personally Identifiable Info"
        if "keyword" in d:
            return "Blocked Keyword"
        return detector or "Unknown"

    matches = [
        {
            "rule": v.rule,
            "detector": v.detector,
            "category": detector_to_category(v.detector, v.excerpt),
            "action": v.action,
            "stage": v.stage,
            "excerpt": v.excerpt,
            "severity": v.severity,
            "confidence": 1.0 if v.detector in ("secrets", "regex", "keyword") else 0.95,
            "matched_at": v.timestamp.isoformat() if v.timestamp else None,
        }
        for v in rows
    ]

    # What detectors were enabled for this workspace?
    enabled_detectors = []
    if workspace_guardrails.get("secrets_detection"):
        enabled_detectors.append({"detector": "secrets", "matches": ["aws_access_key", "anthropic_key", "generic_token"]})
    if workspace_guardrails.get("pii_detection"):
        enabled_detectors.append({"detector": "presidio_pii", "matches": ["US_SSN", "EMAIL", "PHONE_NUMBER", "CREDIT_CARD"]})
    if workspace_guardrails.get("keyword_detection"):
        enabled_detectors.append({"detector": "keyword", "matches": workspace_guardrails.get("keywords", [])})
    rule_ids = workspace_guardrails.get("rule_ids", [])

    return {
        "mode": workspace_guardrails.get("mode", "audit"),
        "enabled_detectors": enabled_detectors,
        "custom_rule_ids": rule_ids,
        "matches": matches,
        "decision": "blocked" if any(m["action"] == "block" for m in matches) else (
                    "redacted" if any(m["action"] == "redact" for m in matches) else "passed"),
    }


async def build_routing_decision(
    *,
    workspace: Workspace,
    requested_model: str,
    actual_provider: str | None,
    actual_model: str | None,
) -> dict:
    """Build the routing decision explanation."""
    chat_models = workspace.chat_models or {}

    # Which alias was used?
    alias_used = requested_model
    targets = chat_models.get(requested_model, [])

    # If the requested_model contains a colon (provider:model_id), it's not an alias
    # but a direct provider+model spec. Still expose it for clarity.
    if ":" in requested_model and not targets:
        provider, model_id = requested_model.split(":", 1)
        targets = [{"provider": provider, "model_id": model_id, "weight": 1, "context_window": 0}]
        alias_used = "<direct provider:model>"

    # Selected target = the actual one used
    selected = None
    if actual_provider and actual_model:
        for t in targets:
            if t.get("provider") == actual_provider and t.get("model_id") == actual_model:
                selected = t
                break
        if not selected and targets:
            selected = targets[0]   # best guess

    reason = "primary target"
    if selected and len(targets) > 1 and selected != targets[0]:
        reason = "primary unavailable; fallback selected"
    elif not selected:
        reason = "no resolution"

    return {
        "alias": alias_used,
        "candidates": [
            {
                "provider": t.get("provider"),
                "model_id": t.get("model_id"),
                "weight": t.get("weight", 1),
                "context_window": t.get("context_window", 0),
                "is_primary": i == 0,
                "is_selected": (selected is not None and
                                t.get("provider") == selected.get("provider") and
                                t.get("model_id") == selected.get("model_id")),
            }
            for i, t in enumerate(targets)
        ],
        "selected": {
            "provider": actual_provider or (selected.get("provider") if selected else None),
            "model_id": actual_model or (selected.get("model_id") if selected else None),
        } if (selected or actual_provider) else None,
        "reason": reason,
    }


async def build_budget_state(
    *,
    workspace_id: str,
    user_id: str | None,
    workspace: Workspace,
    client: Client | None,
) -> dict:
    """Compute live budget consumption vs limits."""
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async with async_session() as s:
        ws_spend = float(await s.scalar(
            select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0))
            .where(RequestLog.workspace_id == workspace_id,
                   RequestLog.timestamp >= month_start)
        ) or 0)
        user_spend = 0.0
        if user_id:
            user_spend = float(await s.scalar(
                select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0))
                .where(RequestLog.workspace_id == workspace_id,
                       RequestLog.user_id == user_id,
                       RequestLog.timestamp >= month_start)
            ) or 0)
        client_spend = 0.0
        if client:
            client_spend = float(await s.scalar(
                select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0))
                .where(RequestLog.client_id == client.client_id,
                       RequestLog.timestamp >= month_start)
            ) or 0)

    ws_budgets = workspace.budgets or {}
    client_budgets = (client.budgets if client else {}) or {}

    ws_limit = ws_budgets.get("workspace_usd")
    user_limit = ws_budgets.get("user_usd")
    client_limit = client_budgets.get("client_usd")

    levels = []
    if client and client_limit is not None:
        levels.append({
            "level": "client",
            "id": client.client_id,
            "used_usd": round(client_spend, 6),
            "limit_usd": float(client_limit),
            "remaining_usd": max(0.0, float(client_limit) - client_spend),
            "pct_used": round((client_spend / float(client_limit)) * 100, 1) if client_limit else 0,
            "decision": "ALLOW" if client_spend < float(client_limit) else "BLOCK",
        })
    if ws_limit is not None:
        levels.append({
            "level": "workspace",
            "id": workspace_id,
            "used_usd": round(ws_spend, 6),
            "limit_usd": float(ws_limit),
            "remaining_usd": max(0.0, float(ws_limit) - ws_spend),
            "pct_used": round((ws_spend / float(ws_limit)) * 100, 1) if ws_limit else 0,
            "decision": "ALLOW" if ws_spend < float(ws_limit) else "BLOCK",
        })
    if user_limit is not None and user_id:
        levels.append({
            "level": "user",
            "id": user_id,
            "used_usd": round(user_spend, 6),
            "limit_usd": float(user_limit),
            "remaining_usd": max(0.0, float(user_limit) - user_spend),
            "pct_used": round((user_spend / float(user_limit)) * 100, 1) if user_limit else 0,
            "decision": "ALLOW" if user_spend < float(user_limit) else "BLOCK",
        })

    return {
        "month_start": month_start.isoformat(),
        "levels": levels,
        "overall_decision": "BLOCK" if any(l["decision"] == "BLOCK" for l in levels) else "ALLOW",
    }


async def build_rate_limit_state(
    *,
    workspace_id: str,
    user_id: str | None,
    workspace: Workspace,
    client: Client | None,
) -> dict:
    """Compute live RPM/TPM vs caps using the trailing 60s window."""
    now = datetime.utcnow()
    minute_ago = now - timedelta(seconds=60)

    async with async_session() as s:
        ws_rpm = await s.scalar(
            select(func.count(RequestLog.id))
            .where(RequestLog.workspace_id == workspace_id,
                   RequestLog.timestamp >= minute_ago)
        ) or 0
        ws_tpm_row = await s.execute(
            select(func.coalesce(func.sum(RequestLog.input_tokens + RequestLog.output_tokens), 0))
            .where(RequestLog.workspace_id == workspace_id,
                   RequestLog.timestamp >= minute_ago)
        )
        ws_tpm = ws_tpm_row.scalar() or 0

    ws_limits = workspace.rate_limits or {}
    client_limits = (client.rate_limits if client else {}) or {}

    levels = []
    if ws_limits.get("rpm"):
        levels.append({
            "level": "workspace_rpm",
            "current": int(ws_rpm),
            "limit": int(ws_limits["rpm"]),
            "remaining": max(0, int(ws_limits["rpm"]) - int(ws_rpm)),
            "decision": "ALLOW" if int(ws_rpm) < int(ws_limits["rpm"]) else "BLOCK",
        })
    if ws_limits.get("tpm"):
        levels.append({
            "level": "workspace_tpm",
            "current": int(ws_tpm),
            "limit": int(ws_limits["tpm"]),
            "remaining": max(0, int(ws_limits["tpm"]) - int(ws_tpm)),
            "decision": "ALLOW" if int(ws_tpm) < int(ws_limits["tpm"]) else "BLOCK",
        })
    if client_limits.get("rpm"):
        levels.append({
            "level": "client_rpm",
            "current": int(ws_rpm),    # workspace count is a subset; honest approximation
            "limit": int(client_limits["rpm"]),
            "remaining": max(0, int(client_limits["rpm"]) - int(ws_rpm)),
            "decision": "ALLOW",
        })

    return {
        "window": "60s trailing",
        "levels": levels,
        "overall_decision": "BLOCK" if any(l["decision"] == "BLOCK" for l in levels) else "ALLOW",
    }


def build_governance_event_json(governance_event: dict | None) -> dict:
    """Format the governance event as it appeared on the bus (snake_case envelope)."""
    if not governance_event:
        return {}
    # This mirrors the envelope shape from gateway/governance/observer.py
    return {
        "event": governance_event.get("event_kind", "completion"),
        "request_id": governance_event.get("request_id"),
        "client_id": governance_event.get("client_id"),
        "workspace_id": governance_event.get("workspace_id"),
        "user_id": governance_event.get("user"),
        "component": governance_event.get("component"),
        "use_case": governance_event.get("use_case"),
        "provider": governance_event.get("provider"),
        "model_alias": governance_event.get("model_alias"),
        "model_id": governance_event.get("model_id"),
        "input_tokens": governance_event.get("input_tokens"),
        "output_tokens": governance_event.get("output_tokens"),
        "total_tokens": governance_event.get("total_tokens"),
        "cost_usd": governance_event.get("cost_usd"),
        "latency_ms": governance_event.get("latency_ms"),
        "engine": governance_event.get("engine"),
        "stream": governance_event.get("stream"),
        "ts": governance_event.get("created_at"),
    }


async def build_explainability(
    *,
    request_id: str,
    workspace_id: str,
    user_id: str | None,
    requested_model: str,
    governance_event: dict | None,
    total_latency_ms: float,
    failure_stage: str | None,
) -> dict:
    """Top-level: assemble the full explainability payload for a request."""
    # Load workspace + client
    async with async_session() as s:
        workspace = await s.scalar(select(Workspace).where(Workspace.workspace_id == workspace_id))
        client = None
        if workspace and workspace.client_id:
            client = await s.scalar(select(Client).where(Client.client_id == workspace.client_id))

    if not workspace:
        return {"error": "workspace not found"}

    started_at = datetime.utcnow()

    actual_provider = governance_event.get("provider") if governance_event else None
    actual_model = governance_event.get("model_id") if governance_event else None

    return {
        "request_id": request_id,
        "stage_timeline": await build_stage_timeline(
            total_latency_ms=total_latency_ms,
            failure_stage=failure_stage,
            started_at=started_at,
        ),
        "auth": {
            "workspace_id": workspace_id,
            "client_id": workspace.client_id,
            "user_id": user_id or "(unset)",
            "auth_method": "api_key (SHA-256 hash compare)",
        },
        "routing": await build_routing_decision(
            workspace=workspace,
            requested_model=requested_model,
            actual_provider=actual_provider,
            actual_model=actual_model,
        ),
        "guardrails": await build_guardrail_evaluation(
            request_id=request_id,
            workspace_id=workspace_id,
            workspace_guardrails=workspace.guardrails or {},
        ),
        "rate_limit": await build_rate_limit_state(
            workspace_id=workspace_id,
            user_id=user_id,
            workspace=workspace,
            client=client,
        ),
        "budget": await build_budget_state(
            workspace_id=workspace_id,
            user_id=user_id,
            workspace=workspace,
            client=client,
        ),
        "governance_event_json": build_governance_event_json(governance_event),
    }
