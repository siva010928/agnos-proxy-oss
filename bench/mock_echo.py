"""Mock upstream - a zero-latency stand-in for Bifrost, exposing just enough of
its API surface to isolate PURE gateway overhead (no real LLM/network latency).

Run:  poetry run python bench/mock_echo.py   (listens on :8077)
Then point the gateway at it:  BIFROST_URL=http://localhost:8077
"""
from __future__ import annotations

import json
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
_KEYS: dict[str, list] = {}


@app.post("/api/providers")
async def create_provider(request: Request):
    return {"ok": True}


@app.get("/api/providers/{provider}/keys")
async def list_keys(provider: str):
    return {"keys": _KEYS.get(provider, [])}


@app.post("/api/providers/{provider}/keys")
async def add_key(provider: str, request: Request):
    body = await request.json()
    kid = str(uuid.uuid4())
    _KEYS.setdefault(provider, []).append({"name": body.get("name"), "id": kid})
    return {"id": kid, "name": body.get("name")}


@app.get("/api/providers")
async def providers():
    return {"providers": [], "total": 0}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model", "mock")
    # fail-trigger: any model id containing "fail" returns an upstream error
    # (used to benchmark fallback-added latency)
    if "fail" in model:
        return JSONResponse(status_code=503,
                            content={"is_bifrost_error": True,
                                     "error": {"type": "upstream_error", "message": "mock forced failure"}})
    if body.get("stream"):
        async def gen():
            cid = f"chatcmpl-mock-{uuid.uuid4().hex[:8]}"
            for tok in ["OK", " one", " two", " three"]:
                chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                         "model": model, "choices": [{"index": 0, "delta": {"content": tok}}]}
                yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return {
        "id": f"chatcmpl-mock-{uuid.uuid4().hex[:8]}", "object": "chat.completion",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "OK"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
    }


@app.post("/v1/embeddings")
async def emb(request: Request):
    return {"data": [{"index": 0, "object": "embedding", "embedding": [0.0] * 8}],
            "model": "mock", "object": "list", "usage": {"prompt_tokens": 2, "total_tokens": 2}}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8077, log_level="warning")
