"""BifrostEngine - STATELESS translator via Bifrost's Direct API Key.

We never register or store a key in Bifrost. On each request we inject the
provider credential decrypted from OUR vault, using Bifrost's `x-bf-direct-key`
mechanism (docs.getbifrost.ai/providers/request-options): send
``x-bf-direct-key: true`` plus the raw key in the provider's native auth header.
Bifrost then bypasses its key pool entirely and holds nothing at rest. Requires
``allow_direct_keys: true`` in the Bifrost client config (set once, server-side).

Security consequence: a compromise of Bifrost exposes only the traffic in flight
during the window - there is NO key store inside it to dump. Same stateless
property as PortkeyEngine and DirectEngine.

Self-contained: references NO other engine's surface (anti-coupling stays green).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from gateway.config import settings
from gateway.core.registry import ResolvedTarget
from gateway.engines.base import BackendEngine, EngineResult


class BifrostEngine(BackendEngine):
    name = "bifrost"

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.bifrost_url).rstrip("/")
        # One pooled client; READ timeout tracks the configured ceiling (long jobs),
        # connect/write stay short so a dead Bifrost fails fast. The real per-request
        # deadline is enforced upstream by fallback.execute (asyncio.wait_for).
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(settings.max_request_timeout_s), connect=10.0, write=30.0),
            limits=httpx.Limits(max_keepalive_connections=50, max_connections=200))

    def _bifrost_model(self, target: ResolvedTarget) -> str:
        provider = {"google_genai": "gemini"}.get(target.provider, target.provider)
        return f"{provider}/{target.model_id}"

    def _headers(self, target: ResolvedTarget) -> dict[str, str]:
        """Inject THIS request's provider credential via Bifrost's direct-key path
        (allow_direct_keys must be enabled server-side). Nothing is persisted."""
        creds: dict[str, Any] = target.credentials or {}
        h: dict[str, str] = {"Content-Type": "application/json", "x-bf-direct-key": "true"}
        provider = target.provider
        if provider == "bedrock":
            # A Bedrock API key (bearer) rides in Authorization. Multi-part SigV4
            # (access/secret) cannot fit one header, so the stateless path uses the
            # Bedrock bearer key (from the vault, else the process-level setting).
            bearer = (creds.get("bedrock_api_key") or creds.get("api_key")
                      or settings.aws_bedrock_api_key or "")
            if bearer:
                h["Authorization"] = f"Bearer {bearer}"
        elif provider in ("gemini", "google_genai"):
            key = creds.get("api_key", "")
            if key:
                h["x-goog-api-key"] = key
        elif provider == "anthropic":
            key = creds.get("api_key", "")
            if key:
                h["x-api-key"] = key
        else:
            # openai / azure / any other bearer-key provider
            key = creds.get("api_key", "")
            if key:
                h["Authorization"] = f"Bearer {key}"
        return h

    def _payload(self, openai_request: dict, target: ResolvedTarget) -> dict:
        body = dict(openai_request)
        body["model"] = self._bifrost_model(target)
        body.pop("stream", None)
        return body

    async def chat(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        body = self._payload(openai_request, target)
        r = await self._client.post(f"{self.base_url}/v1/chat/completions",
                                    headers=self._headers(target), json=body)
        try:
            data = r.json()
        except Exception:
            data = {"error": {"message": r.text, "type": "upstream_error"}}
        return EngineResult(body=data, status_code=r.status_code)

    async def chat_stream(self, openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
        body = self._payload(openai_request, target)
        body["stream"] = True
        # Ask for a final usage chunk so streamed turns record real token counts.
        opts = dict(body.get("stream_options") or {})
        opts["include_usage"] = True
        body["stream_options"] = opts
        async with self._client.stream("POST", f"{self.base_url}/v1/chat/completions",
                                       headers=self._headers(target), json=body) as resp:
            async for line in resp.aiter_lines():
                if line:
                    yield (self._clean_sse_line(line) + "\n\n").encode()

    @staticmethod
    def _clean_sse_line(line: str) -> str:
        r"""Strip Bifrost-isms (extra_fields / bifrost_config) from an SSE ``data: {...}``
        chunk so only clean OpenAI-shaped JSON crosses the boundary."""
        if not line.startswith("data: ") or "[DONE]" in line:
            return line
        payload = line[6:].strip()
        try:
            obj = json.loads(payload)
        except Exception:
            return line
        if isinstance(obj, dict) and ("extra_fields" in obj or "bifrost_config" in obj):
            obj.pop("extra_fields", None)
            obj.pop("bifrost_config", None)
            return "data: " + json.dumps(obj, separators=(",", ":"))
        return line

    async def embeddings(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        body = dict(openai_request)
        body["model"] = self._bifrost_model(target)
        r = await self._client.post(f"{self.base_url}/v1/embeddings",
                                    headers=self._headers(target), json=body)
        try:
            data = r.json()
        except Exception:
            data = {"error": {"message": r.text, "type": "upstream_error"}}
        return EngineResult(body=data, status_code=r.status_code)

    async def healthcheck(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/api/providers", timeout=5)
            return r.status_code == 200
        except Exception:
            return False
