"""Deterministic backend tests (no network/DB) - covers the gaps closed in the
backend hardening pass: new metrics, anti-corruption boundary, fallback/breaker,
budgets, chunker, observer-bus overflow, guardrails depth, RBAC, parity, etc.

Run: pytest tests/test_backend.py -q
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from gateway.config import settings
from gateway.core import errors


# ───────────────────────── metrics (item 36 + WAVE 19 TRACK D2) ─────────────────────────
def test_metrics_new_series_present():
    from gateway.core import metrics as M
    M.FALLBACKS.labels(client="-", workspace="ws", from_provider="bedrock",
                       to_provider="anthropic", reason="primary_failed").inc()
    M.PROVIDER_LATENCY.labels(provider="bedrock", model="us.anthropic.claude").observe(0.9)
    M.OVERHEAD.labels(stage="total").observe(0.003)
    M.OVERHEAD.labels(stage="auth").observe(0.0005)
    body = M.render()[0].decode()
    assert "gateway_fallbacks_total" in body
    assert "gateway_provider_latency_seconds" in body
    assert 'gateway_overhead_seconds_bucket{le="0.005",stage="total"}' in body or 'stage="total"' in body


def test_prometheus_observer_records_provider_latency_and_fallback():
    from gateway.core import metrics as M
    from gateway.governance.observer import FallbackEvent, RequestSuccessEvent
    from gateway.governance.prometheus_observer import PrometheusObserver
    obs = PrometheusObserver()
    ev = RequestSuccessEvent(request_id="r", workspace_id="wsP", user_id=None, use_case=None,
                             model_alias="m", provider="gemini", provider_model_id="gemini-2.5-flash",
                             engine="bifrost", input_tokens=1, output_tokens=1, cost_usd=0.0,
                             latency_ms=800.0, stream=False, provider_ms=750.0,
                             client_id="cP")
    asyncio.run(obs.on_event(ev))
    asyncio.run(obs.on_event(FallbackEvent(request_id="r", workspace_id="wsP", model_alias="m",
                                           from_provider="gemini", to_provider="bedrock",
                                           reason="primary_failed", client_id="cP")))
    body = M.render()[0].decode()
    assert 'gateway_provider_latency_seconds_count{model=' in body and 'provider="gemini"' in body
    assert 'to_provider="bedrock"' in body
    # WAVE 19 D2 - the new tenancy labels are present on REQUESTS/COST
    assert 'client="cP"' in body
    assert 'workspace="wsP"' in body


# ─────────────── anti-corruption boundary (items 7/8) ───────────────
def test_engineresult_strips_extra_fields():
    from gateway.engines.base import EngineResult
    r = EngineResult({"choices": [{"message": {"content": "hi"}}],
                      "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                      "extra_fields": {"provider": "bedrock", "latency": 100},
                      "bifrost_config": {"x": 1}})
    assert "extra_fields" not in r.body
    assert "bifrost_config" not in r.body
    assert r.usage == {"input_tokens": 3, "output_tokens": 2}
    assert r.ok


def test_engineresult_strips_x_bf_keys():
    from gateway.engines.base import EngineResult
    r = EngineResult({"choices": [], "x-bf-trace": "abc"})
    assert all(not k.lower().startswith("x-bf-") for k in r.body)


def test_direct_engine_body_clean_at_boundary():
    """DirectEngine injects extra_fields internally; EngineResult must strip it."""
    from gateway.engines.base import EngineResult
    from gateway.engines.direct_engine import _openai_body
    raw = _openai_body("us.x", "hello", {"inputTokens": 3, "outputTokens": 2, "totalTokens": 5}, "stop")
    assert "extra_fields" in raw  # raw helper still carries it
    assert "extra_fields" not in EngineResult(raw).body  # boundary strips it


def test_bifrost_clean_sse_line():
    from gateway.engines.bifrost_engine import BifrostEngine
    dirty = 'data: {"choices":[{"delta":{"content":"hi"}}],"extra_fields":{"provider":"bedrock"}}'
    cleaned = BifrostEngine._clean_sse_line(dirty)
    assert "extra_fields" not in cleaned and "hi" in cleaned
    assert BifrostEngine._clean_sse_line("data: [DONE]") == "data: [DONE]"


def test_bifrost_payload_and_headers_no_leak():
    from gateway.core.registry import ResolvedTarget
    from gateway.engines.bifrost_engine import BifrostEngine
    eng = BifrostEngine()
    # STATELESS: the raw provider key is injected per request via Bifrost's direct-key
    # path; nothing is stored or selected by name in the engine.
    t = ResolvedTarget(provider="anthropic", model_id="claude-x", credentials={"api_key": "sk-test"})
    payload = eng._payload({"model": "claude", "stream": True, "messages": []}, t)
    assert payload["model"] == "anthropic/claude-x" and "stream" not in payload
    h = eng._headers(t)
    assert h["x-bf-direct-key"] == "true"      # per-request direct key, not a stored managed key
    assert h["x-api-key"] == "sk-test"          # anthropic's native auth header
    assert "x-bf-api-key" not in h              # the old stored-key selector is gone
    # bedrock carries a Bedrock API key (bearer) in Authorization
    tb = ResolvedTarget(provider="bedrock", model_id="us.x", credentials={"bedrock_api_key": "ABSK-test"})
    hb = eng._headers(tb)
    assert hb["x-bf-direct-key"] == "true" and hb["Authorization"] == "Bearer ABSK-test"


# ───────────────── fallback / circuit breaker (items 23, 26) ─────────────────
class _FakeEngine:
    name = "fake"

    async def chat(self, body, target):
        from gateway.engines.base import EngineResult
        if target.provider == "bedrock":
            return EngineResult({"error": {"message": "boom", "type": "upstream_error"}}, 502)
        return EngineResult({"choices": [{"message": {"content": "ok"}}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


def test_fallback_primary_fail_secondary_success(monkeypatch):
    import gateway.core.fallback as fb_mod
    from gateway.core.registry import ResolvedTarget

    async def _no_cred(ws, provider):
        return None
    monkeypatch.setattr(fb_mod, "get_provider_credential", _no_cred)

    targets = [ResolvedTarget(provider="bedrock", model_id="us.x"),
               ResolvedTarget(provider="anthropic", model_id="claude-x")]
    res = asyncio.run(fb_mod.execute(_FakeEngine(), {"messages": []}, targets, "ws", max_retries=0))
    assert res.target.provider == "anthropic" and res.result.ok
    assert ("bedrock", "anthropic", "primary_failed") in res.fallbacks_emitted


def test_circuit_breaker_opens_and_resets():
    from gateway.core.fallback import CircuitBreaker
    cb = CircuitBreaker(threshold=2, cooldown=1000.0)
    cb.record("ws", "bedrock", ok=False)
    assert not cb.is_open("ws", "bedrock")
    cb.record("ws", "bedrock", ok=False)
    assert cb.is_open("ws", "bedrock")          # opened after threshold
    cb._b["ws:bedrock"].opened_at = 0.0          # simulate cooldown elapsed
    assert not cb.is_open("ws", "bedrock")
    cb.record("ws", "bedrock", ok=True)
    assert cb._b["ws:bedrock"].fails == 0        # success resets


# ───────────────────────── budgets (item 31) ─────────────────────────
def _preload_budget_cache(client_spend, ws_spend, user_spend, per_model_spend=0.0):
    """Seed the WAVE 19 hierarchical budget cache. Key shape:
    (client_id, workspace_id, user_id, model_substr) \u2192 ({client, workspace, user, per_model}, expiry)"""
    import time as _t
    from gateway.core import budgets
    budgets._CACHE[("clientB", "wsB", "userB", None)] = (
        {"client": client_spend, "workspace": ws_spend, "user": user_spend, "per_model": per_model_spend},
        _t.monotonic() + 100,
    )


async def _no_client_caps(_cid):
    """Stub that bypasses the DB lookup so unit tests don't touch asyncpg
    across asyncio.run() invocations (which close the event loop)."""
    return {}


def test_budget_blocks_workspace_cap(monkeypatch):
    from gateway.core.budgets import check_budget
    monkeypatch.setattr("gateway.core.budgets._client_caps", _no_client_caps)
    _preload_budget_cache(0.0, 60.0, 1.0)
    decision = asyncio.run(check_budget("clientB", "wsB", "userB",
                                        {"workspace_usd": 50.0, "user_usd": 10.0}))
    assert decision.allowed is False and decision.scope == "workspace"


def test_budget_blocks_user_cap(monkeypatch):
    from gateway.core.budgets import check_budget
    monkeypatch.setattr("gateway.core.budgets._client_caps", _no_client_caps)
    _preload_budget_cache(0.0, 10.0, 12.0)
    decision = asyncio.run(check_budget("clientB", "wsB", "userB",
                                        {"workspace_usd": 50.0, "user_usd": 10.0}))
    assert decision.allowed is False and decision.scope == "user"


def test_budget_allows_under_cap(monkeypatch):
    from gateway.core.budgets import check_budget
    monkeypatch.setattr("gateway.core.budgets._client_caps", _no_client_caps)
    _preload_budget_cache(0.0, 1.0, 1.0)
    decision = asyncio.run(check_budget("clientB", "wsB", "userB",
                                        {"workspace_usd": 50.0, "user_usd": 10.0}))
    assert decision.allowed is True and decision.scope == ""


def test_budget_blocks_client_cap_first(monkeypatch):
    """WAVE 19 hierarchy: a client-level cap trips BEFORE workspace/user even
    if those are unbounded. Proof of Client \u2192 Workspace \u2192 User ordering."""
    from gateway.core.budgets import check_budget, _CACHE
    import time as _t
    _CACHE[("clientB", "wsB", "userB", None)] = (
        {"client": 5500.0, "workspace": 100.0, "user": 50.0, "per_model": 0.0},
        _t.monotonic() + 100,
    )
    # Client cap is loaded from DB; stub the lookup
    async def _fake_caps(_cid):
        return {"client_usd": 5000.0}
    monkeypatch.setattr("gateway.core.budgets._client_caps", _fake_caps)
    decision = asyncio.run(check_budget("clientB", "wsB", "userB",
                                        {"workspace_usd": 200.0, "user_usd": 100.0}))
    assert decision.allowed is False and decision.scope == "client"
    assert decision.cap == 5000.0 and decision.spend == 5500.0


def test_budget_blocks_per_model_cap(monkeypatch):
    """Per-model cap (Workspace.budgets.per_model) trips when the substring matches."""
    from gateway.core.budgets import check_budget, _CACHE
    import time as _t
    _CACHE[("clientB", "wsB", "userB", "claude-sonnet")] = (
        {"client": 0.0, "workspace": 5.0, "user": 1.0, "per_model": 9.5},
        _t.monotonic() + 100,
    )
    async def _no_caps(_cid):
        return {}
    monkeypatch.setattr("gateway.core.budgets._client_caps", _no_caps)
    decision = asyncio.run(check_budget(
        "clientB", "wsB", "userB",
        {"workspace_usd": 100.0, "user_usd": 50.0,
         "per_model": {"claude-sonnet": 8.0}},
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    ))
    assert decision.allowed is False and decision.scope == "per_model"
    assert decision.cap == 8.0


def test_required_headers_missing_returns_400_shape():
    from gateway.core.required_headers import (
        missing_required_headers, required_headers_error,
    )
    miss = missing_required_headers(
        {"Authorization": "Bearer x", "Content-Type": "application/json"},
        ["X-Gateway-Component"],
    )
    assert miss == ["X-Gateway-Component"]
    err = required_headers_error(miss)
    assert err["error"]["type"] == "missing_required_header"
    assert "X-Gateway-Component" in err["error"]["message"]
    assert err["error"]["param"] == "X-Gateway-Component"


def test_required_headers_present_passes():
    from gateway.core.required_headers import missing_required_headers
    miss = missing_required_headers(
        {"x-gateway-component": "document-processing"},   # case-insensitive
        ["X-Gateway-Component"],
    )
    assert miss == []


def test_budget_live_delta_traps_back_to_back_overspend(monkeypatch):
    """Cache-warmth correctness fix (WAVE 19 TRACK C2).

    Scenario: client cap = $1.00. First request sees DB=$0.0 (cache miss),
    completes with cost $0.60 \u2014 allowed. Second request must SEE the
    $0.60 even though the Postgres write is still in flight; otherwise
    the cap doesn't enforce until the cache TTL ages. Without
    add_live_spend(), the second check would still serve $0.0 from the
    just-cached snapshot and let the tenant overspend.
    """
    from gateway.core import budgets
    from gateway.core.budgets import add_live_spend, check_budget

    # Stub the DB lookup so it always returns 0 (the just-completed cost
    # has not yet been committed to RequestLog).
    async def _zero_query(*_a, **_kw):
        return {"client": 0.0, "workspace": 0.0, "user": 0.0, "per_model": 0.0}
    monkeypatch.setattr("gateway.core.budgets._query_spends", _zero_query)
    async def _client_caps(_cid):
        return {"client_usd": 1.0}
    monkeypatch.setattr("gateway.core.budgets._client_caps", _client_caps)
    # clean state
    budgets._CACHE.clear()
    budgets._LIVE_DELTA.clear()
    budgets._ALERTED.clear()

    # Request 1 \u2014 fresh tenant: $0 spent, cap $1 \u2014 ALLOWED.
    d1 = asyncio.run(check_budget("clientLD", "wsLD", "userLD", {}))
    assert d1.allowed is True

    # Pretend request 1 cost $0.60. The async Postgres write hasn't landed
    # yet, but add_live_spend records it in memory.
    add_live_spend("clientLD", "wsLD", "userLD", 0.60, "claude-sonnet-4-5")

    # Request 2: DB still says $0, but the in-memory delta is $0.60.
    # 0.60 < 1.0 \u2014 still ALLOWED.
    d2 = asyncio.run(check_budget("clientLD", "wsLD", "userLD", {}))
    assert d2.allowed is True

    # Pretend request 2 cost another $0.60 (running total $1.20 against $1 cap).
    add_live_spend("clientLD", "wsLD", "userLD", 0.60, "claude-sonnet-4-5")

    # Request 3: must 402. DB still says $0; live delta is $1.20 \u2265 $1.00 cap.
    # Without the live-delta fix this would return ALLOWED because the cached
    # snapshot is $0 and the cap looks fine.
    d3 = asyncio.run(check_budget("clientLD", "wsLD", "userLD", {}))
    assert d3.allowed is False
    assert d3.scope == "client"
    assert d3.spend >= 1.0


def test_rate_limit_first_violation_user_then_workspace_then_client_then_model():
    """WAVE 19 TRACK C2: per-scope rate limits eval in User \u2192 Workspace \u2192
    Client \u2192 Model order, first violation wins."""
    from gateway.core.rate_limit import RateLimiter

    # ── User scope wins (RPM=2, fire 3) ──
    rl = RateLimiter()
    for i in range(2):
        ok, scope, lt, _ = rl.check_multi_scope(
            client_id="cX", workspace_id="wX", user_id="uX", alias="aX",
            client_rl={"rpm": 1000}, workspace_rl={"rpm": 100, "user": {"rpm": 2}},
            model_quota={"rpm": 50}, est_tokens=1)
        assert ok, f"req {i} should be allowed, got scope={scope} lt={lt}"
    ok, scope, lt, _ = rl.check_multi_scope(
        client_id="cX", workspace_id="wX", user_id="uX", alias="aX",
        client_rl={"rpm": 1000}, workspace_rl={"rpm": 100, "user": {"rpm": 2}},
        model_quota={"rpm": 50}, est_tokens=1)
    assert not ok and scope == "user" and lt == "rpm"

    # ── Workspace scope wins (no per-user, workspace RPM=2) ──
    rl = RateLimiter()
    for _ in range(2):
        assert rl.check_multi_scope(
            client_id="cY", workspace_id="wY", user_id=None, alias="aY",
            client_rl={"rpm": 1000}, workspace_rl={"rpm": 2},
            model_quota={"rpm": 50}, est_tokens=1)[0]
    ok, scope, lt, _ = rl.check_multi_scope(
        client_id="cY", workspace_id="wY", user_id=None, alias="aY",
        client_rl={"rpm": 1000}, workspace_rl={"rpm": 2},
        model_quota={"rpm": 50}, est_tokens=1)
    assert not ok and scope == "workspace" and lt == "rpm"

    # ── Client scope wins (workspace RPM very high; client RPM=2) ──
    rl = RateLimiter()
    for _ in range(2):
        assert rl.check_multi_scope(
            client_id="cZ", workspace_id="wZ", user_id=None, alias="aZ",
            client_rl={"rpm": 2}, workspace_rl={"rpm": 1000},
            model_quota={"rpm": 50}, est_tokens=1)[0]
    ok, scope, lt, _ = rl.check_multi_scope(
        client_id="cZ", workspace_id="wZ", user_id=None, alias="aZ",
        client_rl={"rpm": 2}, workspace_rl={"rpm": 1000},
        model_quota={"rpm": 50}, est_tokens=1)
    assert not ok and scope == "client" and lt == "rpm"

    # ── Model scope wins last (client + workspace generous; per-model RPM=2) ──
    rl = RateLimiter()
    for _ in range(2):
        assert rl.check_multi_scope(
            client_id="cM", workspace_id="wM", user_id=None, alias="aM",
            client_rl={"rpm": 1000}, workspace_rl={"rpm": 1000},
            model_quota={"rpm": 2}, est_tokens=1)[0]
    ok, scope, lt, _ = rl.check_multi_scope(
        client_id="cM", workspace_id="wM", user_id=None, alias="aM",
        client_rl={"rpm": 1000}, workspace_rl={"rpm": 1000},
        model_quota={"rpm": 2}, est_tokens=1)
    assert not ok and scope == "model" and lt == "rpm"


def test_rate_limit_user_scope_tpm_violation_returns_full_headers():
    """The full OpenAI 429 header set is returned with the breached dimension's
    Remaining=0 and Retry-After present."""
    from gateway.core.rate_limit import RateLimiter, rate_limit_headers
    rl = RateLimiter()
    user_quota = {"tpm": 10}
    # consume the bucket
    rl.check_multi_scope(
        client_id=None, workspace_id="w", user_id="u", alias="a",
        client_rl=None, workspace_rl={"user": user_quota},
        model_quota=None, est_tokens=10)
    ok, scope, lt, ra = rl.check_multi_scope(
        client_id=None, workspace_id="w", user_id="u", alias="a",
        client_rl=None, workspace_rl={"user": user_quota},
        model_quota=None, est_tokens=5)
    assert not ok and scope == "user" and lt == "tpm"
    h = rate_limit_headers(user_quota, lt, ra)
    assert h["X-RateLimit-Limit-Tokens"] == "10"
    assert h["X-RateLimit-Remaining-Tokens"] == "0"
    assert "Retry-After" in h


# ───────────────────────── chunker (item 27) ─────────────────────────
def test_chunker_drops_oldest_keeps_system():
    from gateway.core.chunker import apply_truncation
    msgs = [{"role": "system", "content": "sys"}] + \
           [{"role": "user", "content": f"message number {i} " * 20} for i in range(6)]
    final, info = apply_truncation(msgs, context_window=1100)
    assert info["truncated"] is True
    assert final[0]["role"] == "system"          # system always preserved
    assert info["dropped_messages"] >= 1
    assert len(final) < len(msgs)


def test_chunker_no_truncation_within_budget():
    from gateway.core.chunker import apply_truncation
    final, info = apply_truncation([{"role": "user", "content": "hi"}], context_window=200000)
    assert info["truncated"] is False and len(final) == 1


# ──────────────── observer bus overflow (item 32) ────────────────
def test_bus_overflow_drops_oldest_and_counts():
    from gateway.core import metrics as M
    from gateway.governance.bus import _ObserverWorker
    before = M.GOV_DROPPED._value.get()
    w = _ObserverWorker(observer=SimpleNamespace(), maxsize=2)
    for i in range(5):
        w.submit(f"e{i}")
    assert w.dropped == 3
    assert M.GOV_DROPPED._value.get() == before + 3


# ──────────────── guardrails depth (items 18-21) ────────────────
def test_regex_pii_redact_email_and_phone():
    from gateway.core.guardrails.detectors import RegexPIIDetector
    red, findings = RegexPIIDetector().redact("mail a@b.com call 415-555-1234")
    assert "[REDACTED:email]" in red and "[REDACTED:us_phone]" in red
    cats = {f.category for f in findings}
    assert "email" in cats and "us_phone" in cats


def test_secrets_detector_multiple_categories():
    from gateway.core.guardrails.detectors import SecretsDetector
    cats = {f.category for f in SecretsDetector().scan(
        "AKIAIOSFODNN7EXAMPLE sk-ant-abcdefghijklmnopqrstuvwx "
        "-----BEGIN RSA PRIVATE KEY-----")}
    assert {"aws_access_key", "anthropic_key", "private_key"} <= cats


def test_keyword_detector_case_insensitive():
    from gateway.core.guardrails.detectors import KeywordDetector
    assert KeywordDetector(["ProjectPhoenix"]).scan("about projectphoenix today")


def test_cel_apply_to_output_skips_input():
    from gateway.core.guardrails.engine import GuardrailEngine
    gconf = {"pii_detection": True,
             "rules": [{"name": "out-only", "cel": "true", "apply_to": "output",
                        "detectors": ["regex_pii"], "action": "block"}]}
    out = GuardrailEngine().run_input(
        {"messages": [{"role": "user", "content": "ssn 123-45-6789"}]}, gconf)
    assert out.blocked is False           # output-only rule must not fire on input


def test_guardrail_mode_override_block_to_audit():
    from gateway.core.guardrails.engine import GuardrailEngine
    out = GuardrailEngine().run_input(
        {"messages": [{"role": "user", "content": "ssn 123-45-6789"}]},
        {"pii_detection": True, "mode": "block"}, mode_override="audit")
    assert out.action == "audit" and out.blocked is False and out.findings


# ──────────────── error mapping (items 8) ────────────────
def test_map_exception_timeout_and_connect():
    class TimeoutErr(Exception):
        pass
    class ConnectErr(Exception):
        pass
    code, _ = errors.map_exception(TimeoutErr("x"))
    assert code == 504
    code2, _ = errors.map_exception(ConnectErr("x"))
    assert code2 == 502


def test_openai_error_body_shape():
    b = errors.openai_error_body("nope", "invalid_request_error")
    assert b["error"]["type"] == "invalid_request_error" and "message" in b["error"]


# ──────────────── rate limit (items 29, 30) ────────────────
def test_rate_limiter_tpm_blocks():
    from gateway.core.rate_limit import RateLimiter
    rl = RateLimiter()
    q = {"rpm": 1000, "tpm": 100}
    allowed, ltype, _ = rl.check("w", "m", q, 200)   # 200 > 100 tpm
    assert allowed is False and ltype == "tpm"


def test_using_redis_reflects_config():
    from gateway.core import redis_rate_limit as rr
    assert rr.using_redis() == bool(rr.settings.redis_url)


# ──────────────── registry (items 5, 6) ────────────────
def test_registry_unknown_alias_raises_404():
    from fastapi import HTTPException
    from gateway.core.auth import WorkspaceContext
    from gateway.core.registry import resolve_chat_targets
    ws = WorkspaceContext("ws-x", "x", {}, {}, None, {}, {}, {})
    with pytest.raises(HTTPException) as ei:
        resolve_chat_targets(ws, {"model": "nope"}, {})
    assert ei.value.status_code == 404


def test_registry_embedding_resolve():
    from gateway.core.auth import WorkspaceContext
    from gateway.core.registry import resolve_embedding_target
    ws = WorkspaceContext("ws-x", "x", {}, {"emb": [{"provider": "bedrock", "model_id": "titan"}]},
                          None, {}, {}, {})
    t = resolve_embedding_target(ws, "emb")
    assert t.provider == "bedrock" and t.model_id == "titan"


# ──────────────── parity matrix (enrichment 60) ────────────────
def test_parity_matrix_meets_goal():
    from gateway.core.parity import matrix
    m = matrix()
    assert m["total_capabilities"] >= 18
    assert m["coverage"] >= 0.80 and m["meets_goal"] is True
    assert all({"capability", "embedded_sdk", "generic_gateway", "ours", "status"} <= set(r) for r in m["rows"])
    assert m["we_lead"]  # generic comparison, no named external systems


# ──────────────── RBAC require_admin (item 40) ────────────────
def test_require_admin_allows_platform_token():
    from gateway.core.security import require_admin
    req = SimpleNamespace(headers={"x-admin-token": settings.platform_admin_token}, cookies={})
    out = asyncio.run(require_admin(req))
    assert "admin" in out["roles"]


def test_require_admin_denies_without_credentials():
    from fastapi import HTTPException
    from gateway.core.security import require_admin
    req = SimpleNamespace(headers={}, cookies={})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(require_admin(req))
    assert ei.value.status_code == 403


# ──────────────── kafka envelope kinds (items 33, 34) ────────────────
def test_kafka_envelope_error_and_guardrail():
    from gateway.governance.kafka_observer import _envelope
    from gateway.governance.observer import GuardrailDecisionEvent, RequestErrorEvent
    g = _envelope(GuardrailDecisionEvent(request_id="r", workspace_id="ws", model_alias="m",
                                         rule="default", detector="regex_pii", action="block",
                                         stage="input", excerpt="ssn:***"))
    assert g["event_kind"] == "guardrail_block" and g["payload"]["action"] == "block"
    e = _envelope(RequestErrorEvent(request_id="r", workspace_id="ws", user_id=None, model_alias="m",
                                    provider="bedrock", engine="bifrost", error_type="upstream_error",
                                    http_status=502, message="boom", latency_ms=1.0))
    assert e["event_kind"] == "error" and e["payload"]["status"] == "error"


# ──────────────── token counting (item 16) ────────────────
def test_count_message_tokens_positive():
    from gateway.core.tokens import count_message_tokens
    assert count_message_tokens([{"role": "user", "content": "hello world"}]) > 0


# ──────────────── TRACK 7 enrichments ────────────────
def test_cache_key_stable_and_distinct():
    from gateway.core.cache import cache_key
    b1 = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
    b2 = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
    b3 = {"messages": [{"role": "user", "content": "bye"}], "max_tokens": 5}
    assert cache_key("ws", "m", b1) == cache_key("ws", "m", b2)      # deterministic
    assert cache_key("ws", "m", b1) != cache_key("ws", "m", b3)      # content-sensitive
    assert cache_key("ws2", "m", b1) != cache_key("ws", "m", b1)     # workspace-scoped


def test_new_enrichment_metrics_present():
    from gateway.core import metrics as M
    M.CACHE_HITS.labels(client="-", workspace="ws", component="-").inc()
    M.BUDGET_ALERTS.labels(client="-", workspace="ws", scope="workspace", threshold="80").inc()
    M.KAFKA_DLQ.inc()
    body = M.render()[0].decode()
    assert "gateway_cache_hits_total" in body
    assert "gateway_budget_alerts_total" in body
    assert "gateway_kafka_dlq_total" in body


def test_run_output_audit_scan():
    from gateway.core.guardrails.engine import GuardrailEngine
    findings = GuardrailEngine().run_output("leaked ssn 123-45-6789", {"pii_detection": True})
    assert any(f.category == "ssn" for f in findings)


# ──────────────── WAVE 12 - JWT + component identity ────────────────
def test_looks_like_jwt_vs_api_key():
    from gateway.core.security import looks_like_jwt
    from scripts.mint_demo_jwt import mint
    jwt = mint("ws-x", "alice", "document-processing", ["member"], 3600)
    assert looks_like_jwt(jwt) is True
    assert looks_like_jwt("gw-key-secondary-001") is False
    assert looks_like_jwt("a.b.c") is False        # not a decodable JWT header


def test_decode_bearer_jwt_dev_trust():
    from gateway.core.security import decode_bearer_jwt
    from scripts.mint_demo_jwt import mint
    claims = decode_bearer_jwt(mint("ws-novatech", "bob", "code-generation", ["admin"], 3600))
    assert claims["workspace_id"] == "ws-novatech"
    assert claims["sub"] == "bob"
    assert claims["component"] == "code-generation"
    assert "admin" in claims["roles"]


def test_expired_jwt_rejected():
    from gateway.core.security import decode_bearer_jwt
    from scripts.mint_demo_jwt import mint
    assert decode_bearer_jwt(mint("ws-x", "u", None, ["member"], -10)) is None


# ──────────────── WAVE 13 - guardrail Rules+Profiles store ────────────────
def test_guardrail_store_test_rule_real_eval():
    from gateway.core.guardrails import store
    res = asyncio.run(store.test_rule("my ssn is 123-45-6789", "true",
                                      [{"detector_type": "regex", "config": {}}], "block"))
    assert res["cel_matched"] is True and res["violation"] is True
    assert res["action"] == "block"
    assert any(f["category"] == "ssn" for f in res["findings"])


def test_guardrail_store_inline_block(monkeypatch):
    from gateway.core.guardrails import store
    from gateway.core.auth import WorkspaceContext
    ctx = WorkspaceContext("ws", "x", {}, {}, None,
                           {"pii_detection": True, "mode": "block"}, {}, {})

    async def _no_db(*a, **k):
        return []
    monkeypatch.setattr(store, "_db_rules", _no_db)
    monkeypatch.setattr(store, "_selected_rules", _no_db)
    out = asyncio.run(store.evaluate_input(
        {"messages": [{"role": "user", "content": "ssn 123-45-6789"}]}, ctx))
    assert out.blocked is True and out.action == "block"


# ── guardrail enforcement: rule action authority + scope (cross-workspace isolation) ──
def test_guardrail_resolve_action_explicit_rule_wins():
    """An admin-chosen rule action (DB rule from the Rule Builder) is authoritative.
    The workspace's enforcement mode no longer downgrades it - that contradicted
    the rule's own configuration ("rule says BLOCK but workspace is audit -> audit
    won") and was exactly what an admin reported. The workspace mode is now only
    the default for INLINE rules (built-in detector flags)."""
    from gateway.core.guardrails.store import _resolve_action
    # explicit DB rule: its action wins regardless of workspace mode
    assert _resolve_action("block", explicit=True, workspace_mode="audit", request_override=None) == "block"
    assert _resolve_action("redact", explicit=True, workspace_mode="audit", request_override=None) == "redact"
    assert _resolve_action("block", explicit=True, workspace_mode="redact", request_override=None) == "block"
    # inline rule (no admin-chosen action): workspace mode IS the default
    assert _resolve_action("block", explicit=False, workspace_mode="audit", request_override=None) == "audit"
    assert _resolve_action("block", explicit=False, workspace_mode="redact", request_override=None) == "redact"
    # no workspace mode -> inline rule keeps its own default
    assert _resolve_action("block", explicit=False, workspace_mode=None, request_override=None) == "block"
    # per-request override (X-Gateway-Guardrail-Mode) is the operator-level
    # ceiling - it wins even over an explicit rule action
    assert _resolve_action("block", explicit=True, workspace_mode="block", request_override="audit") == "audit"
    assert _resolve_action("block", explicit=False, workspace_mode="block", request_override="audit") == "audit"


def test_guardrail_rule_applies_scope():
    from gateway.core.guardrails.store import _rule_applies
    # global → every workspace
    assert _rule_applies("global", None, None, "ws-a", None) is True
    assert _rule_applies("global", None, None, "ws-b", "comp") is True
    # workspace-scoped → only its own workspace (THE cross-workspace leak guard)
    assert _rule_applies("workspace", "ws-a", None, "ws-a", None) is True
    assert _rule_applies("workspace", "ws-a", None, "ws-b", None) is False
    # component-scoped → workspace AND component must match
    assert _rule_applies("component", "ws-a", "c1", "ws-a", "c1") is True
    assert _rule_applies("component", "ws-a", "c1", "ws-a", "c2") is False
    assert _rule_applies("component", "ws-a", "c1", "ws-b", "c1") is False


def test_guardrail_inline_rule_uses_workspace_mode_as_default(monkeypatch):
    """For INLINE rules (the built-in detector flags like `pii_detection`) the
    workspace's enforcement mode is the default. An audit-only workspace logs
    PII matches without blocking. (Distinguishes from explicit DB rules built in
    the Rule Builder, whose action wins.)"""
    from gateway.core.guardrails import store
    from gateway.core.auth import WorkspaceContext
    ctx = WorkspaceContext("ws", "x", {}, {}, None,
                           {"pii_detection": True, "mode": "audit"}, {}, {})

    async def _no_db(*a, **k):
        return []
    monkeypatch.setattr(store, "_db_rules", _no_db)
    monkeypatch.setattr(store, "_selected_rules", _no_db)
    out = asyncio.run(store.evaluate_input(
        {"messages": [{"role": "user", "content": "ssn 123-45-6789"}]}, ctx))
    assert out.findings, "violation should still be detected + logged"
    assert out.blocked is False and out.action == "audit"


def test_guardrail_explicit_db_rule_action_overrides_workspace_audit(monkeypatch):
    """An explicit DB rule with action=block must STILL block even when the
    workspace is in audit mode. This is the bug the admin reported (rule said
    BLOCK, workspace said audit, audit wrongly won)."""
    from gateway.core.guardrails import store
    from gateway.core.auth import WorkspaceContext
    from gateway.core.guardrails.detectors import Finding

    class _AlwaysHits:
        def scan(self, text):
            return [Finding(detector="x", category="pii", excerpt=text[:8])]
        def redact(self, text):
            return text, [Finding(detector="x", category="pii", excerpt=text[:8])]

    ctx = WorkspaceContext("ws", "x", {}, {}, None, {"mode": "audit"}, {}, {})
    explicit = store.CompiledRule(name="explicit-block", action="block", apply_to="input",
                                   cel="true", sampling_rate=1.0, detectors=[_AlwaysHits()],
                                   explicit_action=True)

    async def _db(*a, **k):
        return [explicit]
    async def _empty(*a, **k):
        return []
    monkeypatch.setattr(store, "_db_rules", _db)
    monkeypatch.setattr(store, "_selected_rules", _empty)
    out = asyncio.run(store.evaluate_input(
        {"messages": [{"role": "user", "content": "anything"}]}, ctx))
    assert out.action == "block" and out.blocked is True, \
        "explicit BLOCK rule must block even when workspace mode is audit"


def test_build_detector_external_scaffold_not_configured():
    from gateway.core.guardrails.profiles import build_detector, NotConfigured
    import pytest as _pt
    with _pt.raises(NotConfigured):
        build_detector("azure", {})          # no creds → not configured
    with _pt.raises(NotConfigured):
        build_detector("bedrock", {})         # no guardrail_id → not configured


# ──────────────── TRACK 2 partials ────────────────
def test_rate_limit_headers_full_openai_shape():
    from gateway.core.rate_limit import rate_limit_headers
    h = rate_limit_headers({"rpm": 100, "tpm": 5000}, limit_type="rpm", retry_after=12)
    assert h["X-RateLimit-Limit-Requests"] == "100"
    assert h["X-RateLimit-Remaining-Requests"] == "0"        # breached dim
    assert h["X-RateLimit-Remaining-Tokens"] == "5000"       # other dim at limit
    assert h["X-RateLimit-Reset-Requests"].endswith("s")
    assert h["Retry-After"] == "12"


def test_cel_sampling_rate_zero_skips_rule():
    from gateway.core.guardrails.engine import GuardrailEngine
    gconf = {"pii_detection": True,
             "rules": [{"name": "sampled", "cel": "true", "apply_to": "input",
                        "sampling_rate": 0.0, "detectors": ["regex_pii"], "action": "block"}]}
    out = GuardrailEngine().run_input(
        {"messages": [{"role": "user", "content": "ssn 123-45-6789"}]}, gconf)
    assert out.blocked is False and not out.findings   # sampling_rate=0 → never evaluated


def test_resolved_target_hydrate_from_config():
    from gateway.core.registry import ResolvedTarget
    t = ResolvedTarget(provider="bedrock", model_id="us.x",
                       config={"region": "us-west-2", "base_url": "http://x", "api_version": "2024"})
    t.hydrate_from_config()
    assert t.region == "us-west-2" and t.base_url == "http://x" and t.api_version == "2024"


def test_bus_drain_empty_returns_zero():
    from gateway.governance.bus import _ObserverWorker
    w = _ObserverWorker(observer=SimpleNamespace(), maxsize=4)
    assert asyncio.run(w.drain(timeout=0.1)) == 0


def test_fallback_timeout_maps_to_504(monkeypatch):
    import gateway.core.fallback as fb_mod
    from gateway.core.registry import ResolvedTarget

    async def _no_cred(ws, provider):
        return None
    monkeypatch.setattr(fb_mod, "get_provider_credential", _no_cred)

    class _SlowEngine:
        async def chat(self, body, target):
            await asyncio.sleep(1.0)
            from gateway.engines.base import EngineResult
            return EngineResult({"choices": []})

    targets = [ResolvedTarget(provider="bedrock", model_id="us.x")]
    res = asyncio.run(fb_mod.execute(_SlowEngine(), {"messages": []}, targets, "ws",
                                     max_retries=0, timeout=0.05))
    assert res.result.status_code == 504
    assert (res.result.body.get("error") or {}).get("type") == "timeout"
