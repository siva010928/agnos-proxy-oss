"""Real provider connection test - an actual minimal probe with the supplied
credentials (no persistence, no engine round-trip), returning green/red + the
real upstream error. Backs the UI "Test Connection" button.

**Critical:** uses **only** the credentials in the request body. Never falls
back to environment / managed / stored creds (boto3's default credential chain
would otherwise let an empty `access_key` succeed via `AWS_ACCESS_KEY_ID`).
"""
from __future__ import annotations

import time

import httpx

# Required (non-empty) credential fields per provider. Anything missing → fast-fail.
REQUIRED: dict[str, list[str]] = {
    "bedrock": ["access_key", "secret_key"],
    "anthropic": ["api_key"],
    "gemini": ["api_key"],
    "azure": ["api_key", "endpoint"],
    "openai": ["api_key"],
    "google_genai": ["api_key"],
    "vertex_ai": ["api_key", "vertex_project"],
    "litellm_proxy": ["api_key", "base_url"],
    "ollama": [],
    "hosted_vllm": [],
}


def _bedrock_auth(creds: dict, config: dict) -> str:
    """Infer the bedrock auth mode. Prefer an explicit auth_type, else derive it
    from the fields present (matches the DirectEngine session logic): a
    bedrock_api_key ⇒ bearer, a profile_name ⇒ sso, otherwise static keys."""
    at = (creds.get("auth_type") or config.get("auth_type") or "").lower()
    if at:
        return at
    if (creds.get("bedrock_api_key") or "").strip():
        return "api-key"
    if (creds.get("profile_name") or config.get("profile_name") or "").strip():
        return "sso"
    return "static"


def _missing(provider: str, creds: dict, config: dict) -> str | None:
    if provider == "bedrock":
        # Required fields depend on the chosen auth mode (static / api-key / sso),
        # matching the DirectEngine bedrock session logic.
        auth = _bedrock_auth(creds, config)
        if auth == "sso":
            fields = ["profile_name"]
        elif auth in ("api-key", "api_key", "bearer"):
            fields = ["bedrock_api_key"]
        else:
            fields = ["access_key", "secret_key"]
    else:
        fields = REQUIRED.get(provider)
    if not fields:
        return f"unknown provider '{provider}'"
    blanks = [k for k in fields
              if not (creds.get(k) or config.get(k) or "").strip()]
    if blanks:
        return f"missing required credential field(s): {', '.join(blanks)}"
    return None


async def test_connection(provider: str, creds: dict, config: dict, model_id: str | None) -> dict:
    creds = creds or {}
    config = config or {}
    err = _missing(provider, creds, config)
    if err:
        return {"ok": False, "provider": provider, "latency_ms": 0.0,
                "error": err, "detail": {"reason": "validation"}}
    t0 = time.monotonic()
    try:
        ok, detail = await _probe(provider, creds, config, model_id)
        return {"ok": ok, "provider": provider, "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": provider,
                "latency_ms": round((time.monotonic() - t0) * 1000, 1), "error": str(exc)[:300]}


async def _probe(provider: str, creds: dict, config: dict, model_id: str | None):
    if provider == "bedrock":
        return await _probe_bedrock(creds, config, model_id)
    if provider == "anthropic":
        return await _probe_anthropic(creds, model_id)
    if provider in ("gemini", "google_genai"):
        return await _probe_gemini(creds, model_id)
    if provider == "vertex_ai":
        return await _probe_vertex(creds, config, model_id)
    if provider in ("openai", "azure", "litellm_proxy", "ollama", "hosted_vllm", "lm-studio"):
        return await _probe_openai_like(provider, creds, config, model_id)
    raise ValueError(f"unknown provider '{provider}'")


async def _probe_vertex(creds: dict, config: dict, model_id: str | None):
    """Prove the service account authenticates (mint a token) + a live chat call
    through Vertex's OpenAI-compatible endpoint."""
    from gateway.core.registry import ResolvedTarget
    from gateway.engines.direct_vertex import vertex_chat
    target = ResolvedTarget(provider="vertex_ai", model_id=(model_id or "gemini-2.5-flash"),
                            credentials=creds, config=config)
    res = await vertex_chat({"messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}, target)
    if res.ok:
        return True, {"model": target.model_id, "project": config.get("vertex_project")}
    return False, {"status": res.status_code, "error": (res.body.get("error") or {}).get("message", "")[:200]}


async def _probe_bedrock(creds: dict, config: dict, model_id: str | None):
    """Probe Bedrock with **only** the supplied creds. We pass them through
    botocore.session.Session with explicit credentials and disable env/profile
    lookups, so an empty key cannot pass via the default credential chain."""
    import asyncio
    import os
    import boto3
    from botocore.session import Session as BotocoreSession
    mid = model_id or "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    region = (creds.get("region") or config.get("region") or "us-east-1")
    auth = _bedrock_auth(creds, config)
    profile = creds.get("profile_name") or config.get("profile_name")
    bearer = creds.get("bedrock_api_key") or creds.get("api_key")

    def _call():
        # Build the session per auth mode - mirrors gateway/engines/direct_bedrock.py
        # so a Test proves the SAME auth path the request will use.
        if auth == "sso" and profile:
            sess = boto3.Session(profile_name=profile, region_name=region)   # SSO profile
        elif auth in ("api-key", "api_key", "bearer") and bearer:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = bearer                   # Bedrock API key
            bsess = BotocoreSession()
            bsess.set_config_variable("credentials_file", "/dev/null")
            bsess.set_config_variable("config_file", "/dev/null")
            sess = boto3.Session(botocore_session=bsess, region_name=region)
        else:
            # Static keys - hard-disable any other provider chain (env/IMDS/file),
            # so an empty key cannot pass via the default credential chain.
            bsess = BotocoreSession()
            bsess.set_config_variable("credentials_file", "/dev/null")
            bsess.set_config_variable("config_file", "/dev/null")
            bsess.set_credentials(creds.get("access_key", ""), creds.get("secret_key", ""),
                                  creds.get("session_token") or None)
            sess = boto3.Session(botocore_session=bsess, region_name=region)
        client = sess.client("bedrock-runtime")
        return client.converse(modelId=mid,
                               messages=[{"role": "user", "content": [{"text": "ping"}]}],
                               inferenceConfig={"maxTokens": 1})
    resp = await asyncio.to_thread(_call)
    return True, {"request_id": resp.get("ResponseMetadata", {}).get("RequestId"), "model": mid, "auth": auth}


async def _probe_anthropic(creds: dict, model_id: str | None):
    mid = model_id or "claude-sonnet-4-5-20250929"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post("https://api.anthropic.com/v1/messages",
                         headers={"x-api-key": creds.get("api_key", ""),
                                  "anthropic-version": "2023-06-01", "content-type": "application/json"},
                         json={"model": mid, "max_tokens": 1,
                               "messages": [{"role": "user", "content": "ping"}]})
    if r.status_code == 200:
        return True, {"model": mid}
    return False, {"status": r.status_code, "error": r.text[:200]}


async def _probe_gemini(creds: dict, model_id: str | None):
    mid = model_id or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{mid}:generateContent"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, params={"key": creds.get("api_key", "")},
                         json={"contents": [{"parts": [{"text": "ping"}]}],
                               "generationConfig": {"maxOutputTokens": 1}})
    if r.status_code == 200:
        return True, {"model": mid}
    return False, {"status": r.status_code, "error": r.text[:200]}


_OPENAI_COMPAT_DEFAULT_BASE = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
    "hosted_vllm": "http://127.0.0.1:1234/v1",
    "lm-studio": "http://127.0.0.1:1234/v1",
}


async def _probe_openai_like(provider: str, creds: dict, config: dict, model_id: str | None):
    base = (config.get("base_url") or _OPENAI_COMPAT_DEFAULT_BASE.get(provider, "https://api.openai.com/v1")).rstrip("/")
    if provider == "azure":
        base = config.get("endpoint", "").rstrip("/")
    headers = {"Authorization": f"Bearer {creds.get('api_key', '')}"}
    if provider == "azure":
        headers = {"api-key": creds.get("api_key", "")}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{base}/models", headers=headers)
    return (r.status_code == 200), {"status": r.status_code, "base_url": base}


def clean_guardrail_id(raw: str) -> str:
    """Normalize a pasted guardrail identifier. The AWS console 'copy' often
    includes an 'ARN: ' label prefix and stray whitespace - strip the label so
    ApplyGuardrail gets the bare id or arn:aws:... value."""
    s = (raw or "").strip()
    if s.lower().startswith("arn:") and not s.lower().startswith("arn:aws"):
        # 'ARN: arn:aws:bedrock:...' (label) → keep the real arn after the first ':'
        s = s.split(":", 1)[1].strip()
    return s


def clean_guardrail_version(raw) -> str:
    """ApplyGuardrail wants 'DRAFT' or a numeric version; the console label is
    'Working draft' - normalize common variants."""
    s = str(raw or "").strip()
    if not s or s.lower() in ("working draft", "draft", "working-draft", "working_draft"):
        return "DRAFT"
    return s


async def test_bedrock_guardrail(config: dict) -> dict:
    """Connectivity test for an AWS Bedrock *Guardrail* profile - calls the real
    ``ApplyGuardrail`` API (exactly what the runtime detector uses), so it works
    with a guardrail-only IAM principal and validates guardrail_id + region +
    credentials (including temporary STS session tokens) together.
    Uses ONLY the supplied credentials (no env/profile fallback)."""
    import asyncio
    config = config or {}
    gid = clean_guardrail_id(config.get("guardrail_id") or config.get("guardrail_arn") or "")
    if not gid:
        return {"ok": False, "error": "guardrail_id / ARN is required", "latency_ms": 0.0}
    access = (config.get("access_key") or "").strip()
    secret = (config.get("secret_key") or "").strip()
    if not access or not secret:
        return {"ok": False, "error": "access_key and secret_key are required", "latency_ms": 0.0}
    region = (config.get("region") or "us-east-1").strip()
    version = clean_guardrail_version(config.get("guardrail_version"))
    session_token = (config.get("session_token") or "").strip() or None

    t0 = time.monotonic()

    def _call():
        import boto3
        from botocore.config import Config as BotoConfig
        from botocore.session import Session as BotocoreSession
        bsess = BotocoreSession()
        bsess.set_config_variable("credentials_file", "/dev/null")
        bsess.set_config_variable("config_file", "/dev/null")
        bsess.set_credentials(access, secret, session_token)
        timeout_s = max(1.0, float(config.get("timeout_ms") or 5000) / 1000.0)
        sess = boto3.Session(botocore_session=bsess, region_name=region)
        client = sess.client("bedrock-runtime",
                             config=BotoConfig(connect_timeout=timeout_s, read_timeout=timeout_s,
                                               retries={"max_attempts": 0}))
        return client.apply_guardrail(guardrailIdentifier=gid, guardrailVersion=version,
                                      source="INPUT", content=[{"text": {"text": "connectivity check"}}])

    try:
        resp = await asyncio.to_thread(_call)
        return {"ok": True, "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "detail": {"action": resp.get("action"),
                           "request_id": resp.get("ResponseMetadata", {}).get("RequestId"),
                           "guardrail": gid, "region": region, "version": version,
                           "used_session_token": bool(session_token)}}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        low = msg.lower()
        # Pull the account id out of the guardrail ARN so we can name the mismatch
        # precisely (arn:aws:bedrock:<region>:<account>:guardrail/<id>).
        arn_acct = ""
        if gid.startswith("arn:"):
            _p = gid.split(":")
            if len(_p) >= 5 and _p[4].isdigit():
                arn_acct = _p[4]
        hint = ""
        if "UnrecognizedClientException" in msg or "security token" in msg:
            hint = (" - the access key looks like a temporary (ASIA…) STS credential; "
                    "those REQUIRE a Session token. Paste it in the Session token field.")
        elif "different account" in low:
            # The ARN's account ≠ the credentials' account. Bedrock guardrails are
            # single-account: ApplyGuardrail can only be called by the owning account.
            who = f"account {arn_acct}" if arn_acct else "a different account"
            hint = (f" - this guardrail belongs to {who}, but the access key you pasted is "
                    "from a DIFFERENT AWS account. Bedrock guardrails can only be applied by "
                    "the account that owns them (there is no cross-account ApplyGuardrail). "
                    f"Paste an access key from {who}, or recreate the guardrail in the account "
                    "your keys belong to.")
        elif "AccessDenied" in msg:
            hint = " - the IAM principal needs bedrock:ApplyGuardrail on this guardrail."
        elif "ResourceNotFound" in msg or "ValidationException" in msg:
            hint = " - check the Guardrail ID/ARN and that the Region matches where it was created."
        return {"ok": False, "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                "error": (msg[:280] + hint)}
