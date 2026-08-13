"""Deterministic gateway unit tests (no network/DB)."""
from __future__ import annotations

import json

from gateway.core import errors
from gateway.core.auth import WorkspaceContext
from gateway.core.cost import compute_cost
from gateway.core.guardrails.detectors import RegexPIIDetector, SecretsDetector, KeywordDetector
from gateway.core.guardrails.engine import GuardrailEngine
from gateway.core.rate_limit import RateLimiter
from gateway.core.registry import resolve_chat_targets


def _ws(**kw):
    base = dict(workspace_id="ws-x", name="x", chat_models={}, embedding_models={},
                default_chat_alias=None, guardrails={}, quotas={}, budgets={}, roles=["member"])
    base.update(kw)
    return WorkspaceContext(**base)


# ── routing modes ──
def test_routing_mode_a_alias_with_fallback():
    ws = _ws(chat_models={"claude-sonnet-4-5": [
        {"provider": "bedrock", "model_id": "us.x", "context_window": 200000},
        {"provider": "anthropic", "model_id": "claude-x"}]})
    alias, targets = resolve_chat_targets(ws, {"model": "claude-sonnet-4-5"}, {})
    assert alias == "claude-sonnet-4-5"
    assert [t.provider for t in targets] == ["bedrock", "anthropic"]   # fallback chain order


def test_routing_mode_b_provider_prefixed():
    ws = _ws()
    alias, targets = resolve_chat_targets(ws, {"model": "bedrock:us.anthropic.claude"}, {})
    assert targets[0].provider == "bedrock" and targets[0].model_id == "us.anthropic.claude"


def test_routing_mode_c_default_chat():
    ws = _ws(chat_models={"claude-sonnet-4-5": [{"provider": "bedrock", "model_id": "us.x"}]},
             default_chat_alias="claude-sonnet-4-5")
    alias, targets = resolve_chat_targets(ws, {"model": "default-chat"}, {})
    assert alias == "claude-sonnet-4-5" and targets[0].provider == "bedrock"


# ── cost ──
def test_cost_compute():
    c = compute_cost("us.anthropic.claude-sonnet-4-5-20250929-v1:0", 1000, 1000)
    assert c > 0  # priced from synced/builtin table


def test_cost_unknown_model_zero():
    assert compute_cost("totally-unknown-model-xyz", 1000, 1000) == 0.0


# ── rate limit ──
def test_rate_limiter_blocks_after_rpm():
    rl = RateLimiter()
    q = {"rpm": 3, "tpm": 10_000_000}
    oks = [rl.check("w", "m", q, 10)[0] for _ in range(5)]
    assert oks[:3] == [True, True, True] and oks[3] is False


# ── guardrail detectors + CEL engine ──
def test_secrets_and_keyword_detectors():
    assert any(f.category == "aws_access_key" for f in SecretsDetector().scan("AKIAIOSFODNN7EXAMPLE"))
    assert KeywordDetector(["phoenix"]).scan("project phoenix go")


def test_engine_redact_keeps_structure():
    out = GuardrailEngine().run_input(
        {"model": "m", "messages": [{"role": "user", "content": "ssn 123-45-6789"}]},
        {"pii_detection": True, "mode": "redact"})
    assert out.action == "redact" and "[REDACTED:ssn]" in out.redacted_messages[0]["content"]


# ── error mapping (bifrost -> openai-clean) ──
def test_bifrost_auth_to_provider_auth_error():
    code, body = errors.map_bifrost_error(401, {"error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    assert code == 502 and body["error"]["type"] == "provider_auth_error"


def test_bifrost_context_window():
    code, body = errors.map_bifrost_error(400, {"error": {"message": "context length exceeded"}})
    assert body["error"]["type"] == "context_length_exceeded"


# ── kafka envelope shaping ──
def test_kafka_envelope_shape():
    from gateway.governance.kafka_observer import _envelope
    from gateway.governance.observer import RequestSuccessEvent
    ev = RequestSuccessEvent(request_id="r1", workspace_id="ws", user_id="u", use_case="uc",
                             model_alias="claude-sonnet-4-5", provider="bedrock", provider_model_id="us.x",
                             engine="bifrost", input_tokens=10, output_tokens=5, cost_usd=0.01,
                             latency_ms=12.3, stream=False)
    env = _envelope(ev)
    assert env["event_kind"] == "completion"
    p = env["payload"]
    assert p["workspace_id"] == "ws" and p["provider"] == "bedrock" and p["input_tokens"] == 10
    assert "cost_usd" in p and env["schema_version"] == 1


# ── DirectEngine OpenAI-shape conversion (no network) ──
def test_direct_engine_openai_body():
    from gateway.engines.direct_engine import _openai_body
    b = _openai_body("us.x", "hello", {"inputTokens": 3, "outputTokens": 2, "totalTokens": 5}, "stop")
    assert b["object"] == "chat.completion"
    assert b["choices"][0]["message"]["content"] == "hello"
    assert b["usage"]["prompt_tokens"] == 3 and b["usage"]["completion_tokens"] == 2


# ── session JWT round-trip (RBAC) ──
def test_session_jwt_roundtrip():
    from gateway.core.security import issue_session, verify_session
    tok = issue_session("admin", ["admin"])
    data = verify_session(tok)
    assert data and data["sub"] == "admin" and "admin" in data["roles"]
    assert verify_session(tok + "tamper") is None
