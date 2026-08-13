"""LiteLLMEngine - STATELESS translator via LiteLLM's Clientside Credentials.

We never sync or store keys in the LiteLLM proxy. On each request we pass the
provider credential decrypted from OUR vault as request-body params (LiteLLM
"Clientside LLM Credentials": the proxy declares
``configurable_clientside_auth_params`` on per-provider wildcard models and NO
stored key). The proxy runs with spend + message logging disabled, so it holds
and records nothing.

Security consequence: like PortkeyEngine/BifrostEngine/DirectEngine, a compromise
of this engine exposes only in-flight traffic - there is NO key store to dump.

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

# our provider name -> LiteLLM provider prefix
_LITELLM_PROVIDER = {"google_genai": "gemini"}


class LiteLLMEngine(BackendEngine):
    name = "litellm"

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.litellm_engine_url).rstrip("/")
        self.api_key = api_key or settings.litellm_engine_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(settings.max_request_timeout_s), connect=10.0, write=30.0),
            limits=httpx.Limits(max_keepalive_connections=50, max_connections=200))

    def _headers(self) -> dict[str, str]:
        # The proxy authenticates us with its master/virtual key ONLY; the provider
        # credential travels per request in the body (clientside credentials).
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"}

    def _model(self, target: ResolvedTarget) -> str:
        provider = _LITELLM_PROVIDER.get(target.provider, target.provider)
        return f"{provider}/{target.model_id}"

    def _cred_params(self, target: ResolvedTarget) -> dict[str, Any]:
        """THIS request's provider credential, from OUR vault, as LiteLLM clientside
        params merged into the request body. Nothing is stored in the proxy."""
        creds: dict[str, Any] = target.credentials or {}
        cfg: dict[str, Any] = target.config or {}
        p = target.provider
        if p == "bedrock":
            out = {
                "aws_access_key_id": creds.get("access_key") or creds.get("aws_access_key_id") or settings.aws_access_key_id,
                "aws_secret_access_key": creds.get("secret_key") or creds.get("aws_secret_access_key") or settings.aws_secret_access_key,
                "aws_region_name": creds.get("region") or cfg.get("region") or settings.aws_region_name,
            }
            tok = creds.get("session_token") or creds.get("aws_session_token")
            if tok:
                out["aws_session_token"] = tok
            return {k: v for k, v in out.items() if v}
        if p == "azure":
            return {k: v for k, v in {
                "api_key": creds.get("api_key", ""),
                "api_base": cfg.get("endpoint") or creds.get("endpoint") or settings.azure_openai_endpoint,
                "api_version": cfg.get("api_version") or settings.azure_openai_api_version,
            }.items() if v}
        # anthropic | openai | gemini (and other key-based providers)
        out = {"api_key": creds.get("api_key", "")}
        if cfg.get("base_url"):
            out["api_base"] = cfg["base_url"]
        return {k: v for k, v in out.items() if v}

    def _payload(self, openai_request: dict, target: ResolvedTarget) -> dict:
        body = dict(openai_request)
        body["model"] = self._model(target)
        body.pop("stream", None)
        body.update(self._cred_params(target))
        return body

    async def chat(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        body = self._payload(openai_request, target)
        r = await self._client.post(f"{self.base_url}/v1/chat/completions",
                                    headers=self._headers(), json=body)
        try:
            data = r.json()
        except Exception:
            data = {"error": {"message": r.text, "type": "upstream_error"}}
        return EngineResult(body=data, status_code=r.status_code)

    async def chat_stream(self, openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
        body = self._payload(openai_request, target)
        body["stream"] = True
        opts = dict(body.get("stream_options") or {})
        opts["include_usage"] = True
        body["stream_options"] = opts
        async with self._client.stream("POST", f"{self.base_url}/v1/chat/completions",
                                       headers=self._headers(), json=body) as resp:
            async for line in resp.aiter_lines():
                if line:
                    yield (line + "\n\n").encode()

    async def embeddings(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        body = dict(openai_request)
        body["model"] = self._model(target)
        body.update(self._cred_params(target))
        r = await self._client.post(f"{self.base_url}/v1/embeddings",
                                    headers=self._headers(), json=body)
        try:
            data = r.json()
        except Exception:
            data = {"error": {"message": r.text, "type": "upstream_error"}}
        return EngineResult(body=data, status_code=r.status_code)

    async def healthcheck(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/health/liveliness", timeout=5)
            if r.status_code < 500:
                return True
            r = await self._client.get(self.base_url, timeout=5)
            return r.status_code < 500
        except Exception:
            return False
