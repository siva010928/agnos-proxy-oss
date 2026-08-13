"""Pass-through escape hatch: /engine/{path} forwards raw to Bifrost with
auth+logging. Strictly scoped to the **OpenAI-compatible wire only** so this
escape hatch never becomes a back door into Bifrost's native API surface
(governance/customers/teams/virtualkeys/routing/guardrails/prompts/mcp/
telemetry - all of which Agnos owns in our own layer).

Covers the long tail (new OpenAI endpoints like /v1/responses) without the
gateway hardcoding every route. Auth is enforced; body forwarded verbatim.
"""
from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from gateway.config import settings
from gateway.core.auth import resolve_principal

router = APIRouter()

# Only OpenAI-wire-compatible paths may be forwarded. This is the
# anti-coupling rule: Agnos talks to Bifrost via OpenAI HTTP only.
_ALLOWED_PATH = re.compile(r"^v1/[A-Za-z0-9_\-/]+$")


@router.api_route("/engine/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def passthrough(path: str, request: Request):
    await resolve_principal(request.headers.get("authorization"), request.headers)  # enforce auth

    if not _ALLOWED_PATH.match(path):
        # Bifrost-native paths (and anything else) are explicitly OUT OF SCOPE.
        # Agnos owns governance/routing/guardrails/etc. in its own layer.
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "type": "passthrough_path_forbidden",
                    "message": (
                        f"/engine/{path} is not an OpenAI-compatible path. "
                        f"The /engine/* escape hatch is restricted to /v1/* "
                        f"endpoints only; Agnos owns all governance / routing "
                        f"/ guardrails / telemetry features in its own layer "
                        f"and does not delegate them to Bifrost."
                    ),
                }
            },
        )

    url = f"{settings.bifrost_url}/{path}"
    raw = await request.body()
    fwd_headers = {"Content-Type": request.headers.get("content-type", "application/json")}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.request(request.method, url, content=raw, headers=fwd_headers,
                            params=dict(request.query_params))
    media = r.headers.get("content-type", "application/json")
    return Response(content=r.content, status_code=r.status_code, media_type=media)
