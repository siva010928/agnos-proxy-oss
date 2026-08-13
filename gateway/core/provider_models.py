"""List the models an account can ACTUALLY reach, per provider.

A configured account (bedrock via sso/key/static keys, an OpenAI key, a Gemini
key, a local Ollama, ...) can usually reach only a subset of the global catalog.
This module queries the live account with ONLY the supplied credentials (same
isolation as provider_test) so the admin UI can restrict alias/model selection to
what's genuinely accessible - no more picking a model the account can't call.

Returns {"ok": bool, "models": [ids...], "count": N, "error": str|None}.
"""
from __future__ import annotations

import asyncio

import httpx


async def list_available_models(provider: str, creds: dict, config: dict) -> dict:
    creds = creds or {}
    config = config or {}
    try:
        if provider == "bedrock":
            models = await _bedrock_models(creds, config)
        elif provider in ("gemini", "google_genai"):
            models = await _gemini_models(creds)
        elif provider == "anthropic":
            models = await _anthropic_models(creds)
        elif provider == "azure":
            models = await _azure_models(creds, config)
        elif provider in ("openai", "litellm_proxy", "ollama", "hosted_vllm", "lm-studio"):
            models = await _openai_like_models(provider, creds, config)
        else:
            return {"ok": False, "error": f"model listing not supported for '{provider}'", "models": [], "count": 0}
        uniq = sorted({m for m in models if m})
        return {"ok": True, "models": uniq, "count": len(uniq), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300], "models": [], "count": 0}


# ── bedrock: cross-region inference profiles + on-demand foundation models ──
def _bedrock_auth(creds: dict, config: dict) -> str:
    at = (creds.get("auth_type") or config.get("auth_type") or "").lower()
    if at:
        return at
    if (creds.get("bedrock_api_key") or "").strip():
        return "api-key"
    if (creds.get("profile_name") or config.get("profile_name") or "").strip():
        return "sso"
    return "static"


def _bedrock_session(creds: dict, config: dict, region: str):
    import os
    import boto3
    from botocore.session import Session as BotocoreSession
    auth = _bedrock_auth(creds, config)
    profile = creds.get("profile_name") or config.get("profile_name")
    bearer = creds.get("bedrock_api_key") or creds.get("api_key")
    if auth == "sso" and profile:
        return boto3.Session(profile_name=profile, region_name=region)
    if auth in ("api-key", "api_key", "bearer") and bearer:
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = bearer
        bsess = BotocoreSession()
        bsess.set_config_variable("credentials_file", "/dev/null")
        bsess.set_config_variable("config_file", "/dev/null")
        return boto3.Session(botocore_session=bsess, region_name=region)
    bsess = BotocoreSession()
    bsess.set_config_variable("credentials_file", "/dev/null")
    bsess.set_config_variable("config_file", "/dev/null")
    bsess.set_credentials(creds.get("access_key", ""), creds.get("secret_key", ""),
                          creds.get("session_token") or None)
    return boto3.Session(botocore_session=bsess, region_name=region)


async def _bedrock_models(creds: dict, config: dict) -> list[str]:
    region = (creds.get("region") or config.get("region") or "us-east-1")

    def _call() -> list[str]:
        sess = _bedrock_session(creds, config, region)
        out: list[str] = []
        # cross-region inference profiles (us.anthropic.*, eu.*, ...) - the ids you
        # actually call for newer Anthropic models.
        try:
            bedrock = sess.client("bedrock")
            resp = bedrock.list_inference_profiles(maxResults=1000)
            for p in resp.get("inferenceProfileSummaries", []):
                pid = p.get("inferenceProfileId")
                if pid:
                    out.append(pid)
            # on-demand foundation models (bare ids)
            fm = bedrock.list_foundation_models()
            for m in fm.get("modelSummaries", []):
                mid = m.get("modelId")
                inf = m.get("inferenceTypesSupported") or []
                if mid and ("ON_DEMAND" in inf or not inf):
                    out.append(mid)
        except Exception:  # noqa: BLE001
            # bearer/guardrail-only principals can't list; fall back to empty (the
            # UI then allows free-typing).
            pass
        return out

    return await asyncio.to_thread(_call)


# ── gemini / google_genai (AI Studio) ──
async def _gemini_models(creds: dict) -> list[str]:
    key = creds.get("api_key", "")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://generativelanguage.googleapis.com/v1beta/models",
                        params={"key": key, "pageSize": 1000})
    if r.status_code != 200:
        raise RuntimeError(f"AI Studio /models HTTP {r.status_code}: {r.text[:160]}")
    out = []
    for m in r.json().get("models", []):
        name = (m.get("name") or "").replace("models/", "")
        methods = m.get("supportedGenerationMethods") or []
        if name and ("generateContent" in methods or "embedContent" in methods or not methods):
            out.append(name)
    return out


# ── anthropic ──
async def _anthropic_models(creds: dict) -> list[str]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://api.anthropic.com/v1/models",
                        headers={"x-api-key": creds.get("api_key", ""), "anthropic-version": "2023-06-01"})
    if r.status_code != 200:
        raise RuntimeError(f"anthropic /models HTTP {r.status_code}: {r.text[:160]}")
    return [m.get("id") for m in r.json().get("data", []) if m.get("id")]


# ── azure: deployments ──
async def _azure_models(creds: dict, config: dict) -> list[str]:
    endpoint = (config.get("endpoint") or creds.get("endpoint") or "").rstrip("/")
    api_version = config.get("api_version") or creds.get("api_version") or "2024-10-21"
    if not endpoint:
        raise RuntimeError("azure endpoint is required to list deployments")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{endpoint}/openai/deployments", params={"api-version": api_version},
                        headers={"api-key": creds.get("api_key", "")})
    if r.status_code != 200:
        raise RuntimeError(f"azure deployments HTTP {r.status_code}: {r.text[:160]}")
    return [d.get("id") or d.get("model") for d in r.json().get("data", []) if (d.get("id") or d.get("model"))]


# ── OpenAI-compatible (openai / litellm_proxy / ollama / hosted_vllm / lm-studio) ──
_OPENAI_COMPAT_DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
    "hosted_vllm": "http://127.0.0.1:1234/v1",
    "lm-studio": "http://127.0.0.1:1234/v1",
}


async def _openai_like_models(provider: str, creds: dict, config: dict) -> list[str]:
    base = (config.get("base_url") or _OPENAI_COMPAT_DEFAULT_BASE.get(provider, "https://api.openai.com/v1")).rstrip("/")
    headers = {}
    key = creds.get("api_key", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{base}/models", headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"{provider} /models HTTP {r.status_code}: {r.text[:160]}")
    return [m.get("id") for m in r.json().get("data", []) if m.get("id")]
