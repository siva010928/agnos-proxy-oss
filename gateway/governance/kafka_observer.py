"""Kafka governance observer - publishes our own self-described event envelope.

Enabled only when KAFKA_BROKERS is set. The envelope is a clean, generic design
owned by this gateway: a top-level frame (schema_version, event_kind, occurred_at,
correlation_id, idempotency_key) + a snake_case payload. Any downstream consumer
(analytics, billing, a metrics hydrator) subscribes to `agnos-proxy.governance.v1`
without any gateway change.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from gateway.config import settings
from gateway.governance.observer import (
    BudgetAlertEvent,
    CacheHitEvent,
    FallbackEvent,
    GovernanceEvent,
    GovernanceObserver,
    GuardrailDecisionEvent,
    RateLimitedEvent,
    RequestErrorEvent,
    RequestStartEvent,
    RequestSuccessEvent,
)


def _frame(event_kind: str, correlation_id: str | None, payload: dict) -> dict:
    return {
        "schema_version": 1,
        "event_kind": event_kind,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "idempotency_key": str(uuid.uuid4()),
        "payload": payload,
    }


def _envelope(event: GovernanceEvent) -> dict | None:
    if isinstance(event, RequestStartEvent):
        return _frame("request_start", event.request_id, {
            "client_id": getattr(event, "client_id", None),
            "workspace_id": event.workspace_id, "user_id": event.user_id,
            "use_case": event.use_case,
            "engine": event.engine, "provider": event.provider, "model": event.model_alias,
            "provider_model_id": event.provider_model_id,
            "component": getattr(event, "component", None),
            "metadata": {"call_kind": event.call_kind, "stream": event.stream,
                          "has_tools": event.has_tools},
            "status": "started",
        })
    if isinstance(event, RequestSuccessEvent):
        return _frame("completion", event.request_id, {
            "client_id": getattr(event, "client_id", None),
            "workspace_id": event.workspace_id, "user_id": event.user_id,
            "component": getattr(event, "component", None), "use_case": event.use_case,
            "engine": event.engine, "provider": event.provider, "model": event.model_alias,
            "provider_model_id": event.provider_model_id,
            "input_tokens": event.input_tokens, "output_tokens": event.output_tokens,
            "cost_usd": event.cost_usd, "latency_ms": event.latency_ms, "status": "success",
            "metadata": {"call_kind": event.call_kind, "stream": event.stream, "attempt": event.attempt},
        })
    if isinstance(event, GuardrailDecisionEvent):
        return _frame("guardrail_block", event.request_id, {
            "client_id": getattr(event, "client_id", None),
            "workspace_id": event.workspace_id, "model": event.model_alias,
            "component": getattr(event, "component", None),
            "rule": event.rule, "detector": event.detector, "action": event.action,
            "stage": event.stage, "status": "blocked"})
    if isinstance(event, FallbackEvent):
        return _frame("fallback", event.request_id, {
            "client_id": getattr(event, "client_id", None),
            "workspace_id": event.workspace_id, "model": event.model_alias,
            "from_provider": event.from_provider, "to_provider": event.to_provider,
            "reason": event.reason, "status": "fallback"})
    if isinstance(event, RateLimitedEvent):
        return _frame("rate_limited", event.request_id, {
            "client_id": getattr(event, "client_id", None),
            "user_id": getattr(event, "user_id", None),
            "workspace_id": event.workspace_id, "model": event.model_alias,
            "limit_type": event.limit_type,
            "scope": getattr(event, "scope", "workspace"),
            "status": "rate_limited"})
    if isinstance(event, CacheHitEvent):
        return _frame("cache_hit", event.request_id, {
            "client_id": getattr(event, "client_id", None),
            "workspace_id": event.workspace_id, "user_id": event.user_id,
            "component": getattr(event, "component", None), "use_case": event.use_case,
            "provider": event.provider, "model": event.model_alias,
            "input_tokens": event.input_tokens, "output_tokens": event.output_tokens,
            "cost_saved_usd": event.cost_saved_usd, "status": "cache_hit"})
    if isinstance(event, BudgetAlertEvent):
        return _frame("budget_alert", None, {
            "client_id": getattr(event, "client_id", None),
            "user_id": getattr(event, "user_id", None),
            "workspace_id": event.workspace_id, "scope": event.scope,
            "threshold": event.threshold, "spend_usd": event.spend_usd,
            "cap_usd": event.cap_usd, "status": "budget_alert"})
    if isinstance(event, RequestErrorEvent):
        return _frame("error", event.request_id, {
            "client_id": getattr(event, "client_id", None),
            "workspace_id": event.workspace_id, "provider": event.provider,
            "component": getattr(event, "component", None),
            "model": event.model_alias, "error_type": event.error_type, "status": "error"})
    return None


class KafkaObserver(GovernanceObserver):
    def __init__(self):
        self._producer = None
        self._dlq: list[tuple[bytes, bytes]] = []   # dead-letter retry buffer

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer
        self._producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_brokers)
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def on_event(self, event: GovernanceEvent) -> None:
        if not self._producer:
            return
        env = _envelope(event)
        if env is None:
            return
        key = (env["payload"].get("workspace_id") or "").encode()
        payload = json.dumps(env).encode()
        try:
            await self._producer.send_and_wait(settings.kafka_topic, payload, key=key)
            # opportunistically flush any buffered dead-letters
            while self._dlq:
                k, p = self._dlq[0]
                await self._producer.send_and_wait(settings.kafka_topic, p, key=k)
                self._dlq.pop(0)
        except Exception as exc:  # noqa: BLE001 - never silently drop: buffer + count
            self._dlq.append((key, payload))
            if len(self._dlq) > 1000:
                self._dlq.pop(0)
            try:
                from gateway.core import metrics as _M
                _M.KAFKA_DLQ.inc()
            except Exception:
                pass
            print(f"[kafka] publish failed (buffered to DLQ, depth={len(self._dlq)}): {exc}")
