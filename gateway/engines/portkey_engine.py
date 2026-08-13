"""PortkeyEngine - a STATELESS commodity translator behind our governed boundary.

Portkey's open-source AI Gateway (``@portkey-ai/gateway``, MIT) is a pure,
stateless OpenAI-compatible translator: it stores NO credentials. The provider
key is supplied *per request* in headers. So our boundary decrypts the provider
credential from OUR vault and injects it into the Portkey call for this one
request - Portkey holds nothing at rest.

Security consequence (the whole point of the stateless slot): a compromise of this engine
exposes only the traffic in flight during the compromise window - there is NO
key vault inside it to dump. Contrast BifrostEngine / LiteLLMEngine, which store
the provider keys themselves.

Wire (verified against the Portkey source):
  * ``x-portkey-provider: <slug>`` selects the upstream provider
  * key providers (anthropic/openai/google) → ``Authorization: Bearer <key>``
  * bedrock → ``x-portkey-aws-access-key-id`` / ``-aws-secret-access-key`` /
    ``-aws-region`` (+ optional ``-aws-session-token``)
  * the OpenAI ``model`` field carries the provider-native model id

This file is deliberately self-contained: it references NO Bifrost surface, so
the anti-coupling audit stays green.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from gateway.config import settings
from gateway.core.registry import ResolvedTarget
from gateway.engines.base import BackendEngine, EngineResult

# our provider name → Portkey provider slug
_PROVIDER_SLUG = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "google",
    "google_genai": "google",
    "bedrock": "bedrock",
    "azure": "azure-openai",
}


class PortkeyEngine(BackendEngine):
    name = "portkey"

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.portkey_url).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(settings.max_request_timeout_s), connect=10.0, write=30.0),
            limits=httpx.Limits(max_keepalive_connections=50, max_connections=200))

    def _slug(self, target: ResolvedTarget) -> str:
        return _PROVIDER_SLUG.get(target.provider, target.provider)

    def _headers(self, target: ResolvedTarget) -> dict[str, str]:
        """Inject THIS request's provider credential (from our decrypted vault)
        into Portkey headers. Nothing is persisted in Portkey."""
        creds: dict[str, Any] = target.credentials or {}
        cfg: dict[str, Any] = target.config or {}
        h: dict[str, str] = {"Content-Type": "application/json",
                             "x-portkey-provider": self._slug(target)}
        provider = target.provider
        if provider == "bedrock":
            access = creds.get("access_key") or creds.get("aws_access_key_id") or ""
            secret = creds.get("secret_key") or creds.get("aws_secret_access_key") or ""
            region = (creds.get("region") or cfg.get("region")
                      or target.region or settings.aws_region_name)
            if access:
                h["x-portkey-aws-access-key-id"] = access
            if secret:
                h["x-portkey-aws-secret-access-key"] = secret
            if region:
                h["x-portkey-aws-region"] = region
            token = creds.get("session_token") or creds.get("aws_session_token")
            if token:
                h["x-portkey-aws-session-token"] = token
        elif provider in ("azure", ):
            api_key = creds.get("api_key", "")
            if api_key:
                h["Authorization"] = f"Bearer {api_key}"
            # Azure needs resource + deployment; pass through when configured.
            resource = cfg.get("resource_name") or cfg.get("azure_resource_name")
            deployment = cfg.get("deployment_id") or cfg.get("azure_deployment_id")
            api_version = cfg.get("api_version") or target.api_version
            if resource:
                h["x-portkey-azure-resource-name"] = str(resource)
            if deployment:
                h["x-portkey-azure-deployment-id"] = str(deployment)
            if api_version:
                h["x-portkey-azure-api-version"] = str(api_version)
        else:
            # anthropic / openai / gemini(google) and any other key-based provider
            api_key = creds.get("api_key", "")
            if api_key:
                h["Authorization"] = f"Bearer {api_key}"
        return h

    def _payload(self, openai_request: dict, target: ResolvedTarget) -> dict:
        body = dict(openai_request)
        body["model"] = target.model_id
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
        opts = dict(body.get("stream_options") or {})
        opts["include_usage"] = True
        body["stream_options"] = opts
        async with self._client.stream("POST", f"{self.base_url}/v1/chat/completions",
                                       headers=self._headers(target), json=body) as resp:
            async for line in resp.aiter_lines():
                if line:
                    yield (line + "\n\n").encode()

    async def embeddings(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        body = dict(openai_request)
        body["model"] = target.model_id
        r = await self._client.post(f"{self.base_url}/v1/embeddings",
                                    headers=self._headers(target), json=body)
        try:
            data = r.json()
        except Exception:
            data = {"error": {"message": r.text, "type": "upstream_error"}}
        return EngineResult(body=data, status_code=r.status_code)

    async def healthcheck(self) -> bool:
        try:
            # Portkey serves its console/root; any HTTP answer means the process is up.
            r = await self._client.get(self.base_url, timeout=5)
            return r.status_code < 500
        except Exception:
            return False
