"""POST /v1/batch/completions - pseudo-batch (async fan-out + concurrency limit).

NOT OpenAI's 24h /v1/batches. This multiplexes N chat requests in parallel
through the same governed pipeline. Honest about not being provider batch pricing.
"""
from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/v1/batch/completions")
async def batch_completions(request: Request):
    body = await request.json()
    reqs = body.get("requests")
    if not isinstance(reqs, list) or not reqs:
        return {"error": {"message": "'requests' must be a non-empty list.",
                          "type": "invalid_request_error"}}
    max_conc = int(body.get("max_concurrency", 5))
    sem = asyncio.Semaphore(max_conc)
    base = str(request.base_url).rstrip("/")
    started = time.perf_counter()

    # Forward auth + every X-Gateway-* header so per-item governance works
    # (required-header enforcement, component attribution, guardrail-mode
    # override, idempotency keys, etc). We deliberately don't forward
    # Content-Length/host/etc \u2014 httpx sets those.
    fwd: dict[str, str] = {}
    auth = request.headers.get("authorization")
    if auth:
        fwd["Authorization"] = auth
    for k, v in request.headers.items():
        if k.lower().startswith("x-gateway-"):
            fwd[k] = v
    fwd.setdefault("Content-Type", "application/json")

    async def one(rb: dict):
        async with sem:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(f"{base}/v1/chat/completions",
                                 headers=fwd, json=rb)
                try:
                    return r.json()
                except Exception:
                    return {"error": {"message": r.text}}

    results = await asyncio.gather(*[one(r) for r in reqs], return_exceptions=True)
    return {"results": [r if not isinstance(r, Exception) else {"error": {"message": str(r)}}
                        for r in results],
            "stats": {"count": len(reqs), "concurrency": max_conc,
                      "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}}
