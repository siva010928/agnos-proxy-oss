"""POST /v1/chat/completions - the core endpoint."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gateway.core import engine_routing, errors
from gateway.config import settings
from gateway.core import cache as _cache
from gateway.core.auth import resolve_principal
from gateway.core.budgets import check_budget
from gateway.core.chunker import apply_truncation
from gateway.core.cost import compute_cost
from gateway.core.credentials import get_provider_credential
from gateway.core.fallback import execute as fallback_execute
from gateway.core.guardrails.engine import engine as guardrail_engine
from gateway.core.rate_limit import enforce_multi_scope, rate_limit_headers
from gateway.core.registry import ResolvedTarget, resolve_chat_targets
from gateway.core.tokens import count_message_tokens
from gateway.governance.observer import (
    CacheHitEvent,
    FallbackEvent,
    GuardrailDecisionEvent,
    RateLimitedEvent,
    RequestErrorEvent,
    RequestStartEvent,
    RequestSuccessEvent,
)
from gateway.runtime import bus, engine, select_engine

router = APIRouter()


def _attribution(body: dict, request: Request) -> tuple[str | None, str | None]:
    # Accept both canonical header (X-Gateway-User) and legacy (X-Gateway-User-Id).
    # The canonical name matches the docs, code snippets, and the playground.
    user_id = (body.get("user")
               or request.headers.get("x-gateway-user")
               or request.headers.get("x-gateway-user-id"))
    meta = body.get("metadata") or {}
    use_case = meta.get("use_case") if isinstance(meta, dict) else None
    use_case = use_case or request.headers.get("x-gateway-use-case")
    return user_id, use_case


async def _attach_credentials(workspace_id: str, target: ResolvedTarget) -> None:
    cred = await get_provider_credential(workspace_id, target.provider)
    if cred:
        target.credentials = cred.credentials
        target.config = cred.config
        target.bifrost_key_name = cred.bifrost_key_name


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat-completions handler.

    Governance flow order (WAVE 19 TRACK C5 \u2014 owned, not proxied):

        1. Auth                 \u2014 resolve_principal() \u2014 workspace key OR JWT
                                  \u2192 WorkspaceContext (client_id + workspace_id +
                                  user_id from JWT sub + component from header
                                  + roles).
        2. Required headers     \u2014 Client.required_headers (e.g. X-Gateway-Component)
                                  must all be present \u2192 400 missing_required_header.
        3. Routing              \u2014 resolve_chat_targets() picks the alias\u2192target
                                  chain from the workspace's chat_models.
        4. Model eligibility    \u2014 ModelCatalog says enabled? Disabled \u2192 403
                                  model_disabled.
        5. Rate limit           \u2014 multi-scope: User \u2192 Workspace \u2192 Client \u2192 Model;
                                  first violation wins \u2192 429 + full OpenAI headers.
        6. Budget               \u2014 hierarchical: Client \u2192 Workspace \u2192 User
                                  (+ per-model substring); first violation wins
                                  \u2192 402 budget_exceeded with the breached scope.
        7. Guardrails (input)   \u2014 CEL rules + detector profiles run in OUR layer
                                  \u2192 422 guardrail_violation OR redacted-but-passed.
        8. Cache (optional)     \u2014 X-Gateway-Cache-TTL / Idempotency-Key \u2192 hit \u2192 $0
                                  served + cache_hit event.
        9. Provider invocation  \u2014 BackendEngine (bifrost / direct / echo) handles
                                  the upstream call. Anti-corruption boundary
                                  strips engine-isms before the body returns.
       10. Fallback chain       \u2014 our policy: ordered targets + retries + breaker;
                                  emits FallbackEvent on each hop.
       11. Cost compute         \u2014 LiteLLM-synced rates + admin overrides.
       12. Governance event     \u2014 RequestSuccessEvent / RequestErrorEvent on the
                                  bus \u2192 fan-out to Postgres / Prometheus / SSE /
                                  Kafka + add_live_spend() so the next request's
                                  budget check sees the new spend immediately.

    Every step is OURS \u2014 the engine boundary is purely the OpenAI HTTP wire
    plus the encapsulated managed-key side-channel header set by BifrostEngine.
    An engine swap (POST /admin/engine) does not change the governance behaviour.
    """
    started = time.perf_counter()
    started_ns = time.time_ns()
    request_id = request.headers.get("x-request-id") or f"req-{uuid.uuid4().hex[:16]}"
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001 \u2014 malformed body
        return JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request_error",
                                "code": "malformed_json",
                                "message": f"Request body is not valid JSON: {exc}"}},
            headers={"X-Gateway-Correlation-Id": request_id},
        )
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": {"type": "invalid_request_error",
                                "code": "invalid_body",
                                "message": "Request body must be a JSON object."}},
            headers={"X-Gateway-Correlation-Id": request_id},
        )

    # OTel parent span + child-span helper (real stage durations, correlation_id on all)
    try:
        from opentelemetry import trace as _otel
        from gateway.core.tracing import tracer
        _span = tracer().start_span("gateway.chat")
        _span.set_attribute("correlation_id", request_id)
        _parent_ctx = _otel.set_span_in_context(_span)
    except Exception:  # noqa: BLE001
        _span, _parent_ctx, _otel = None, None, None

    def _stage_ns(pc: float) -> int:
        return started_ns + int((pc - started) * 1e9)

    def _child(name: str, pc0: float, pc1: float, *, error: dict | None = None, **attrs) -> None:
        if _span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode
            from gateway.core.tracing import tracer
            cs = tracer().start_span(name, context=_parent_ctx, start_time=_stage_ns(pc0))
            cs.set_attribute("correlation_id", request_id)
            for k, v in attrs.items():
                cs.set_attribute(k, v)
            if error:
                # Mark the stage span FAILED so the trace exposes the real failure
                # point (not a normal teal span). Structured attrs + an event carry
                # the http status / provider / error type / sanitized message.
                cs.set_status(Status(StatusCode.ERROR, str(error.get("message", ""))[:300]))
                cs.set_attribute("error", True)
                cs.set_attribute("otel.status_code", "ERROR")
                if error.get("http_status") is not None:
                    cs.set_attribute("http.status_code", int(error["http_status"]))
                if error.get("provider"):
                    cs.set_attribute("agnos.provider", error["provider"])
                if error.get("type"):
                    cs.set_attribute("agnos.error_type", error["type"])
                if error.get("code"):
                    cs.set_attribute("agnos.error_code", error["code"])
                cs.add_event("exception", {
                    "exception.type": str(error.get("type") or "error"),
                    "exception.message": str(error.get("message", ""))[:500],
                    "http.status_code": int(error.get("http_status") or 0),
                })
            cs.end(end_time=_stage_ns(pc1))
        except Exception:  # noqa: BLE001
            pass

    ws = await resolve_principal(request.headers.get("authorization"), request.headers)
    t_auth = time.perf_counter()
    user_id, use_case = _attribution(body, request)
    user_id = ws.user_id or user_id          # JWT sub wins over body/header
    component = ws.component

    # ── Required-header enforcement (WAVE 19 TRACK C3) ──
    # Per-Client required_headers list (e.g. X-Gateway-Component) MUST be present.
    from gateway.core.required_headers import (
        missing_required_headers, required_headers_error, required_headers_for,
    )
    required = await required_headers_for(ws.client_id)
    if required:
        miss = missing_required_headers(dict(request.headers), required)
        if miss:
            _finish_span(False) if False else None  # span ends in finally below
            if _span is not None:
                _span.set_attribute("agnos.ok", False)
                _span.set_attribute("agnos.missing_headers", ", ".join(miss))
                _span.end()
            return JSONResponse(status_code=400, content=required_headers_error(miss),
                                headers={"X-Gateway-Correlation-Id": request_id})

    # WAVE 26: capture routing failures with structured error_detail so the
    # admin can see "alias X attempted; no candidate found" without grepping logs.
    try:
        model_alias, targets = resolve_chat_targets(ws, body, request.headers)
    except HTTPException as exc:
        # 404 'Model not registered' or similar - emit a RequestErrorEvent so it
        # surfaces in /admin/request-logs with the alias and available aliases.
        msg = ""
        if isinstance(exc.detail, dict):
            err = exc.detail.get("error", {})
            msg = err.get("message", str(exc.detail))
        else:
            msg = str(exc.detail or "")
        requested_alias = body.get("model") if isinstance(body, dict) else None
        available_aliases = list((ws.chat_models or {}).keys()) if hasattr(ws, "chat_models") else []
        bus().emit(RequestErrorEvent(
            request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
            model_alias=str(requested_alias or ""), provider="-", engine=engine().name,
            error_type="routing_failed", http_status=exc.status_code,
            message=msg, latency_ms=(time.perf_counter() - started) * 1000,
            client_id=ws.client_id, component=component,
            error_detail={
                "category": "routing",
                "alias_requested": requested_alias,
                "available_aliases": available_aliases,
                "reason": msg,
                "result": "no_target_resolved",
            },
            use_case=use_case,
            input_tokens=count_message_tokens(body.get("messages", []) if isinstance(body, dict) else []),
        ))
        if _span is not None:
            _span.set_attribute("agnos.routing.result", "failed")
            _span.set_attribute("agnos.ok", False)
            _span.end()
        return JSONResponse(status_code=exc.status_code, content=exc.detail or {"error": {"message": msg}},
                            headers={"X-Gateway-Correlation-Id": request_id})
    t_route = time.perf_counter()
    target = targets[0]
    if _span is not None:
        _span.set_attribute("agnos.workspace_id", ws.workspace_id)
        if ws.client_id:
            _span.set_attribute("agnos.client_id", ws.client_id)
        _span.set_attribute("agnos.model_alias", model_alias)
        _span.set_attribute("agnos.provider", target.provider)
        if user_id:
            _span.set_attribute("agnos.user_id", user_id)
        if component:
            _span.set_attribute("agnos.component", component)
        if use_case:
            _span.set_attribute("agnos.use_case", use_case)
    _child("auth", started, t_auth, **{"agnos.workspace_id": ws.workspace_id})
    _child("routing", t_auth, t_route, **{"agnos.provider": target.provider,
                                          "agnos.model_alias": model_alias})

    def _finish_span(ok: bool, *, error: dict | None = None) -> None:
        if _span is not None:
            try:
                _span.set_attribute("agnos.ok", ok)
                if not ok:
                    from opentelemetry.trace import Status, StatusCode
                    msg = str((error or {}).get("message", "request failed"))[:300]
                    _span.set_status(Status(StatusCode.ERROR, msg))
                    _span.set_attribute("error", True)
                    _span.set_attribute("otel.status_code", "ERROR")
                    if error:
                        if error.get("http_status") is not None:
                            _span.set_attribute("http.status_code", int(error["http_status"]))
                        if error.get("type"):
                            _span.set_attribute("agnos.error_type", error["type"])
                        if error.get("category"):
                            _span.set_attribute("agnos.error_category", error["category"])
                        if error.get("provider"):
                            _span.set_attribute("agnos.provider", error["provider"])
                        _span.add_event("exception", {
                            "exception.type": str(error.get("type") or "error"),
                            "exception.message": msg,
                            "http.status_code": int(error.get("http_status") or 0),
                        })
            except Exception:  # noqa: BLE001
                pass
            _span.end()

    # ── Model-catalog eligibility (WAVE 19 TRACK C4) ──
    # Reject before any provider work if the resolved model is disabled in the
    # operator-curated catalog.  Uncatalogued ids are allowed (admin freedom).
    from gateway.core.model_catalog import is_eligible
    elig = await is_eligible(target.provider, target.model_id)
    if not elig.allowed:
        _finish_span(False, error={"type": "model_disabled", "http_status": 403,
                                   "category": "eligibility", "provider": target.provider,
                                   "message": elig.reason})
        return errors.openai_error_response(
            403, elig.reason, "model_disabled",
            headers={"X-Gateway-Correlation-Id": request_id})

    messages = body.get("messages", [])
    est_tokens = count_message_tokens(messages)

    # ── Rate limit (WAVE 19 TRACK C2: User \u2192 Workspace \u2192 Client \u2192 Model) ──
    # Each scope is evaluated in order; first violation wins. Per-scope buckets
    # do not share state.  Client.rate_limits is loaded once per request.
    quota = (ws.quotas or {}).get(model_alias, {})
    client_rl: dict | None = None
    if ws.client_id:
        from gateway.db.models import Client as _Client
        from gateway.db.database import async_session as _ses
        async with _ses() as _s:
            _c = await _s.get(_Client, ws.client_id)
            client_rl = (_c.rate_limits if _c else None) or None
    workspace_rl = ws.rate_limits or None
    allowed, ms_scope, limit_type, retry_after = await enforce_multi_scope(
        client_id=ws.client_id, workspace_id=ws.workspace_id, user_id=user_id,
        alias=model_alias, client_rl=client_rl, workspace_rl=workspace_rl,
        model_quota=quota, est_tokens=est_tokens,
    )
    if not allowed:
        # WAVE 26: include numeric context (limit, current, retry_after) for the
        # admin "why?" view. The limiter already computed retry_after; we read
        # the active limit from the scope_quota dict below.
        scope_quota = (
            quota if ms_scope == "model"
            else (workspace_rl or {}) if ms_scope == "workspace"
            else (client_rl or {}) if ms_scope == "client"
            else ((workspace_rl or {}).get("user") or {}) if ms_scope == "user"
            else {}
        )
        active_limit = scope_quota.get(limit_type) or scope_quota.get(limit_type.lower())
        # current = active_limit + 1 (we know we just exceeded by at least 1).
        # The Redis sliding-window limiter doesn't expose exact current count
        # cheaply at this point, so we approximate as limit + 1 for the demo.
        current_approx = (active_limit + 1) if isinstance(active_limit, int) else None
        bus().emit(RateLimitedEvent(
            request_id=request_id, workspace_id=ws.workspace_id,
            model_alias=model_alias, limit_type=limit_type,
            scope=ms_scope,
            client_id=ws.client_id, user_id=user_id, use_case=use_case,
            limit=active_limit if isinstance(active_limit, int) else None,
            current=current_approx,
            retry_after_seconds=int(retry_after) if retry_after else None,
        ))
        # OTel span attributes for business observability
        if _span is not None:
            _span.set_attribute("agnos.rate_limit.result", "blocked")
            _span.set_attribute("agnos.rate_limit.scope", ms_scope)
            _span.set_attribute("agnos.rate_limit.limit_type", limit_type)
        headers = rate_limit_headers(scope_quota, limit_type, retry_after) | {
            "X-Gateway-Correlation-Id": request_id,
            "X-Gateway-RateLimit-Scope": ms_scope,
        }
        _finish_span(False, error={"type": "rate_limit_exceeded", "http_status": 429,
                                   "category": "rate_limit",
                                   "message": f"Rate limit exceeded ({ms_scope}/{limit_type})."})
        return errors.openai_error_response(429, f"Rate limit exceeded ({ms_scope}/{limit_type}).",
                                            "rate_limit_exceeded", headers=headers)

    # ── Budget caps (Client \u2192 Workspace \u2192 User + per-model) \u2192 402 Payment Required ──
    decision = await check_budget(ws.client_id, ws.workspace_id, user_id,
                                   ws.budgets or {}, model_id=target.model_id)
    if not decision.allowed:
        # WAVE 26: surface the actual cap + spend so the admin sees "$100.84 of $100"
        bus().emit(RateLimitedEvent(
            request_id=request_id, workspace_id=ws.workspace_id,
            model_alias=model_alias, limit_type="budget",
            scope=decision.scope or "workspace",
            client_id=ws.client_id, user_id=user_id, use_case=use_case,
            budget_usd=getattr(decision, "cap", None),
            spent_usd=getattr(decision, "spend", None),
        ))
        if _span is not None:
            _span.set_attribute("agnos.budget.result", "blocked")
            _span.set_attribute("agnos.budget.scope", decision.scope or "workspace")
        _finish_span(False, error={"type": "budget_exceeded", "http_status": 402,
                                   "category": "budget", "message": decision.message})
        return errors.openai_error_response(402, decision.message,
                                            "budget_exceeded",
                                            headers={"X-Gateway-Correlation-Id": request_id})

    # ── Guardrails (input) - DB Rules+Profiles + inline + per-request selection ──
    mode_override = request.headers.get("x-gateway-guardrail-mode")
    ids_hdr = request.headers.get("x-gateway-guardrail-ids")
    selected_ids = [int(x) for x in ids_hdr.split(",") if x.strip().isdigit()] if ids_hdr else []
    guardrail_redacted = False
    from gateway.core.guardrails import store as _gstore
    outcome = await _gstore.evaluate_input(body, ws, selected_ids, mode_override, headers=dict(request.headers))
    if outcome.findings:
        for f in outcome.findings:
            bus().emit(GuardrailDecisionEvent(
                request_id=request_id, workspace_id=ws.workspace_id, model_alias=model_alias,
                rule=outcome.rule, detector=f.detector, action=outcome.action,
                stage="input", excerpt=f"{f.category}:{f.excerpt}",
                client_id=ws.client_id, component=component,
                sub_category=f.category, confidence=getattr(f, "confidence", 1.0)))
        if outcome.blocked:
            _child("guardrails", t_route, time.perf_counter(),
                   **{"agnos.guardrail_action": "block", "agnos.guardrail_rule": outcome.rule})
            if _span is not None:
                _span.set_attribute("agnos.guardrail.result", "blocked")
                _span.set_attribute("agnos.guardrail.rule", outcome.rule)
                _span.set_attribute("agnos.guardrail.detector", outcome.findings[0].detector)
                _span.set_attribute("agnos.guardrail.sub_category", outcome.findings[0].category)
            # WAVE 26: emit a RequestErrorEvent too so the failure shows up in
            # request_logs with full why-context (admin doesn't have to cross-
            # reference guardrail_violations manually).
            first = outcome.findings[0]
            # Prefer the guardrail provider's own CONFIGURED block message (e.g. an
            # AWS Bedrock Guardrail "restricted response") so the caller sees the
            # exact policy text, not a generic gateway message.
            custom_msg = next((f.message for f in outcome.findings if getattr(f, "message", None)), None)
            block_message = custom_msg or (f"Request blocked by gateway guardrail '{outcome.rule}' "
                                           f"({first.category}).")
            bus().emit(RequestErrorEvent(
                request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
                model_alias=model_alias, provider=target.provider, engine=engine().name,
                error_type="guardrail_violation", http_status=422,
                message=block_message,
                latency_ms=(time.perf_counter() - started) * 1000,
                client_id=ws.client_id, component=component,
                error_detail={
                    "category": "guardrail",
                    "rule": outcome.rule,
                    "action": outcome.action,
                    "stage": "input",
                    "guardrail_message": custom_msg,        # the provider's configured response (if any)
                    "matches": [
                        {
                            "detector": f.detector,
                            "sub_category": f.category,
                            "excerpt": f.excerpt,
                            "confidence": getattr(f, "confidence", 1.0),
                        }
                        for f in outcome.findings
                    ],
                },
                use_case=use_case, input_tokens=est_tokens, provider_model_id=target.model_id,
            ))
            _child("guardrails", t_route, time.perf_counter(),
                   error={"type": "guardrail_violation", "http_status": 422, "category": "guardrail",
                          "message": block_message})
            _finish_span(False, error={"type": "guardrail_violation", "http_status": 422,
                                       "category": "guardrail", "provider": target.provider,
                                       "message": block_message})
            return errors.openai_error_response(
                422, block_message, "guardrail_violation",
                headers={"X-Gateway-Correlation-Id": request_id})
        if outcome.action == "redact" and outcome.redacted_messages is not None:
            body["messages"] = outcome.redacted_messages
            guardrail_redacted = True

    # ── Chunking (opt-in) ──
    truncate = (request.headers.get("x-gateway-auto-truncate", "").lower() == "true"
                or bool((ws.guardrails or {}).get("auto_truncate")))
    trunc_info = {}
    if truncate:
        body["messages"], trunc_info = apply_truncation(body["messages"], target.context_window)

    t_policy = time.perf_counter()
    _child("guardrails", t_route, t_policy,
           **{"agnos.guardrail_action": outcome.action if outcome.findings else "none"})
    stream = bool(body.get("stream"))
    has_tools = bool(body.get("tools"))
    eng = engine()

    # WAVE 25 TRACK 3: per-workspace engine override (insourcing path).
    # If the workspace declares a direct-engine override for this provider,
    # use our owned adapter instead of the rented Bifrost translation.
    # Per-provider engine selection is a GATEWAY-WIDE setting (identical for every
    # client/workspace), honoring a rented→owned override incl. a weighted CANARY
    # split (e.g. 30% DirectEngine / 70% Bifrost). The engine that actually served
    # is recorded per request, so the split shows in analytics.
    eng = select_engine(engine_routing.get_overrides(), target.provider)
    # record WHICH engine served on the trace so the Jaeger span shows it (proof of
    # the swappable slot: bifrost | litellm | portkey | direct | echo).
    if _span is not None:
        try:
            _span.set_attribute("agnos.engine", eng.name)
            _span.set_attribute("agnos.provider_model_id", target.model_id)
        except Exception:  # noqa: BLE001
            pass

    # per-request timeout (seconds): an explicit X-Gateway-Timeout header is a hard
    # override (clamped to the global ceiling); otherwise None, so fallback applies
    # each target's CONFIGURED timeout (provider config else gateway default). That
    # is what lets a long-running use case run up to settings.max_request_timeout_s
    # without forcing a header on every call.
    to_hdr = request.headers.get("x-gateway-timeout")
    req_timeout: float | None = None
    if to_hdr:
        try:
            req_timeout = max(1.0, min(float(to_hdr), float(settings.max_request_timeout_s)))
        except ValueError:
            req_timeout = None

    bus().emit(RequestStartEvent(
        request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
        use_case=use_case, model_alias=model_alias, provider=target.provider,
        provider_model_id=target.model_id, engine=eng.name, stream=stream,
        has_tools=has_tools,
        client_id=ws.client_id, component=component,
    ))

    if stream:
        await _attach_credentials(ws.workspace_id, target)
        if _span is not None:
            _span.set_attribute("agnos.stream", True)
        # Keep the parent span OPEN across the stream: _stream ends it (OK) on a clean
        # finish, or marks a failed ENGINE child + parent span ERROR if the provider
        # fails mid-stream - so streaming failures are visible in the trace too.
        def _stream_ok() -> None:
            _finish_span(True)

        def _stream_err(err: dict, t0: float) -> None:
            _child("engine", t0, time.perf_counter(), error=err, **{"agnos.engine": eng.name})
            _finish_span(False, error=err)

        return StreamingResponse(
            _stream(eng, body, target, ws, model_alias, user_id, use_case, request_id, started,
                    on_success=_stream_ok, on_error=_stream_err),
            media_type="text/event-stream",
            headers={
                "X-Gateway-Correlation-Id": request_id,
                # Stop reverse proxies (nginx/ingress) from buffering the SSE stream -
                # without these, prod streams arrive all-at-once or appear to hang
                # ("pending") even though local (no proxy) works fine.
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ── Exact-match response cache + Idempotency-Key (opt-in) ──
    cache_ttl_hdr = request.headers.get("x-gateway-cache-ttl")
    idem_key = request.headers.get("idempotency-key")
    ckey = None
    if cache_ttl_hdr or idem_key:
        ckey = (f"idem:{ws.workspace_id}:{idem_key}" if idem_key
                else _cache.cache_key(ws.workspace_id, model_alias, body))
        cached = await _cache.get(ckey)
        if cached is not None:
            u = cached.get("usage") or {}
            bus().emit(CacheHitEvent(
                request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
                use_case=use_case, model_alias=model_alias, provider=target.provider,
                input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0),
                cost_saved_usd=compute_cost(target.model_id, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)),
                component=component, client_id=ws.client_id))
            _finish_span(True)
            return JSONResponse(content=cached, headers={"X-Gateway-Correlation-Id": request_id,
                                                         "X-Gateway-Cache": "HIT"})

    t_eng0 = time.perf_counter()
    try:
        fb = await fallback_execute(eng, body, targets, ws.workspace_id, timeout=req_timeout)
    except Exception as exc:  # noqa: BLE001
        code, err = errors.map_exception(exc)
        # WAVE 26: capture full failure context for admin "why?" view
        provider_error_detail = {
            "category": "provider_error",
            "http_status": code,
            "raw_message": err["error"]["message"],
            "exception_type": type(exc).__name__,
            "retries": 0,                        # exception path = no successful attempt
            "fallback_attempted": False,
            "attempted_targets": [
                {"provider": t.provider, "model_id": t.model_id, "weight": getattr(t, "weight", 1)}
                for t in targets
            ],
        }
        bus().emit(RequestErrorEvent(
            request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
            model_alias=model_alias, provider=target.provider, engine=eng.name,
            error_type=err["error"]["type"], http_status=code, message=err["error"]["message"],
            latency_ms=(time.perf_counter() - started) * 1000,
            client_id=ws.client_id, component=component,
            error_detail=provider_error_detail,
            use_case=use_case, input_tokens=est_tokens, provider_model_id=target.model_id,
        ))
        _eng_err = {"type": err["error"]["type"], "code": err["error"].get("code"),
                    "http_status": code, "provider": target.provider,
                    "message": err["error"]["message"], "category": "provider_error"}
        _child("engine", t_eng0, time.perf_counter(), error=_eng_err,
               **{"agnos.engine": eng.name, "agnos.provider": target.provider,
                  "agnos.provider_model_id": target.model_id})
        _finish_span(False, error=_eng_err)
        return JSONResponse(status_code=code, content=err,
                            headers={"X-Gateway-Correlation-Id": request_id})

    # engine stage span - mark ERROR when the served result failed, so the trace
    # exposes the failing provider invocation instead of a normal teal span.
    _served_ok = fb.result.ok
    _eng_err = None
    if not _served_ok:
        _c, _e = errors.map_bifrost_error(fb.result.status_code, fb.result.body)
        _eng_err = {"type": _e["error"]["type"], "code": _e["error"].get("code"),
                    "http_status": fb.result.status_code, "provider": fb.target.provider,
                    "message": _e["error"]["message"], "category": "provider_error"}
    _child("engine", t_eng0, time.perf_counter(), error=_eng_err,
           **{"agnos.engine": eng.name, "agnos.provider": fb.target.provider,
              "agnos.provider_model_id": fb.target.model_id, "agnos.attempt": fb.attempt})
    if _span is not None and fb.fallbacks_emitted:
        _span.set_attribute("agnos.fallback.attempted", True)
        _span.set_attribute("agnos.fallback.from_provider", fb.fallbacks_emitted[0][0])
        _span.set_attribute("agnos.fallback.to_provider", fb.fallbacks_emitted[-1][1])
    for frm, to, reason in fb.fallbacks_emitted:
        bus().emit(FallbackEvent(request_id=request_id, workspace_id=ws.workspace_id,
                                 model_alias=model_alias, from_provider=frm, to_provider=to, reason=reason,
                                 client_id=ws.client_id))

    result = fb.result
    target = fb.target  # the target that actually served (may be a fallback)

    if not result.ok:
        code, err = errors.map_bifrost_error(result.status_code, result.body)
        # Timeouts: replace the engine's misleading generic message (e.g. Bifrost's
        # static "default is 30 seconds", which ignores the real configured value)
        # with the timeout the gateway ACTUALLY applied for this target.
        is_timeout = err["error"]["type"] == "timeout"
        eff_to = fb.effective_timeout_s or req_timeout or settings.request_timeout_seconds
        timeout_source = "X-Gateway-Timeout header" if req_timeout else "provider config / gateway default"
        if is_timeout:
            err["error"]["message"] = (
                f"Upstream timed out after the configured {eff_to:g}s for provider "
                f"'{target.provider}' (source: {timeout_source}). This is the timeout the "
                f"gateway actually applied - increase it via the X-Gateway-Timeout request "
                f"header or in Providers > {target.provider} > Network Config "
                f"(request_timeout_seconds, up to {settings.max_request_timeout_s}s).")
        # WAVE 26: capture provider raw response so admin doesn't have to grep logs.
        # Include full fallback chain context if multiple targets were tried.
        raw_body = result.body
        if isinstance(raw_body, (dict, list)):
            raw_excerpt = raw_body
        else:
            raw_excerpt = str(raw_body)[:500] if raw_body else None
        provider_error_detail = {
            "category": "provider_error",
            "provider": target.provider,
            "model_id": target.model_id,
            "http_status": result.status_code,
            "raw_response": raw_excerpt,
            "mapped_message": err["error"]["message"],
            "retries": max(0, fb.attempt - 1),
            "fallback_attempted": len(fb.fallbacks_emitted) > 0,
            "fallback_chain": [
                {"from": frm, "to": to, "reason": reason}
                for frm, to, reason in fb.fallbacks_emitted
            ],
            "attempted_targets": [
                {"provider": t.provider, "model_id": t.model_id, "weight": getattr(t, "weight", 1)}
                for t in targets
            ],
            # Per-attempt outcomes in actual order, each with WHY it failed - so the
            # failover sequence in the trace is debuggable, not just a list of names.
            "attempts": fb.attempts,
        }
        if is_timeout:
            provider_error_detail["timeout"] = {
                "effective_s": eff_to,
                "source": timeout_source,
                "max_s": settings.max_request_timeout_s,
            }
        bus().emit(RequestErrorEvent(
            request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
            model_alias=model_alias, provider=target.provider, engine=eng.name,
            error_type=err["error"]["type"], http_status=code, message=err["error"]["message"],
            latency_ms=(time.perf_counter() - started) * 1000,
            client_id=ws.client_id, component=component,
            error_detail=provider_error_detail,
            use_case=use_case, input_tokens=est_tokens, provider_model_id=target.model_id,
        ))
        _finish_span(False, error=_eng_err or {"type": err["error"]["type"], "http_status": code,
                                               "category": "provider_error", "provider": target.provider,
                                               "message": err["error"]["message"]})
        return JSONResponse(status_code=code, content=err,
                            headers={"X-Gateway-Correlation-Id": request_id})

    usage = result.usage
    cost = compute_cost(target.model_id, usage["input_tokens"], usage["output_tokens"])
    total_ms = (time.perf_counter() - started) * 1000
    t_gov0 = time.perf_counter()
    from gateway.core import metrics as _M
    overhead_total = max(0.0, (total_ms - fb.provider_ms)) / 1000.0
    _M.OVERHEAD.labels(stage="auth").observe(max(0.0, t_auth - started))
    _M.OVERHEAD.labels(stage="routing").observe(max(0.0, t_route - t_auth))
    _M.OVERHEAD.labels(stage="policy").observe(max(0.0, t_policy - t_route))
    # Bare-proxy plumbing: identity resolution + routing decision before any
    # governance work (guardrails/budget) or the provider dispatch. This is the
    # honest analogue of a bare gateway's "added latency" (cf. Bifrost), since it
    # excludes the value-add policy stages and the upstream call entirely.
    _M.OVERHEAD.labels(stage="proxy").observe(max(0.0, t_route - started))
    _M.OVERHEAD.labels(stage="total").observe(overhead_total)
    if _span is not None:
        _span.set_attribute("agnos.input_tokens", usage["input_tokens"])
        _span.set_attribute("agnos.output_tokens", usage["output_tokens"])
        _span.set_attribute("agnos.cost_usd", cost)
        _span.set_attribute("agnos.overhead_ms", max(0.0, total_ms - fb.provider_ms))

    # ── Guardrails (output) on the NON-stream response ──
    # The streaming path audits output (bytes are already sent, can't unsend).
    # Non-stream responses aren't sent yet, so we can additionally BLOCK when the
    # effective guardrail mode is "block" - closing the gap where output was never
    # scanned on the non-stream path. Same detector/rule set (apply_to=output|both).
    out_flagged = False
    try:
        _out_text = (result.body.get("choices", [{}])[0].get("message", {}) or {}).get("content") or ""
    except Exception:  # noqa: BLE001
        _out_text = ""
    if _out_text:
        out_findings = await _gstore.evaluate_output(_out_text, ws, headers=dict(request.headers))
        if out_findings:
            out_flagged = True
            for f in out_findings:
                bus().emit(GuardrailDecisionEvent(
                    request_id=request_id, workspace_id=ws.workspace_id, model_alias=model_alias,
                    rule="output-scan", detector=f.detector, action="output", stage="output",
                    excerpt=f"{f.category}:{f.excerpt}", client_id=ws.client_id, component=component,
                    sub_category=f.category, confidence=getattr(f, "confidence", 1.0)))
            eff_mode = (mode_override or (ws.guardrails or {}).get("mode") or "audit").lower()
            if eff_mode == "block":
                block_message = (f"Response blocked by gateway output guardrail "
                                 f"({out_findings[0].category}).")
                bus().emit(RequestErrorEvent(
                    request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
                    model_alias=model_alias, provider=target.provider, engine=eng.name,
                    error_type="guardrail_violation", http_status=422, message=block_message,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    client_id=ws.client_id, component=component,
                    error_detail={"category": "guardrail", "rule": "output-scan", "action": "block",
                                  "stage": "output",
                                  "matches": [{"detector": f.detector, "sub_category": f.category,
                                               "excerpt": f.excerpt} for f in out_findings]},
                    use_case=use_case, input_tokens=usage["input_tokens"], provider_model_id=target.model_id))
                _finish_span(False, error={"type": "guardrail_violation", "http_status": 422,
                                           "category": "guardrail", "provider": target.provider,
                                           "message": block_message})
                return errors.openai_error_response(
                    422, block_message, "guardrail_violation",
                    headers={"X-Gateway-Correlation-Id": request_id, "X-Gateway-Guardrail": "output-blocked"})

    bus().emit(RequestSuccessEvent(
        request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
        use_case=use_case, model_alias=model_alias, provider=target.provider,
        provider_model_id=target.model_id, engine=eng.name,
        input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
        cost_usd=cost, latency_ms=total_ms, stream=False,
        attempt=fb.attempt, provider_ms=fb.provider_ms, key_id=ws.key_id, component=component,
        client_id=ws.client_id,
    ))
    # Correctness fix (TRACK C2): record the just-completed cost in the
    # live-delta map so the *next* request's budget check sees it before the
    # async Postgres write lands. Without this the cap-check stays warm at the
    # pre-call snapshot and a tenant could overspend until the cache TTL ages.
    from gateway.core.budgets import add_live_spend
    add_live_spend(ws.client_id, ws.workspace_id, user_id, cost, target.model_id)
    # record the full outcome on the trace so Jaeger stores everything (engine, model,
    # tokens, cost) - not just the provider.
    if _span is not None:
        try:
            _span.set_attribute("agnos.engine", eng.name)
            _span.set_attribute("agnos.provider_model_id", target.model_id)
            _span.set_attribute("agnos.input_tokens", int(usage["input_tokens"]))
            _span.set_attribute("agnos.output_tokens", int(usage["output_tokens"]))
            _span.set_attribute("agnos.cost_usd", float(cost))
        except Exception:  # noqa: BLE001
            pass
    _child("governance", t_gov0, time.perf_counter(),
           **{"agnos.engine": eng.name, "agnos.input_tokens": int(usage["input_tokens"]),
              "agnos.output_tokens": int(usage["output_tokens"]), "agnos.cost_usd": float(cost)})
    _finish_span(True)
    # populate response cache / idempotency store
    if ckey is not None:
        ttl = 0
        try:
            ttl = int(cache_ttl_hdr) if cache_ttl_hdr else 300
        except ValueError:
            ttl = 300
        await _cache.put(ckey, result.body, ttl)
    headers = {"X-Gateway-Correlation-Id": request_id}
    if cache_ttl_hdr or idem_key:
        headers["X-Gateway-Cache"] = "MISS"
    if guardrail_redacted:
        headers["X-Gateway-Guardrail"] = "redacted"
    elif out_flagged:
        headers["X-Gateway-Guardrail"] = "output-flagged"
    if trunc_info.get("truncated"):
        headers.update({"X-Gateway-Truncated-Messages": str(trunc_info.get("dropped_messages", 0)),
                        "X-Gateway-Original-Tokens": str(trunc_info.get("original_tokens", 0)),
                        "X-Gateway-Sent-Tokens": str(trunc_info.get("sent_tokens", 0))})
    return JSONResponse(content=result.body, headers=headers)


async def _stream(eng, body, target, ws, model_alias, user_id, use_case, request_id, started,
                  *, on_success=None, on_error=None):
    """Forward Bifrost SSE verbatim; parse a typed copy for governance.

    A provider failure mid-stream is recorded as an ERROR (not a success) and the
    trace's engine + parent span are marked ERROR via ``on_error`` - so a streamed
    failure is as visible in governance + Jaeger as a non-streamed one.
    """
    import json as _json

    in_tok = out_tok = 0
    out_text = []
    _t_eng0 = time.perf_counter()
    _stream_error: dict | None = None
    try:
        async for chunk in eng.chat_stream(body, target):
            yield chunk
            # parse a copy for usage (Bifrost includes usage in final chunk when available)
            text = chunk.decode(errors="ignore")
            if text.startswith("data: ") and "[DONE]" not in text:
                try:
                    payload = _json.loads(text[6:].strip())
                    u = payload.get("usage") or {}
                    in_tok = u.get("prompt_tokens", in_tok) or in_tok
                    out_tok = u.get("completion_tokens", out_tok) or out_tok
                    delta = (((payload.get("choices") or [{}])[0]).get("delta") or {})
                    if delta.get("content"):
                        out_text.append(delta["content"])
                    # tool-call turns put generated content in tool_calls, not content
                    for tc in (delta.get("tool_calls") or []):
                        fn = (tc.get("function") or {})
                        if fn.get("name"):
                            out_text.append(fn["name"])
                        if fn.get("arguments"):
                            out_text.append(fn["arguments"])
                except Exception:
                    pass
    except Exception as exc:  # noqa: BLE001
        # A real provider failure mid-stream (not a client disconnect, which raises
        # GeneratorExit/CancelledError - both BaseException, deliberately not caught).
        code, err = errors.map_exception(exc)
        _stream_error = {"type": err["error"]["type"], "code": err["error"].get("code"),
                         "http_status": code, "provider": target.provider,
                         "message": err["error"]["message"]}
    finally:
        # Backfill token counts when the provider omitted a usage chunk so streamed
        # turns aren't recorded as 0 (input from the request, output from what we saw).
        if not in_tok:
            try:
                from gateway.core.tokens import count_message_tokens
                in_tok = count_message_tokens(body.get("messages") or [])
            except Exception:  # noqa: BLE001
                pass
        if not out_tok and out_text:
            try:
                from gateway.core.tokens import estimate_tokens
                out_tok = estimate_tokens("".join(out_text))
            except Exception:  # noqa: BLE001
                pass
        cost = compute_cost(target.model_id, in_tok, out_tok)
        from gateway.core.budgets import add_live_spend
        if _stream_error is not None:
            # Failed mid-stream → record an ERROR (not a success) + mark the trace.
            bus().emit(RequestErrorEvent(
                request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
                model_alias=model_alias, provider=target.provider, engine=eng.name,
                error_type=_stream_error["type"], http_status=_stream_error["http_status"],
                message=_stream_error["message"],
                latency_ms=(time.perf_counter() - started) * 1000, stream=True,
                client_id=ws.client_id, component=ws.component, use_case=use_case,
                input_tokens=in_tok, provider_model_id=target.model_id))
            if on_error:
                try:
                    on_error(_stream_error, _t_eng0)
                except Exception:  # noqa: BLE001
                    pass
        else:
            # streaming output guardrails = audit-only (never block mid-stream)
            try:
                from gateway.core.guardrails import store as _gstore
                findings = await _gstore.evaluate_output("".join(out_text), ws)
                for f in findings:
                    bus().emit(GuardrailDecisionEvent(
                        request_id=request_id, workspace_id=ws.workspace_id, model_alias=model_alias,
                        rule="output-audit", detector=f.detector, action="audit",
                        stage="output", excerpt=f"{f.category}:{f.excerpt}",
                        client_id=ws.client_id, component=ws.component))
            except Exception:  # noqa: BLE001
                pass
            bus().emit(RequestSuccessEvent(
                request_id=request_id, workspace_id=ws.workspace_id, user_id=user_id,
                use_case=use_case, model_alias=model_alias, provider=target.provider,
                provider_model_id=target.model_id, engine=eng.name,
                input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
                latency_ms=(time.perf_counter() - started) * 1000, stream=True, key_id=ws.key_id,
                component=ws.component, client_id=ws.client_id,
            ))
            add_live_spend(ws.client_id, ws.workspace_id, user_id, cost, target.model_id)
            if on_success:
                try:
                    on_success()
                except Exception:  # noqa: BLE001
                    pass
