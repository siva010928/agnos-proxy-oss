"""POST /v1/embeddings - provider-agnostic via the backend engine.

INVARIANT: an embedding request is governed EXACTLY like a chat request - no
request may skip rate-limits, budgets, or guardrails. The full pipeline
(auth → required-headers → rate-limit → budget → guardrails → engine → governance
event) runs here too, so governance can never be bypassed by choosing a route.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gateway.core import errors
from gateway.core.auth import resolve_principal
from gateway.core.budgets import add_live_spend, check_budget
from gateway.core.cost import compute_cost
from gateway.core.credentials import get_provider_credential
from gateway.core.rate_limit import enforce_multi_scope, rate_limit_headers
from gateway.core.registry import resolve_embedding_target
from gateway.core.tokens import estimate_tokens
from gateway.governance.observer import (
    GuardrailDecisionEvent, RateLimitedEvent, RequestErrorEvent, RequestSuccessEvent,
)
from gateway.runtime import bus, engine, select_engine

router = APIRouter()


def _embed_texts(body: dict) -> list[str]:
    inp = body.get("input")
    if isinstance(inp, str):
        return [inp]
    return [str(x) for x in (inp or [])]


@router.post("/v1/embeddings")
async def embeddings(request: Request):
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:16]}"
    hdrs = {"X-Gateway-Correlation-Id": request_id}
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=400, headers=hdrs, content={"error": {
            "type": "invalid_request_error", "code": "malformed_json",
            "message": f"Request body is not valid JSON: {exc}"}})
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, headers=hdrs, content={"error": {
            "type": "invalid_request_error", "code": "invalid_body",
            "message": "Request body must be a JSON object."}})

    # OTel: a parent span for the whole embeddings request, marked ERROR (with
    # structured attributes) on every failure path - so a failed embedding is
    # visible in the trace exactly like a failed chat, not an invisible success.
    started_ns = time.time_ns()
    try:
        from opentelemetry import trace as _otel
        from gateway.core.tracing import tracer
        _span = tracer().start_span("gateway.embeddings", start_time=started_ns)
        _span.set_attribute("correlation_id", request_id)
        _span.set_attribute("agnos.call_kind", "embedding")
        _parent_ctx = _otel.set_span_in_context(_span)
    except Exception:  # noqa: BLE001
        _span, _parent_ctx = None, None

    def _mark_error(sp, error: dict) -> None:
        from opentelemetry.trace import Status, StatusCode
        sp.set_status(Status(StatusCode.ERROR, str(error.get("message", ""))[:300]))
        sp.set_attribute("error", True)
        sp.set_attribute("otel.status_code", "ERROR")
        if error.get("http_status") is not None:
            sp.set_attribute("http.status_code", int(error["http_status"]))
        if error.get("provider"):
            sp.set_attribute("agnos.provider", error["provider"])
        if error.get("type"):
            sp.set_attribute("agnos.error_type", error["type"])
        if error.get("code"):
            sp.set_attribute("agnos.error_code", error["code"])
        sp.add_event("exception", {
            "exception.type": str(error.get("type") or "error"),
            "exception.message": str(error.get("message", ""))[:500],
            "http.status_code": int(error.get("http_status") or 0),
        })

    def _finish_span(ok: bool, *, error: dict | None = None) -> None:
        if _span is None:
            return
        try:
            _span.set_attribute("agnos.ok", ok)
            if not ok and error:
                _mark_error(_span, error)
        except Exception:  # noqa: BLE001
            pass
        _span.end()

    def _engine_error_child(error: dict, t0_ns: int) -> None:
        """A failed ENGINE child span so the trace exposes the real failure point."""
        if _span is None:
            return
        try:
            from gateway.core.tracing import tracer
            cs = tracer().start_span("engine", context=_parent_ctx, start_time=t0_ns)
            cs.set_attribute("correlation_id", request_id)
            cs.set_attribute("agnos.call_kind", "embedding")
            _mark_error(cs, error)
            cs.end()
        except Exception:  # noqa: BLE001
            pass

    ws = await resolve_principal(request.headers.get("authorization"), request.headers)
    user_id = body.get("user") or request.headers.get("x-gateway-user-id") or ws.user_id
    use_case = request.headers.get("x-gateway-use-case")
    component = ws.component

    # ── Required-header enforcement ──
    from gateway.core.required_headers import (
        missing_required_headers, required_headers_error, required_headers_for,
    )
    required = await required_headers_for(ws.client_id)
    if required:
        miss = missing_required_headers(dict(request.headers), required)
        if miss:
            _finish_span(False, error={"type": "invalid_request_error", "code": "missing_required_header",
                                       "http_status": 400, "message": f"missing required headers: {miss}"})
            return JSONResponse(status_code=400, headers=hdrs, content=required_headers_error(miss))

    alias = body.get("model")
    target = resolve_embedding_target(ws, alias)
    texts = _embed_texts(body)
    est_tokens = sum(estimate_tokens(t) for t in texts) or 1

    # ── Rate limit (same multi-scope enforcement as chat) ──
    quota = (ws.quotas or {}).get(alias, {})
    client_rl = None
    if ws.client_id:
        from gateway.db.database import async_session as _ses
        from gateway.db.models import Client as _Client
        async with _ses() as _s:
            _c = await _s.get(_Client, ws.client_id)
            client_rl = (_c.rate_limits if _c else None) or None
    allowed, scope, limit_type, retry_after = await enforce_multi_scope(
        client_id=ws.client_id, workspace_id=ws.workspace_id, user_id=user_id,
        alias=alias, client_rl=client_rl, workspace_rl=(ws.rate_limits or None),
        model_quota=quota, est_tokens=est_tokens)
    if not allowed:
        scope_quota = (quota if scope == "model" else (ws.rate_limits or {}) if scope == "workspace"
                       else (client_rl or {}) if scope == "client"
                       else ((ws.rate_limits or {}).get("user") or {}) if scope == "user" else {})
        bus().emit(RateLimitedEvent(
            request_id=request_id, workspace_id=ws.workspace_id, model_alias=alias,
            limit_type=limit_type, scope=scope, client_id=ws.client_id, user_id=user_id,
            use_case=use_case))
        headers = rate_limit_headers(scope_quota, limit_type, retry_after) | hdrs | {
            "X-Gateway-RateLimit-Scope": scope}
        _finish_span(False, error={"type": "rate_limit_exceeded", "http_status": 429,
                                   "message": f"Rate limit exceeded ({scope}/{limit_type})."})
        return errors.openai_error_response(
            429, f"Rate limit exceeded ({scope}/{limit_type}).", "rate_limit_exceeded", headers=headers)

    # ── Budget caps → 402 ──
    decision = await check_budget(ws.client_id, ws.workspace_id, user_id,
                                  ws.budgets or {}, model_id=target.model_id)
    if not decision.allowed:
        bus().emit(RateLimitedEvent(
            request_id=request_id, workspace_id=ws.workspace_id, model_alias=alias,
            limit_type="budget", scope=decision.scope or "workspace",
            client_id=ws.client_id, user_id=user_id, use_case=use_case,
            budget_usd=getattr(decision, "cap", None), spent_usd=getattr(decision, "spend", None)))
        _finish_span(False, error={"type": "budget_exceeded", "http_status": 402, "message": decision.message})
        return errors.openai_error_response(402, decision.message, "budget_exceeded", headers=hdrs)

    # ── Guardrails (input) - scan the embedding inputs exactly like chat prompts ──
    from gateway.core.guardrails import store as _gstore
    mode_override = request.headers.get("x-gateway-guardrail-mode")
    ids_hdr = request.headers.get("x-gateway-guardrail-ids")
    selected_ids = [int(x) for x in ids_hdr.split(",") if x.strip().isdigit()] if ids_hdr else []
    gbody = {"messages": [{"role": "user", "content": t} for t in texts]}
    outcome = await _gstore.evaluate_input(gbody, ws, selected_ids, mode_override, headers=dict(request.headers))
    if outcome.findings:
        for f in outcome.findings:
            bus().emit(GuardrailDecisionEvent(
                request_id=request_id, workspace_id=ws.workspace_id, model_alias=alias,
                rule=outcome.rule, detector=f.detector, action=outcome.action, stage="input",
                excerpt=f"{f.category}:{f.excerpt}", client_id=ws.client_id, component=component,
                sub_category=f.category, confidence=getattr(f, "confidence", 1.0)))
        if outcome.blocked:
            custom_msg = next((f.message for f in outcome.findings if getattr(f, "message", None)), None)
            block_message = custom_msg or (f"Request blocked by gateway guardrail '{outcome.rule}' "
                                           f"({outcome.findings[0].category}).")
            bus().emit(RequestErrorEvent(
                request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
                model_alias=alias, provider=target.provider, engine=engine().name,
                error_type="guardrail_violation", http_status=422, message=block_message,
                latency_ms=(time.perf_counter() - started) * 1000,
                client_id=ws.client_id, component=component,
                error_detail={"category": "guardrail", "rule": outcome.rule, "action": outcome.action,
                              "stage": "input", "call_kind": "embedding",
                              "matches": [{"detector": f.detector, "sub_category": f.category,
                                           "excerpt": f.excerpt} for f in outcome.findings]},
                use_case=use_case, input_tokens=est_tokens, provider_model_id=target.model_id,
                call_kind="embedding"))
            _finish_span(False, error={"type": "guardrail_violation", "http_status": 422,
                                       "provider": target.provider, "message": block_message})
            return errors.openai_error_response(422, block_message, "guardrail_violation", headers=hdrs)
        if outcome.action == "redact" and outcome.redacted_messages is not None:
            body["input"] = [m.get("content", "") for m in outcome.redacted_messages]

    # ── Credentials + engine (honor per-provider insourcing override) ──
    cred = await get_provider_credential(ws.workspace_id, target.provider)
    if cred:
        target.credentials = cred.credentials
        target.config = cred.config
        target.bifrost_key_name = cred.bifrost_key_name
    # Per-provider engine selection (rented / owned / weighted canary split) -
    # identical to chat, so embeddings honor the same rented→owned migration.
    from gateway.core import engine_routing
    eng = select_engine(engine_routing.get_overrides(), target.provider)

    _eng_t0 = time.time_ns()
    try:
        result = await eng.embeddings(body, target)
    except Exception as exc:  # noqa: BLE001
        code, err = errors.map_exception(exc)
        _eng_err = {"type": err["error"]["type"], "code": err["error"].get("code"),
                    "http_status": code, "provider": target.provider, "message": err["error"]["message"]}
        bus().emit(RequestErrorEvent(
            request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
            model_alias=alias, provider=target.provider, engine=eng.name,
            error_type=err["error"]["type"], http_status=code, message=err["error"]["message"],
            latency_ms=(time.perf_counter() - started) * 1000, client_id=ws.client_id,
            component=component, use_case=use_case, input_tokens=est_tokens,
            provider_model_id=target.model_id, call_kind="embedding",
            error_detail={"category": "provider_error", "provider": target.provider,
                          "http_status": code, "code": err["error"].get("code"),
                          "message": err["error"]["message"], "call_kind": "embedding"}))
        _engine_error_child(_eng_err, _eng_t0)
        _finish_span(False, error=_eng_err)
        return JSONResponse(status_code=code, content=err, headers=hdrs)

    if not result.ok:
        code, err = errors.map_bifrost_error(result.status_code, result.body)
        _eng_err = {"type": err["error"]["type"], "code": err["error"].get("code"),
                    "http_status": code, "provider": target.provider, "message": err["error"]["message"]}
        bus().emit(RequestErrorEvent(
            request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
            model_alias=alias, provider=target.provider, engine=eng.name,
            error_type=err["error"]["type"], http_status=code, message=err["error"]["message"],
            latency_ms=(time.perf_counter() - started) * 1000, client_id=ws.client_id,
            component=component, use_case=use_case, input_tokens=est_tokens,
            provider_model_id=target.model_id, call_kind="embedding",
            error_detail={"category": "provider_error", "provider": target.provider,
                          "http_status": code, "code": err["error"].get("code"),
                          "message": err["error"]["message"], "call_kind": "embedding"}))
        _engine_error_child(_eng_err, _eng_t0)
        _finish_span(False, error=_eng_err)
        return JSONResponse(status_code=code, content=err, headers=hdrs)

    usage = result.body.get("usage") or {}
    in_tok = usage.get("prompt_tokens", 0) or 0
    cost = compute_cost(target.model_id, in_tok, 0)
    bus().emit(RequestSuccessEvent(
        request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id, use_case=use_case,
        model_alias=alias, provider=target.provider, provider_model_id=target.model_id,
        engine=eng.name, input_tokens=in_tok, output_tokens=0, cost_usd=cost,
        latency_ms=(time.perf_counter() - started) * 1000, stream=False, call_kind="embedding",
        client_id=ws.client_id, component=component, key_id=ws.key_id))
    add_live_spend(ws.client_id, ws.workspace_id, user_id, cost, target.model_id)
    _span_attrs = {"agnos.input_tokens": in_tok, "agnos.provider": target.provider,
                   "agnos.engine": eng.name}
    if _span is not None:
        try:
            for _k, _v in _span_attrs.items():
                _span.set_attribute(_k, _v)
        except Exception:  # noqa: BLE001
            pass
    _finish_span(True)
    return JSONResponse(content=result.body, headers=hdrs)
