"""Postgres observer - persists durable RequestLog + GuardrailViolation rows.

Writes are bounded by a timeout so a paused/dead Postgres cannot exhaust the
connection pool and starve the request hot path (fail-open governance - a lost
row during an outage is acceptable; the bus already counts drops).
"""
from __future__ import annotations

import asyncio

from gateway.db.database import async_session
from gateway.db.models import GuardrailViolation, RequestLog
from gateway.governance.observer import (
    CacheHitEvent,
    GovernanceEvent,
    GovernanceObserver,
    GuardrailDecisionEvent,
    RateLimitedEvent,
    RequestErrorEvent,
    RequestSuccessEvent,
)

_WRITE_TIMEOUT = 3.0


async def _write(row) -> None:
    async def _do():
        async with async_session() as s:
            s.add(row)
            await s.commit()
    try:
        await asyncio.wait_for(_do(), timeout=_WRITE_TIMEOUT)
    except Exception:  # noqa: BLE001 - DB slow/down: drop the row, never block governance
        pass


class PostgresObserver(GovernanceObserver):
    async def on_event(self, event: GovernanceEvent) -> None:
        if isinstance(event, RequestSuccessEvent):
            await _write(RequestLog(
                request_id=event.request_id, workspace_id=event.workspace_id,
                client_id=getattr(event, "client_id", None),
                user_id=event.user_id, use_case=event.use_case, engine=event.engine,
                provider=event.provider, model_alias=event.model_alias,
                provider_model_id=event.provider_model_id,
                input_tokens=event.input_tokens, output_tokens=event.output_tokens,
                cost_usd=event.cost_usd, latency_ms=event.latency_ms,
                stream=event.stream, status="success", call_kind=event.call_kind,
                event_kind="completion", source="live", key_id=event.key_id,
                component=event.component,
            ))
        elif isinstance(event, RequestErrorEvent):
            # WAVE 26: persist structured error_detail for the admin "why?" view.
            # Keep attribution (use_case) + the prompt-token estimate + which model
            # was tried, so an errored row is debuggable instead of all-None.
            await _write(RequestLog(
                request_id=event.request_id, workspace_id=event.workspace_id,
                client_id=getattr(event, "client_id", None),
                user_id=event.user_id, use_case=getattr(event, "use_case", None),
                engine=event.engine,
                provider=event.provider, model_alias=event.model_alias,
                provider_model_id=getattr(event, "provider_model_id", "") or "",
                input_tokens=getattr(event, "input_tokens", 0) or 0, output_tokens=0,
                cost_usd=0.0, latency_ms=event.latency_ms, stream=False,
                status="error", error_type=event.error_type,
                error_detail=event.error_detail,
                event_kind="error", source="live",
                call_kind=getattr(event, "call_kind", "chat"),
                component=getattr(event, "component", None),
            ))
        elif isinstance(event, RateLimitedEvent):
            # WAVE 26: rate_limit and budget violations now persist as request_logs
            # rows with structured error_detail so they appear in the admin view.
            is_budget = event.limit_type == "budget"
            event_kind = "budget_exceeded" if is_budget else "rate_limited"
            error_type = "budget_exceeded" if is_budget else "rate_limit_exceeded"
            if is_budget:
                detail = {
                    "category": "budget",
                    "scope": event.scope,
                    "budget_usd": event.budget_usd,
                    "spent_usd": event.spent_usd,
                    "exceeded_by_usd": (
                        round((event.spent_usd or 0) - (event.budget_usd or 0), 6)
                        if event.spent_usd is not None and event.budget_usd is not None else None
                    ),
                }
            else:
                detail = {
                    "category": "rate_limit",
                    "scope": event.scope,
                    "limit_type": event.limit_type,
                    "limit": event.limit,
                    "current": event.current,
                    "exceeded_by": (
                        max(0, (event.current or 0) - (event.limit or 0))
                        if event.current is not None and event.limit is not None else None
                    ),
                    "retry_after_seconds": event.retry_after_seconds,
                }
            await _write(RequestLog(
                request_id=event.request_id, workspace_id=event.workspace_id,
                client_id=getattr(event, "client_id", None),
                user_id=getattr(event, "user_id", None),
                use_case=getattr(event, "use_case", None), engine="bifrost",
                provider="-", model_alias=event.model_alias,
                provider_model_id="", input_tokens=0, output_tokens=0,
                cost_usd=0.0, latency_ms=0.0, stream=False,
                status="error", error_type=error_type,
                error_detail=detail,
                event_kind=event_kind, source="live",
            ))
        elif isinstance(event, GuardrailDecisionEvent):
            await _write(GuardrailViolation(
                request_id=event.request_id, workspace_id=event.workspace_id,
                rule=event.rule, detector=event.detector, action=event.action,
                stage=event.stage, excerpt=event.excerpt, source="live",
                severity=getattr(event, "severity", "medium"),
            ))
        elif isinstance(event, CacheHitEvent):
            await _write(RequestLog(
                request_id=event.request_id, workspace_id=event.workspace_id,
                client_id=getattr(event, "client_id", None),
                user_id=event.user_id, use_case=event.use_case, engine="cache",
                provider=event.provider, model_alias=event.model_alias, provider_model_id="cache",
                input_tokens=event.input_tokens, output_tokens=event.output_tokens,
                cost_usd=0.0, latency_ms=0.0, stream=False, status="success",
                call_kind="chat", event_kind="cache_hit", source="live",
                component=getattr(event, "component", None),
            ))
