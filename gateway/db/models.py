"""SQLAlchemy async models (Postgres default; SQLite fallback)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Client(Base):
    """An enterprise customer (tenant root). Workspaces belong to a Client.
    The Client owns the cross-workspace budget cap (Client \u2192 Workspace \u2192 User
    hierarchy). The Client has no credentials of its own; provider creds live
    on workspaces."""
    __tablename__ = "clients"
    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)   # slug
    name: Mapped[str] = mapped_column(String(128), default="")             # display name
    # budgets: {"client_usd": float|None, "user_usd": float|None}
    # client_usd is the cross-workspace cap; user_usd is a default applied to
    # all users under this client unless their workspace overrides it.
    budgets: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # rate_limits: {"rpm": int|None, "tpm": int|None} \u2014 client-wide ceilings
    rate_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # required_headers: ["X-Gateway-Component", ...] \u2014 enforced by governance flow
    required_headers: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"
    workspace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Foreign key to Client. Nullable for backward-compat during migration; the
    # B2 reseed populates this for every workspace, and validation enforces it.
    client_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    # human-facing label shown in the dashboard (e.g. "NovaTech \u2014 Payments")
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # alias -> ordered list of targets: [{"provider","model_id","context_window"}, ...]
    chat_models: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    embedding_models: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    default_chat_alias: Mapped[str | None] = mapped_column(String(128), nullable=True)
    guardrails: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # quotas: {"<alias>": {"rpm": int, "tpm": int}}  +  workspace-wide rate_limits
    quotas: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # budgets: {"workspace_usd": float|None, "user_usd": float|None,
    #           "per_model": {"<model_substr>": float, ...}}
    budgets: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # rate_limits: {"rpm": int|None, "tpm": int|None} \u2014 workspace ceilings
    rate_limits: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # engine_overrides: per-provider engine selection for incremental insourcing.
    # e.g. {"anthropic": "direct"} -> use our DirectEngine for Anthropic traffic.
    engine_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ModelCatalog(Base):
    """Operator-curated catalog of models. Surfaces on /v1/models per workspace,
    drives the eligibility check inside the governance flow (disabled or
    not-allowed-for-workspace \u2192 reject before invocation), and feeds capability
    flags (supports_tools / supports_images / supports_reasoning) for the UI."""
    __tablename__ = "model_catalog"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    model_id: Mapped[str] = mapped_column(String(256), index=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    context_window: Mapped[int] = mapped_column(Integer, default=0)
    supports_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_images: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=True)
    input_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    output_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Component(Base):
    """A logical app/service under a workspace that calls the gateway. Each
    component independently owns its provider config, aliases, guardrails,
    quotas and budget. Empty/None config fields inherit from the workspace.
    """
    __tablename__ = "components"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)        # e.g. "document-processing"
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chat_models: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    embedding_models: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    default_chat_alias: Mapped[str | None] = mapped_column(String(128), nullable=True)
    guardrails: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quotas: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    budgets: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WorkspaceProviderConfig(Base):
    """Per-workspace provider credentials (encrypted) + non-secret config.

    The sole source of truth for provider credentials. The decrypted key is
    injected per request; no engine (Bifrost/LiteLLM/Portkey) keeps a copy.
    """
    __tablename__ = "workspace_provider_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32))            # anthropic|bedrock|google|openai|azure
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)   # region/base_url/api_version/aliases...
    encrypted_credentials: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    bifrost_key_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bifrost_key_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(32))             # display-only
    roles: Mapped[dict[str, Any]] = mapped_column(JSON, default=lambda: ["member"])
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RequestLog(Base):
    __tablename__ = "request_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    # client_id is denormalised here so the analytics roll-up by client is a
    # single-table query; populated at write-time from the workspace's client_id.
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    use_case: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    component: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    key_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    engine: Mapped[str] = mapped_column(String(32), default="bifrost")
    provider: Mapped[str] = mapped_column(String(32))
    model_alias: Mapped[str] = mapped_column(String(128))
    provider_model_id: Mapped[str] = mapped_column(String(256))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    stream: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="success")
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # WAVE 26: structured failure context - admin-facing "why did this fail?"
    # Populated on every non-success row. Categories:
    #   provider_error: {category, http_status, raw_message, retries, fallback_attempted, fallback_provider}
    #   rate_limit:    {category, scope, limit_type, limit, current, retry_after}
    #   budget:        {category, scope, budget_usd, spent_usd, exceeded_by_usd}
    #   guardrail:     {category, rule, detector, sub_category, action, stage, excerpt, confidence}
    #   routing:       {category, alias, attempts:[{provider, model_id, reason}], result}
    #   timeout:       {category, deadline_ms, elapsed_ms}
    error_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    call_kind: Mapped[str] = mapped_column(String(32), default="chat")  # chat|embedding|batch
    # event-kind for dashboard filtering: completion|guardrail_block|fallback|rate_limited|cache_hit|error
    event_kind: Mapped[str] = mapped_column(String(24), default="completion", index=True)
    # provenance: live | synthetic  (synthetic rows are safely truncatable)
    source: Mapped[str] = mapped_column(String(16), default="live", index=True)


class GuardrailViolation(Base):
    __tablename__ = "guardrail_violations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), index=True)
    rule: Mapped[str] = mapped_column(String(128))
    detector: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))            # block|redact|audit
    stage: Mapped[str] = mapped_column(String(16))             # input|output
    excerpt: Mapped[str] = mapped_column(Text, default="")     # masked
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    source: Mapped[str] = mapped_column(String(16), default="live", index=True)


class AuditLog(Base):
    """Immutable admin-action audit trail (who/what/when). Append-only."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    principal: Mapped[str] = mapped_column(String(128), index=True)   # who
    action: Mapped[str] = mapped_column(String(64), index=True)       # what (e.g. workspace.create)
    target: Mapped[str] = mapped_column(String(128), default="")      # resource id
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class CustomPricing(Base):
    """Operator pricing override (wins over the synced public table). Keyed by a
    model-id substring; the override applies when the model id contains the key."""
    __tablename__ = "custom_pricing"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_substr: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    input_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    output_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(String(256), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GuardrailProfile(Base):
    """A reusable detector configuration. detector_type selects the implementation;
    config carries patterns/thresholds/action. Profiles are linked from many rules."""
    __tablename__ = "guardrail_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    # regex | secrets | keyword | presidio | bedrock | azure | model-armor
    detector_type: Mapped[str] = mapped_column(String(32))
    policy_name: Mapped[str] = mapped_column(String(128), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scope: Mapped[str] = mapped_column(String(16), default="global")  # global|workspace|component
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    component: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GuardrailRule(Base):
    """When (CEL) + which detectors (profile_ids) + action. Evaluated in our layer."""
    __tablename__ = "guardrail_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cel_expression: Mapped[str] = mapped_column(Text, default="true")
    # structured visual-builder spec (round-trips on edit). null when authored as raw CEL.
    builder_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    apply_to: Mapped[str] = mapped_column(String(8), default="input")   # input|output|both
    action: Mapped[str] = mapped_column(String(16), default="block")    # block|redact|audit
    sampling_rate: Mapped[float] = mapped_column(Float, default=1.0)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=1000)
    profile_ids: Mapped[list] = mapped_column(JSON, default=list)
    scope: Mapped[str] = mapped_column(String(16), default="global")    # global|workspace|component
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    component: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FXRate(Base):
    """Daily FX rates relative to USD. Synced from a free public API.
    Time-accurate: cost_usd × rate(currency, row_date) gives the historical
    converted cost so past reports don't shift when FX moves."""
    __tablename__ = "fx_rates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    currency: Mapped[str] = mapped_column(String(3), index=True)      # INR, EUR, GBP, USD
    date: Mapped[datetime] = mapped_column(DateTime, index=True)      # day granularity
    rate_to_usd: Mapped[float] = mapped_column(Float)                 # 1 USD = N <currency>
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GatewaySettings(Base):
    """Singleton key-value settings (currency, etc)."""
    __tablename__ = "gateway_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RateCard(Base):
    """Per-client chargeback rate card. billed = raw_usd × (1 + markup_pct/100).
    Falls back to a global default if no per-client row exists."""
    __tablename__ = "rate_cards"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # NULL = global default
    markup_pct: Mapped[float] = mapped_column(Float, default=30.0)    # e.g. 30 = 30% margin
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
