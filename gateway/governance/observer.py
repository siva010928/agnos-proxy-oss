"""Governance observer interface + typed events (async, mirrors Agnos StageObserver)."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RequestStartEvent:
    request_id: str
    workspace_id: str
    user_id: str | None
    use_case: str | None
    model_alias: str
    provider: str
    provider_model_id: str
    engine: str
    stream: bool
    has_tools: bool
    call_kind: str = "chat"
    started_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    # WAVE 19 tenancy attribution
    client_id: str | None = None
    component: str | None = None


@dataclass(frozen=True)
class RequestSuccessEvent:
    request_id: str
    workspace_id: str
    user_id: str | None
    use_case: str | None
    model_alias: str
    provider: str
    provider_model_id: str
    engine: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    stream: bool
    call_kind: str = "chat"
    attempt: int = 1
    provider_ms: float = 0.0   # upstream provider wall-clock latency (excl. gateway overhead)
    key_id: int | None = None
    component: str | None = None
    # WAVE 19 tenancy attribution
    client_id: str | None = None


@dataclass(frozen=True)
class RequestErrorEvent:
    request_id: str
    workspace_id: str
    user_id: str | None
    model_alias: str
    provider: str
    engine: str
    error_type: str
    http_status: int
    message: str
    latency_ms: float
    client_id: str | None = None
    component: str | None = None
    # WAVE 26: structured failure context. See RequestLog.error_detail docstring
    # for category-specific shape.
    error_detail: dict[str, Any] | None = None
    # Capture attribution + the prompt-token estimate even on failure, so an
    # errored request is still debuggable/attributable (use_case, which model was
    # tried, and roughly how big the prompt was). Cost stays 0 (no completion).
    use_case: str | None = None
    input_tokens: int = 0
    provider_model_id: str = ""
    # chat|embedding - so a failed embedding is attributed as such (mirrors
    # RequestSuccessEvent.call_kind). Without this, embeddings error paths that
    # pass call_kind="embedding" would crash the emit with a TypeError.
    call_kind: str = "chat"


@dataclass(frozen=True)
class GuardrailDecisionEvent:
    request_id: str
    workspace_id: str
    model_alias: str
    rule: str
    detector: str
    action: str          # block|redact|audit
    stage: str           # input|output
    excerpt: str
    client_id: str | None = None
    component: str | None = None
    # WAVE 26: extra context for admin UI
    sub_category: str | None = None     # e.g. 'us_phone', 'aws_access_key'
    confidence: float = 1.0
    severity: str = "medium"


@dataclass(frozen=True)
class FallbackEvent:
    request_id: str
    workspace_id: str
    model_alias: str
    from_provider: str
    to_provider: str
    reason: str
    client_id: str | None = None


@dataclass(frozen=True)
class RateLimitedEvent:
    request_id: str
    workspace_id: str
    model_alias: str
    limit_type: str      # rpm|tpm|budget
    # WAVE 19: which scope tripped first \u2014 client|workspace|user|model
    scope: str = "workspace"
    client_id: str | None = None
    user_id: str | None = None
    use_case: str | None = None
    # WAVE 26: numeric context for admin UI ("you used 127 of 100 RPM")
    limit: int | None = None
    current: int | None = None
    retry_after_seconds: int | None = None
    # For budget violations:
    budget_usd: float | None = None
    spent_usd: float | None = None


@dataclass(frozen=True)
class CacheHitEvent:
    request_id: str
    workspace_id: str
    user_id: str | None
    use_case: str | None
    model_alias: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_saved_usd: float
    component: str | None = None
    client_id: str | None = None


@dataclass(frozen=True)
class BudgetAlertEvent:
    workspace_id: str
    scope: str           # client|workspace|user
    threshold: int       # 80|100
    spend_usd: float
    cap_usd: float
    client_id: str | None = None
    user_id: str | None = None


GovernanceEvent = (
    RequestStartEvent | RequestSuccessEvent | RequestErrorEvent
    | GuardrailDecisionEvent | FallbackEvent | RateLimitedEvent
    | CacheHitEvent | BudgetAlertEvent
)


class GovernanceObserver(ABC):
    @abstractmethod
    async def on_event(self, event: GovernanceEvent) -> None: ...

    async def start(self) -> None:  # optional lifecycle
        ...

    async def stop(self) -> None:
        ...
