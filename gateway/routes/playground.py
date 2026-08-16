"""Playground API - server-side execution for the interactive showcase.

POST /playground/run
  - Accepts: framework, workspace_id, model, prompt, options (stream, tools, etc.)
  - Executes a real governed request through the gateway's own /v1/* endpoint
  - Returns the response + the governance event it produced

This lets anyone run any framework through one base_url without needing
API keys or local tooling.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from gateway.config import settings
from gateway.core.security import verify_session
from gateway.db.database import async_session
from gateway.db.models import Workspace, RequestLog, Client
from sqlalchemy import select, desc

_log = structlog.get_logger("playground")
router = APIRouter(prefix="/playground", tags=["playground"])

# Base URL for internal requests (loop back to ourselves)
_GATEWAY_BASE = f"http://127.0.0.1:{settings.port}"


def _require_login(request: Request) -> str:
    """Verify session cookie and return the username."""
    data = verify_session(request.cookies.get("agnos_session", ""))
    if not data:
        raise HTTPException(401, "Login required")
    return data["sub"]


class PlaygroundRunRequest(BaseModel):
    workspace_id: str
    model: str | None = None  # alias or provider:model_id
    prompt: str = "Hello! Explain what you are in one sentence."
    framework: str = "openai"  # openai | langchain | langgraph | crewai | pydantic_ai
    stream: bool = False
    use_tools: bool = False
    component: str = "playground"
    engine: str | None = None  # if set, temporarily swap engine for this request
    # Demo triggers (kept for backwards compat)
    trigger_pii_block: bool = False
    trigger_fallback: bool = False
    trigger_rate_limit: bool = False
    # Scenario-based demo (preferred)
    scenario: str = "normal"  # normal | tool_call | guardrail_phone | guardrail_email | guardrail_ssn | guardrail_aws_secret | rate_limit | provider_failure | budget_exceeded
    max_tokens: int = 200


class PlaygroundRunResponse(BaseModel):
    response_text: str
    framework: str
    engine_used: str
    governance_event: dict[str, Any] | None = None
    latency_ms: float
    error: str | None = None
    error_category: str | None = None
    error_message: str | None = None
    error_details: dict[str, Any] | None = None
    failure_stage: str | None = None
    http_status: int = 200
    trace_id: str | None = None
    code_snippet: str = ""
    # Tool-call visualization
    tool_calls: list[dict[str, Any]] = []
    # Real-data explainability (queried from DB after the request)
    explainability: dict[str, Any] | None = None


# ─── Framework code snippets (shown in "Show the code" tab) ───

SNIPPETS = {
    "openai": '''from openai import OpenAI

client = OpenAI(
    base_url="{base_url}/v1",  # <-- ONE LINE CHANGE
    api_key="{api_key}",
)

response = client.chat.completions.create(
    model="{model}",
    messages=[{{"role": "user", "content": "{prompt}"}}],
    max_tokens={max_tokens},
)
print(response.choices[0].message.content)''',

    "langchain": '''from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="{base_url}/v1",  # <-- ONE LINE CHANGE
    api_key="{api_key}",
    model="{model}",
    max_tokens={max_tokens},
)

response = llm.invoke("{prompt}")
print(response.content)''',

    "langgraph": '''from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, MessagesState, START, END

llm = ChatOpenAI(
    base_url="{base_url}/v1",  # <-- ONE LINE CHANGE
    api_key="{api_key}",
    model="{model}",
    max_tokens={max_tokens},
)

def agent(state: MessagesState):
    return {{"messages": [llm.invoke(state["messages"])]}}

graph = StateGraph(MessagesState)
graph.add_node("agent", agent)
graph.add_edge(START, "agent")
graph.add_edge("agent", END)
app = graph.compile()

result = app.invoke({{"messages": [{{"role": "user", "content": "{prompt}"}}]}})
print(result["messages"][-1].content)''',

    "crewai": '''from crewai import Agent, Task, Crew
import os

os.environ["OPENAI_API_BASE"] = "{base_url}/v1"  # <-- ONE LINE CHANGE
os.environ["OPENAI_API_KEY"] = "{api_key}"
os.environ["OPENAI_MODEL_NAME"] = "{model}"

agent = Agent(role="assistant", goal="Answer questions", backstory="Helpful AI")
task = Task(description="{prompt}", agent=agent, expected_output="A helpful response")
crew = Crew(agents=[agent], tasks=[task])
result = crew.kickoff()
print(result)''',

    "pydantic_ai": '''from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

model = OpenAIModel(
    "{model}",
    base_url="{base_url}/v1",  # <-- ONE LINE CHANGE
    api_key="{api_key}",
)

agent = Agent(model)
result = agent.run_sync("{prompt}")
print(result.data)''',
}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                },
                "required": ["location"],
            },
        },
    }
]


@router.post("/run", response_model=PlaygroundRunResponse)
async def playground_run(body: PlaygroundRunRequest, request: Request):
    """Execute a governed request and return the result + governance event."""
    user = _require_login(request)

    start = time.perf_counter()

    # Resolve workspace and its API key
    async with async_session() as s:
        ws = await s.scalar(select(Workspace).where(Workspace.workspace_id == body.workspace_id))
    if not ws:
        raise HTTPException(404, f"Workspace '{body.workspace_id}' not found")

    # Get a working API key for this workspace
    api_key = await _get_workspace_key(body.workspace_id)
    if not api_key:
        raise HTTPException(400, f"No API key found for workspace '{body.workspace_id}'. Create one first.")

    # Engine swap (temporary)
    original_engine = None
    if body.engine:
        original_engine = await _swap_engine(body.engine)

    try:
        # ─── Scenario controls TECHNICAL knobs only ───
        # The user's prompt is sent VERBATIM. The frontend pre-fills the prompt
        # textarea with a recommended example when a scenario is picked, but the
        # user can edit it freely - what's in the textarea is exactly what we send.
        # That means:
        #   • Guardrails fire whenever PII/secrets are in the actual prompt content,
        #     regardless of which scenario is selected (workspace-level config).
        #   • Picking a guardrail scenario but typing a clean prompt = no block.
        #   • Picking 'normal' but typing real PII = block (guardrails are always on).
        #   • Picking 'tool_call' forces the model to invoke a tool whatever you ask.
        scenario = body.scenario
        prompt = body.prompt                  # ← SENT VERBATIM, never mutated
        use_tools = body.use_tools
        force_tool_call = False

        # Backwards-compat: old per-trigger booleans map to scenario IDs but
        # do NOT change the prompt. (Frontend stopped sending these in WAVE 26.)
        if body.trigger_pii_block:
            scenario = scenario or "guardrail_aws_secret"
        if body.trigger_rate_limit:
            scenario = "rate_limit"

        if scenario == "tool_call":
            # Enable tools and force the model to invoke one. Honest consequence:
            # if the user types a non-tool prompt ("tell me a joke"), Claude is
            # still forced to call get_weather - visible proof of tool_choice="required".
            use_tools = True
            force_tool_call = True
        # rate_limit handled below (pre-fire burst)
        # provider_failure: frontend sets the bad model id; nothing extra here.
        # guardrail_*: NO server-side prompt change - workspace guardrails fire
        #              if and only if the actual prompt content matches.

        # Determine model
        model = body.model or "default-chat"

        # Build request body
        messages = [{"role": "user", "content": prompt}]
        req_body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": body.max_tokens,
            "stream": False,  # playground always non-stream for simplicity of capture
        }
        if use_tools:
            req_body["tools"] = TOOLS_SCHEMA
            if force_tool_call:
                # OpenAI: forces a tool call. Bifrost translates to Anthropic tool_choice={"type":"any"}
                req_body["tool_choice"] = "required"

        # Headers - generate a request_id we can correlate after
        playground_request_id = f"req-pg-{uuid.uuid4().hex[:12]}"
        # Determine use_case from scenario for attribution
        use_case_map = {
            "normal": "playground.demo",
            "tool_call": "playground.tool_demo",
            "guardrail_aws_secret": "playground.guardrail_demo",
            "guardrail_phone": "playground.guardrail_demo",
            "guardrail_email": "playground.guardrail_demo",
            "guardrail_ssn": "playground.guardrail_demo",
            "rate_limit": "playground.rate_limit_demo",
            "provider_failure": "playground.routing_demo",
        }
        use_case = use_case_map.get(scenario, "playground.demo")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Gateway-Component": body.component or "playground",
            "X-Gateway-User": user,
            "X-Gateway-Use-Case": use_case,
            "X-Request-Id": playground_request_id,
            "Content-Type": "application/json",
        }

        # Execute the request against our own gateway
        trace_id = None
        response_text = ""
        error = None
        error_category: str | None = None
        error_message: str | None = None
        error_details: dict[str, Any] | None = None
        failure_stage: str | None = None
        http_status = 200
        tool_calls_list: list[dict] = []
        _no_rpm_configured = False

        async with httpx.AsyncClient(timeout=60.0) as client:
            if scenario == "rate_limit":
                # Trip the workspace's REAL per-minute RPM by DRAINING the in-
                # process rate-limiter bucket directly. The previous approach fired
                # N real HTTP requests, but on a slow engine (Bifrost ~12s/call) the
                # gather awaited all responses → the bucket REFILLED during that time
                # → the final request passed. Draining the bucket in-process is
                # instant, deterministic, and costs $0 (no provider calls).
                from gateway.core.rate_limit import limiter
                eff_rpm = await _effective_rpm(ws, model)
                if eff_rpm:
                    alias_for_rl = model or ws.default_chat_alias or "default"
                    # Drain: call check_multi_scope() eff_rpm times → bucket goes to 0.
                    workspace_rl = ws.rate_limits or None
                    client_rl_obj = None
                    if ws.client_id:
                        try:
                            async with async_session() as _s:
                                _c = await _s.get(Client, ws.client_id)
                            client_rl_obj = (_c.rate_limits if _c else None) or None
                        except Exception:  # noqa: BLE001
                            pass
                    model_quota = (ws.quotas or {}).get(alias_for_rl, {})
                    for _ in range(eff_rpm):
                        limiter.check_multi_scope(
                            client_id=ws.client_id, workspace_id=body.workspace_id,
                            user_id=user, alias=alias_for_rl,
                            client_rl=client_rl_obj, workspace_rl=workspace_rl,
                            model_quota=model_quota, est_tokens=1)
                    _log.info("playground_rate_limit_drain", workspace=body.workspace_id,
                              effective_rpm=eff_rpm, alias=alias_for_rl)
                else:
                    _no_rpm_configured = True
            resp = await client.post(
                f"{_GATEWAY_BASE}/v1/chat/completions",
                json=req_body,
                headers=headers,
            )

        latency_ms = (time.perf_counter() - start) * 1000

        # Set http_status from response (initialized to 200 above)
        http_status = resp.status_code

        # Honest demo: if the rate-limit scenario couldn't trip because the
        # workspace has no rpm cap, say so rather than show a normal success.
        if scenario == "rate_limit" and resp.status_code == 200 and _no_rpm_configured:
            error_category = "rate_limit"
            error_message = ("No rate limit is configured on this workspace, so nothing was "
                             "throttled. Set a per-minute RPM under Workspaces → Edit "
                             "(rate_limits.rpm) to demonstrate this scenario.")
            failure_stage = "rate_limit"
            error = error_message
            response_text = error_message

        # Helper to extract human message from FastAPI error envelope
        def _extract_msg(payload: Any) -> str:
            if isinstance(payload, dict):
                detail = payload.get("detail", payload)
                if isinstance(detail, dict):
                    err = detail.get("error", detail)
                    if isinstance(err, dict):
                        return err.get("message") or str(err)
                    return str(err)
                return str(detail)
            return str(payload)

        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            tool_calls_list = []
            if choices:
                msg = choices[0].get("message", {})
                response_text = msg.get("content") or ""
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        tool_calls_list.append({
                            "id": tc.get("id"),
                            "type": tc.get("type", "function"),
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                            "duration_ms": None,
                        })
                # Fallback: if Claude generated XML-style tool calls in the text
                # (happens when underlying engine doesn't fully wire structured tools API),
                # parse them so the demo is deterministic. This is a *display* normalization;
                # the model genuinely decided to call the tool.
                if not tool_calls_list and response_text and "<function_calls>" in response_text:
                    import re as _re
                    pattern = _re.compile(
                        r'<invoke\s+name="([^"]+)">(.*?)</invoke>',
                        _re.DOTALL,
                    )
                    for m in pattern.finditer(response_text):
                        name = m.group(1)
                        body_xml = m.group(2)
                        # Extract <parameter name="X">VALUE</parameter>
                        params: dict[str, str] = {}
                        for pm in _re.finditer(
                            r'<parameter\s+name="([^"]+)">([^<]*)</parameter>',
                            body_xml,
                        ):
                            params[pm.group(1)] = pm.group(2).strip()
                        import json as _json
                        tool_calls_list.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "name": name,
                            "arguments": _json.dumps(params),
                            "duration_ms": None,
                            "_synthesized": True,   # the playground UI can hint at this
                        })
                    # Replace the XML noise in the response with a clean summary
                    if tool_calls_list:
                        names = [tc["name"] for tc in tool_calls_list]
                        response_text = (f"Model invoked {len(tool_calls_list)} tool(s): "
                                          f"{', '.join(names)}. (See Tool Calls panel below for arguments.)")

        # ── Simulated tool execution (so the demo flow feels complete) ──
        # Tool calls are real - the model decided to call them. Here we just
        # provide a deterministic stub result for display purposes.
        if tool_calls_list:
            for tc in tool_calls_list:
                if tc["name"] == "get_weather":
                    try:
                        import json as _json
                        args = _json.loads(tc.get("arguments") or "{}")
                        location = args.get("location", "the requested location")
                        tc["result"] = f"Weather in {location}: 28°C, partly cloudy, 65% humidity"
                        tc["duration_ms"] = 42  # simulated tool latency
                    except Exception:
                        tc["result"] = "[tool stub]"
                else:
                    tc["result"] = "[tool stub]"

        # ── Error path ──
        if resp.status_code != 200:
            try:
                payload = resp.json()
            except Exception:
                payload = {"detail": resp.text}
            error_details = payload if isinstance(payload, dict) else {"raw": str(payload)}
            human_msg = _extract_msg(payload)

            # Robustly extract the OpenAI-shaped error type across both envelopes:
            #   direct  {"error": {"type": ...}}              (our JSONResponse)
            #   wrapped {"detail": {"error": {"type": ...}}}  (FastAPI HTTPException)
            def _err_type(p: Any) -> str | None:
                if not isinstance(p, dict):
                    return None
                e = p.get("error")
                if isinstance(e, dict) and e.get("type"):
                    return e["type"]
                d = p.get("detail")
                if isinstance(d, dict) and isinstance(d.get("error"), dict):
                    return d["error"].get("type")
                return None
            err_type = _err_type(error_details)

            if resp.status_code == 401:
                error_category = "auth"
                error_message = "Authentication failed - invalid or expired API key."
                failure_stage = "auth"
            elif resp.status_code == 403:
                error_category = "auth"
                error_message = "Forbidden - key lacks required role for this endpoint."
                failure_stage = "auth"
            elif resp.status_code == 404:
                error_category = "model_not_found"
                error_message = f"Model not registered for this workspace. {human_msg}"
                failure_stage = "routing"
            elif resp.status_code == 422:
                # Guardrail block vs request validation. Detect by error TYPE
                # (reliable) - NOT by scanning the message text, since a guardrail's
                # configured response (e.g. Bedrock's restricted message) may not
                # contain the word "guardrail".
                if err_type == "guardrail_violation" or "guardrail" in str(human_msg).lower():
                    error_category = "guardrail"
                    # Surface the gateway's actual block message - which is the
                    # guardrail provider's CONFIGURED response (e.g. a Bedrock
                    # Guardrail's restricted-response text) - not a generic string.
                    error_message = human_msg or "Guardrail blocked the request."
                    failure_stage = "guardrails"
                else:
                    error_category = "validation"
                    error_message = f"Request validation failed: {human_msg}"
                    failure_stage = "auth"   # validation failures are caught early
            elif resp.status_code == 429:
                error_category = "rate_limit"
                error_message = "Rate limit exceeded - too many requests."
                failure_stage = "rate_limit"
            elif resp.status_code == 402:
                error_category = "budget"
                error_message = "Budget exceeded - workspace or user spending cap reached."
                failure_stage = "budget"
            elif resp.status_code in (502, 503, 504):
                error_category = "provider_error"
                error_message = f"Upstream provider unavailable. {human_msg}"
                failure_stage = "provider"
            else:
                error_category = "unknown"
                error_message = f"Unexpected error (HTTP {resp.status_code}). {human_msg}"
                failure_stage = "engine"

            error = error_message
            response_text = error_message  # show the friendly message in the response area

        # Fetch the governance event this request produced
        # Small delay to let the async observer bus persist the event
        await asyncio.sleep(0.5)
        trace_id = resp.headers.get("x-trace-id") or resp.headers.get("x-request-id")
        governance_event = await _fetch_latest_governance_event(body.workspace_id, body.component)

        # Engine actually used for THIS request (not the global default).
        # Pulled from the governance event the chat route emitted: it sets
        # engine="direct-anthropic" / "direct-bedrock" / "bifrost" / etc.
        # Falls back to the global engine name if no event was captured (e.g. early failure).
        from gateway.runtime import engine as get_engine
        engine_used = (governance_event or {}).get("engine") or get_engine().name

        # Generate code snippet
        base_url = str(request.base_url).rstrip("/")
        snippet = SNIPPETS.get(body.framework, SNIPPETS["openai"]).format(
            base_url=base_url,
            api_key=api_key[:8] + "..." if len(api_key) > 8 else api_key,
            model=model,
            prompt=body.prompt[:100],
            max_tokens=body.max_tokens,
        )

        # Build real explainability from DB / live state
        from gateway.routes.playground_explain import build_explainability
        try:
            explainability = await build_explainability(
                request_id=playground_request_id,
                workspace_id=body.workspace_id,
                user_id=user,
                requested_model=model,
                governance_event=governance_event,
                total_latency_ms=latency_ms,
                failure_stage=failure_stage,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("explainability_build_failed", error=str(exc))
            explainability = None

        return PlaygroundRunResponse(
            response_text=response_text,
            framework=body.framework,
            engine_used=engine_used,
            governance_event=governance_event,
            latency_ms=round(latency_ms, 1),
            error=error,
            error_category=error_category,
            error_message=error_message,
            error_details=error_details,
            failure_stage=failure_stage,
            http_status=http_status,
            trace_id=trace_id,
            code_snippet=snippet,
            tool_calls=tool_calls_list,
            explainability=explainability,
        )
    finally:
        # Restore engine if swapped
        if original_engine:
            await _swap_engine(original_engine)


@router.get("/workspaces")
async def playground_workspaces(request: Request):
    """List workspaces available for playground use.

    Filters to workspaces that have at least one alias resolving to an
    Anthropic-capable target (the demo locks to Anthropic Claude Sonnet 4.5).
    No workspaces are hidden by id/prefix.

    Returns each workspace's resolved Anthropic alias so the frontend can use
    it as the model id without hardcoding the alias name (works across local
    dev and production where the alias slug may differ).
    """
    _require_login(request)
    async with async_session() as s:
        rows = (await s.scalars(select(Workspace))).all()
    # Engine routing is GATEWAY-WIDE (identical for every workspace), so read it once
    # and report the same effective routing on each entry - the playground badge shows
    # how THIS gateway currently serves the provider (rented vs owned).
    from gateway.core.engine_routing import get_overrides
    routing = get_overrides()
    eligible: list[dict] = []
    for ws in rows:
        chat_models = ws.chat_models or {}
        # Find the first alias that has an anthropic-capable target.
        anthropic_alias = None
        for alias, targets in chat_models.items():
            if any(t.get("provider") == "anthropic" for t in (targets or [])):
                anthropic_alias = alias
                break
        if not anthropic_alias:
            continue
        eligible.append({
            "workspace_id": ws.workspace_id,
            "display_name": ws.display_name,
            "client_id": ws.client_id,
            "default_chat_alias": ws.default_chat_alias,
            "chat_models": chat_models,
            "engine_overrides": routing,  # gateway-wide routing (same for all workspaces)
            "anthropic_alias": anthropic_alias,   # the alias the playground should use
        })
    return {"workspaces": eligible}


@router.get("/models")
async def playground_models(request: Request):
    """List all models from the catalog with capability hints."""
    _require_login(request)
    from gateway.core.model_catalog import _all_rows, _row_dict
    rows = await _all_rows()
    return {"models": [_row_dict(r) for r in rows if r.enabled]}


# ─── Helpers ───

async def _effective_rpm(ws: Workspace, model: str | None) -> int | None:
    """The TIGHTEST configured RPM cap that applies to a request on this workspace.

    The chat route enforces rate limits across four scopes (user -> workspace ->
    client -> model-alias); whichever is smallest trips first. To make the
    Playground's rate-limit demo trip reliably we read all of them and return the
    minimum positive rpm, so the caller can burst just above it. Returns None when
    no rpm is configured anywhere (nothing to trip)."""
    caps: list[int] = []
    rl = ws.rate_limits or {}
    if isinstance(rl.get("rpm"), int) and rl["rpm"] > 0:
        caps.append(rl["rpm"])
    url = rl.get("user") or {}
    if isinstance(url.get("rpm"), int) and url["rpm"] > 0:
        caps.append(url["rpm"])
    quotas = ws.quotas or {}
    for alias in {model, ws.default_chat_alias}:
        if not alias:
            continue
        q = quotas.get(alias) or {}
        if isinstance(q.get("rpm"), int) and q["rpm"] > 0:
            caps.append(q["rpm"])
    if ws.client_id:
        try:
            async with async_session() as s:
                c = await s.get(Client, ws.client_id)
            crl = (c.rate_limits if c else None) or {}
            if isinstance(crl.get("rpm"), int) and crl["rpm"] > 0:
                caps.append(crl["rpm"])
        except Exception:  # noqa: BLE001
            pass
    return min(caps) if caps else None


async def _get_workspace_key(workspace_id: str) -> str | None:
    """Get or create a playground API key for a workspace.
    
    Since keys are stored as SHA-256 hashes (plaintext shown only at issue time),
    we issue a dedicated playground key and cache it in memory.
    """
    # Check memory cache first
    if workspace_id in _PLAYGROUND_KEYS:
        return _PLAYGROUND_KEYS[workspace_id]

    # Issue a new key via the same logic as the admin endpoint
    import secrets as _secrets
    from gateway.core.auth import hash_key
    from gateway.db.models import ApiKey

    raw = f"gw-playground-{_secrets.token_hex(12)}"
    async with async_session() as s:
        ws = await s.scalar(select(Workspace).where(Workspace.workspace_id == workspace_id))
        if not ws:
            return None
        s.add(ApiKey(
            workspace_id=workspace_id,
            sha256=hash_key(raw),
            prefix=raw[:14] + "...",
            roles=["member"],
        ))
        await s.commit()
    _PLAYGROUND_KEYS[workspace_id] = raw
    return raw


# In-memory cache of playground keys (workspace_id -> plaintext)
_PLAYGROUND_KEYS: dict[str, str] = {}


async def _fetch_latest_governance_event(workspace_id: str, component: str) -> dict | None:
    """Fetch the most recent request log for this workspace+component."""
    async with async_session() as s:
        row = await s.scalar(
            select(RequestLog)
            .where(RequestLog.workspace_id == workspace_id)
            .order_by(desc(RequestLog.timestamp))
            .limit(1)
        )
    if not row:
        return None
    return {
        "id": str(row.id),
        "request_id": row.request_id,
        "workspace_id": row.workspace_id,
        "client_id": row.client_id,
        "user": row.user_id,
        "component": row.component,
        "use_case": row.use_case,
        "provider": row.provider,
        "model_alias": row.model_alias,
        "model_id": row.provider_model_id,
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "total_tokens": row.input_tokens + row.output_tokens,
        "cost_usd": float(row.cost_usd) if row.cost_usd else 0,
        "latency_ms": row.latency_ms,
        "status": row.status,
        "event_kind": row.event_kind,
        "engine": row.engine,
        "stream": row.stream,
        "created_at": row.timestamp.isoformat() if row.timestamp else None,
    }


async def _swap_engine(target: str) -> str:
    """Temporarily swap the runtime engine for one playground request and return the
    previous engine name (restored by the caller). Uses the SAME canonical mapping as
    the live slot (engine_by_name), so ALL engines work here: bifrost | litellm |
    portkey | direct | echo. This is per-request only - it does NOT persist (that is
    the Engine Slot's job), so the playground can prove any engine end-to-end without
    disturbing the operator's chosen slot for long."""
    import gateway.runtime as rt
    prev = rt._engine.name if rt._engine else "bifrost"
    rt._engine = rt.engine_by_name(target)
    return prev
