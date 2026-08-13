"""DirectEngine adapter for OpenAI-COMPATIBLE providers.

openai, litellm_proxy, ollama, hosted_vllm / lm-studio all speak the OpenAI
``/v1`` wire, so this adapter forwards the (boundary-cleaned) request verbatim to
the provider's base_url and returns the response as-is - no translation needed.

That is exactly what makes these providers Bifrost-independent: our OWN engine
owns the call end-to-end (auth + HTTP), so they work whether or not the rented
Bifrost sidecar recognizes the provider name.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from gateway.core.registry import ResolvedTarget
from gateway.engines.base import EngineResult

# Default base_url per provider when the operator didn't set one (config.base_url).
# litellm_proxy has no default - the proxy URL must be supplied.
_DEFAULT_BASE: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "litellm_proxy": "",
    "ollama": "http://localhost:11434/v1",
    "hosted_vllm": "http://127.0.0.1:1234/v1",
    "lm-studio": "http://127.0.0.1:1234/v1",
}

def _base_url(target: ResolvedTarget) -> str:
    cfg = target.config or {}
    return (getattr(target, "base_url", None) or cfg.get("base_url")
            or _DEFAULT_BASE.get(target.provider, "")).rstrip("/")


def _api_key(target: ResolvedTarget) -> str:
    return (target.credentials or {}).get("api_key") or ""


def _clean_body(openai_request: dict, target: ResolvedTarget, *, stream: bool = False) -> dict:
    # The incoming component request is already pure OpenAI (no engine-internal
    # annotations - those are response-side). Forward it with the resolved model.
    body = dict(openai_request or {})
    body["model"] = target.model_id
    if stream:
        body["stream"] = True
    return body


def _headers(target: ResolvedTarget) -> dict:
    h = {"Content-Type": "application/json"}
    key = _api_key(target)
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _timeout(target: ResolvedTarget, default: float) -> float:
    try:
        return max(1.0, float((target.config or {}).get("request_timeout_seconds") or default))
    except (TypeError, ValueError):
        return default


async def _forward(path: str, openai_request: dict, target: ResolvedTarget) -> EngineResult:
    base = _base_url(target)
    if not base:
        return EngineResult({"error": {
            "message": f"provider '{target.provider}' requires a base_url in its provider config.",
            "type": "invalid_request_error", "code": "missing_base_url"}}, 400)
    try:
        async with httpx.AsyncClient(timeout=_timeout(target, 120.0)) as c:
            r = await c.post(f"{base}{path}", headers=_headers(target),
                             json=_clean_body(openai_request, target))
    except httpx.HTTPError as exc:
        return EngineResult({"error": {"message": f"connection to {base} failed: {exc}",
                                       "type": "api_connection_error"}}, 502)
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = {"error": {"message": r.text[:500] or "non-JSON response", "type": "provider_error"}}
    return EngineResult(data, r.status_code)


async def openai_compat_chat(openai_request: dict, target: ResolvedTarget) -> EngineResult:
    return await _forward("/chat/completions", openai_request, target)


async def openai_compat_embeddings(openai_request: dict, target: ResolvedTarget) -> EngineResult:
    return await _forward("/embeddings", openai_request, target)


async def openai_compat_chat_stream(openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
    base = _base_url(target)
    if not base:
        err = {"error": {"message": f"provider '{target.provider}' requires a base_url.",
                         "type": "invalid_request_error"}}
        yield f"data: {json.dumps(err)}\n\n".encode()
        yield b"data: [DONE]\n\n"
        return
    try:
        async with httpx.AsyncClient(timeout=_timeout(target, 300.0)) as c:
            async with c.stream("POST", f"{base}/chat/completions", headers=_headers(target),
                                json=_clean_body(openai_request, target, stream=True)) as r:
                if r.status_code != 200:
                    raw = await r.aread()
                    err = {"error": {"message": raw.decode(errors="ignore")[:500] or f"HTTP {r.status_code}",
                                     "type": "provider_error"}}
                    yield f"data: {json.dumps(err)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    return
                # Forward the OpenAI SSE bytes verbatim (already data: {...}\n\n framed).
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk
    except httpx.HTTPError as exc:
        err = {"error": {"message": f"stream to {base} failed: {exc}", "type": "api_connection_error"}}
        yield f"data: {json.dumps(err)}\n\n".encode()
        yield b"data: [DONE]\n\n"
