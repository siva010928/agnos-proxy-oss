"""DirectEngine adapter for Google Vertex AI (service-account auth).

Vertex exposes an OpenAI-compatible endpoint; we mint a short-lived access token
from the service-account JSON (google-auth) and forward the OpenAI request. This
keeps Vertex on the SAME OpenAI contract as every other provider - our own engine
owns the auth + call end-to-end.

Credential shape:
  credentials.api_key  = the service-account JSON (raw JSON string OR a file path)
  config.vertex_project  (required)   · config.vertex_location  (default us-central1)
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import httpx

from gateway.core.registry import ResolvedTarget
from gateway.engines.base import EngineResult

_SCOPE = ["https://www.googleapis.com/auth/cloud-platform"]
_token_cache: dict[int, tuple[str, float]] = {}


def _load_credentials(raw: str):
    """Service-account JSON (raw string) or a file path to it. The gateway requires
    the admin to PROVIDE the credential - it never falls back to ambient/Application
    Default Credentials (same isolation principle as the bedrock Test Connection)."""
    from google.oauth2 import service_account
    raw = (raw or "").strip()
    if raw.startswith("{"):
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=_SCOPE)
    return service_account.Credentials.from_service_account_file(raw, scopes=_SCOPE)


def _access_token(raw: str) -> str:
    import google.auth.transport.requests
    sig = hash(raw)
    cached = _token_cache.get(sig)
    now = time.time()
    if cached and cached[1] - 60 > now:
        return cached[0]
    creds = _load_credentials(raw)
    creds.refresh(google.auth.transport.requests.Request())
    exp = creds.expiry.timestamp() if getattr(creds, "expiry", None) else now + 3000
    _token_cache[sig] = (creds.token, exp)
    return creds.token


def _project_location(target: ResolvedTarget) -> tuple[str, str]:
    cfg = target.config or {}
    project = cfg.get("vertex_project") or cfg.get("vertex-ai-project") or ""
    location = cfg.get("vertex_location") or cfg.get("vertex-ai-location") or "us-central1"
    return project, location


def _base(project: str, location: str) -> str:
    return (f"https://{location}-aiplatform.googleapis.com/v1beta1/projects/"
            f"{project}/locations/{location}/endpoints/openapi")


def _model(model_id: str) -> str:
    # Vertex's OpenAI endpoint expects a publisher-prefixed model (e.g. google/gemini-2.5-flash).
    return model_id if "/" in model_id else f"google/{model_id}"


async def _prepare(target: ResolvedTarget) -> tuple[str | None, str | None, str | None]:
    """Return (base_url, token, error). error is set on misconfig/auth failure."""
    project, location = _project_location(target)
    if not project:
        return None, None, "vertex_ai requires 'vertex_project' in the provider config."
    sa = (target.credentials or {}).get("api_key") or ""
    if not sa:
        return None, None, "vertex_ai requires the service-account JSON in credentials (admin-provided; no ambient ADC)."
    try:
        token = await asyncio.to_thread(_access_token, sa)
    except Exception as exc:  # noqa: BLE001
        return None, None, f"vertex service-account auth failed: {exc}"
    return _base(project, location), token, None


def _parse(r) -> dict:
    """Parse a Vertex response body to a dict. Google APIs sometimes return a JSON
    ARRAY (e.g. [{"error": {...}}]) - normalize it so the OpenAI-shaped contract
    (and the fallback error reader) always sees a dict."""
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return {"error": {"message": r.text[:500] or "non-JSON response", "type": "provider_error"}}
    if isinstance(data, list):
        first = data[0] if data and isinstance(data[0], dict) else None
        if first and "error" in first:
            return {"error": first["error"] if isinstance(first["error"], dict)
                    else {"message": str(first["error"])[:400], "type": "provider_error"}}
        return {"error": {"message": str(data)[:400], "type": "provider_error"}}
    return data


def _body(openai_request: dict, target: ResolvedTarget, *, stream: bool = False) -> dict:
    body = dict(openai_request or {})
    body["model"] = _model(target.model_id)
    if stream:
        body["stream"] = True
    return body


async def vertex_chat(openai_request: dict, target: ResolvedTarget) -> EngineResult:
    base, token, err = await _prepare(target)
    if err:
        return EngineResult({"error": {"message": err, "type": "invalid_request_error"}}, 400)
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(f"{base}/chat/completions",
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                             json=_body(openai_request, target))
    except httpx.HTTPError as exc:
        return EngineResult({"error": {"message": f"vertex connection failed: {exc}", "type": "api_connection_error"}}, 502)
    return EngineResult(_parse(r), r.status_code)


async def vertex_embeddings(openai_request: dict, target: ResolvedTarget) -> EngineResult:
    base, token, err = await _prepare(target)
    if err:
        return EngineResult({"error": {"message": err, "type": "invalid_request_error"}}, 400)
    body = _body(openai_request, target)
    # Vertex's OpenAI endpoint only supports encoding_format='float' (the OpenAI SDK
    # defaults to 'base64', which Vertex rejects with a 400).
    body["encoding_format"] = "float"
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(f"{base}/embeddings",
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                             json=body)
    except httpx.HTTPError as exc:
        return EngineResult({"error": {"message": f"vertex connection failed: {exc}", "type": "api_connection_error"}}, 502)
    return EngineResult(_parse(r), r.status_code)


async def vertex_chat_stream(openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
    base, token, err = await _prepare(target)
    if err:
        yield f"data: {json.dumps({'error': {'message': err, 'type': 'invalid_request_error'}})}\n\n".encode()
        yield b"data: [DONE]\n\n"
        return
    try:
        async with httpx.AsyncClient(timeout=300.0) as c:
            async with c.stream("POST", f"{base}/chat/completions",
                                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                json=_body(openai_request, target, stream=True)) as r:
                if r.status_code != 200:
                    raw = await r.aread()
                    yield f"data: {json.dumps({'error': {'message': raw.decode(errors='ignore')[:500], 'type': 'provider_error'}})}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    return
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk
    except httpx.HTTPError as exc:
        yield f"data: {json.dumps({'error': {'message': f'vertex stream failed: {exc}', 'type': 'api_connection_error'}})}\n\n".encode()
        yield b"data: [DONE]\n\n"
