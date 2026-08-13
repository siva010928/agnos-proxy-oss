"""POST /v1/utils/count_tokens - pre-flight estimate (tiktoken; approx for non-OpenAI)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from gateway.core.auth import resolve_principal
from gateway.core.tokens import count_message_tokens

router = APIRouter()


@router.post("/v1/utils/count_tokens")
async def count_tokens(request: Request):
    body = await request.json()
    await resolve_principal(request.headers.get("authorization"), request.headers)
    n = count_message_tokens(body.get("messages", []))
    return {"estimated_tokens": n, "method": "tiktoken/cl100k_base",
            "note": "Estimate. Exact counts are provider-specific for non-OpenAI models."}
