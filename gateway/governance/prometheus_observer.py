"""Prometheus observer \u2014 translates governance events into Prometheus metrics
with the WAVE 19 tenancy label set (client/workspace/user/component/provider/
model/status). Every metric uses a strict helper from `core.metrics` that
defaults missing identifiers to ``"-"`` so series stay stable across requests."""
from __future__ import annotations

from gateway.core import metrics as M
from gateway.governance.observer import (
    BudgetAlertEvent,
    CacheHitEvent,
    FallbackEvent,
    GovernanceEvent,
    GovernanceObserver,
    GuardrailDecisionEvent,
    RateLimitedEvent,
    RequestErrorEvent,
    RequestSuccessEvent,
)


class PrometheusObserver(GovernanceObserver):
    async def on_event(self, event: GovernanceEvent) -> None:
        if isinstance(event, RequestSuccessEvent):
            M.REQUESTS.labels(**M.request_labels(
                client_id=event.client_id, workspace_id=event.workspace_id,
                user_id=event.user_id, component=event.component,
                provider=event.provider, model=event.model_alias, status="success")).inc()
            for direction, count in (("input", event.input_tokens),
                                      ("output", event.output_tokens)):
                M.TOKENS.labels(**M.token_labels(
                    client_id=event.client_id, workspace_id=event.workspace_id,
                    user_id=event.user_id, component=event.component,
                    provider=event.provider, model=event.model_alias,
                    direction=direction)).inc(count)
            M.COST.labels(**M.cost_labels(
                client_id=event.client_id, workspace_id=event.workspace_id,
                user_id=event.user_id, component=event.component,
                provider=event.provider, model=event.model_alias)).inc(event.cost_usd)
            M.LATENCY.observe(event.latency_ms / 1000.0)
            prov_ms = event.provider_ms or event.latency_ms
            M.PROVIDER_LATENCY.labels(provider=M._safe(event.provider),
                                       model=M._safe(event.model_alias, max_len=64)).observe(prov_ms / 1000.0)

        elif isinstance(event, RequestErrorEvent):
            M.REQUESTS.labels(**M.request_labels(
                client_id=event.client_id, workspace_id=event.workspace_id,
                user_id=event.user_id, component=getattr(event, "component", None),
                provider=event.provider, model=event.model_alias, status="error")).inc()

        elif isinstance(event, GuardrailDecisionEvent):
            M.GUARDRAIL.labels(**M.guardrail_labels(
                client_id=event.client_id, workspace_id=event.workspace_id,
                component=event.component, action=event.action)).inc()

        elif isinstance(event, FallbackEvent):
            M.FALLBACKS.labels(**M.fallback_labels(
                client_id=event.client_id, workspace_id=event.workspace_id,
                from_provider=event.from_provider, to_provider=event.to_provider,
                reason=event.reason)).inc()

        elif isinstance(event, CacheHitEvent):
            M.CACHE_HITS.labels(**M.cache_labels(
                client_id=event.client_id, workspace_id=event.workspace_id,
                component=event.component)).inc()

        elif isinstance(event, RateLimitedEvent):
            M.RATE_LIMITED.labels(**M.rate_limit_labels(
                client_id=event.client_id, workspace_id=event.workspace_id,
                scope=event.scope, limit_type=event.limit_type)).inc()
            if event.limit_type == "budget":
                M.BUDGET_EXCEEDED.labels(**M.budget_labels(
                    client_id=event.client_id, workspace_id=event.workspace_id,
                    scope=event.scope)).inc()

        elif isinstance(event, BudgetAlertEvent):
            M.BUDGET_ALERTS.labels(**M.budget_alert_labels(
                client_id=event.client_id, workspace_id=event.workspace_id,
                scope=event.scope, threshold=event.threshold)).inc()
