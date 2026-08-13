"""GET /v1/models (OpenAI-shaped) + GET /v1/routing/resolve (introspection)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

from gateway.core.auth import resolve_principal

router = APIRouter()


@router.get("/v1/models")
async def list_models(request: Request):
    ws = await resolve_principal(request.headers.get("authorization"), request.headers)
    now = int(time.time())
    data = []
    for alias, spec in {**ws.chat_models, **ws.embedding_models}.items():
        s0 = spec[0] if isinstance(spec, list) else spec
        data.append({"id": alias, "object": "model", "created": now,
                     "owned_by": s0.get("provider", "agnos-proxy-gateway")})
    return {"object": "list", "data": data}


@router.get("/v1/routing/resolve")
async def routing_resolve(request: Request, alias: str | None = None, component: str | None = None):
    """Non-secret introspection: 'what will the gateway do with my request?'

    Returns the resolved target(s) for the caller's workspace+component - provider,
    model alias→model_id, region, and a guardrail/quota summary. No credentials.
    Component precedence: ?component= → X-Gateway-Component → JWT claim.
    """
    # honor an explicit ?component= override by injecting it as the header source
    headers = dict(request.headers)
    if component:
        headers["x-gateway-component"] = component
    ws = await resolve_principal(request.headers.get("authorization"), headers)

    chosen = alias or ws.default_chat_alias
    aliases = {}
    for a, spec in (ws.chat_models or {}).items():
        targets = spec if isinstance(spec, list) else [spec]
        aliases[a] = [{"provider": t.get("provider"), "model_id": t.get("model_id"),
                       "context_window": t.get("context_window"),
                       "role": "primary" if i == 0 else "fallback"}
                      for i, t in enumerate(targets)]
    g = ws.guardrails or {}
    return {
        "workspace_id": ws.workspace_id,
        "component": ws.component,
        "auth_method": ws.auth_method,
        "user_id": ws.user_id,
        "default_chat_alias": ws.default_chat_alias,
        "resolved_alias": chosen,
        "resolved_targets": aliases.get(chosen or "", []),
        "aliases": aliases,
        "embedding_models": ws.embedding_models,
        "guardrails_summary": {"detectors": [k for k in ("pii_detection", "secrets_detection",
                                                          "keywords", "presidio") if g.get(k)],
                               "mode": g.get("mode")},
        "quotas": ws.quotas,
        "budget": ws.budgets,
    }
