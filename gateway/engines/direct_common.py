"""Shared helpers for the DirectEngine provider adapters (owned, in-process).

Pure OpenAI-shaped builders + provider-error normalization. Nothing here writes
any engine-side annotation key; the only helper that does is the shared body
builder in direct_engine (kept there so the boundary strip stays under test).
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from gateway.core import errors

# Embedding models that reject an explicit ``dimensions`` arg - the gateway
# omits it automatically so a configured default never crashes them
# (Titan v1, Cohere v3, OpenAI ada-002 do not accept a dimensions parameter).
_NO_DIMENSIONS = ("titan-embed-text-v1", "titan-embed-image-v1",
                  "cohere.embed", "text-embedding-ada-002", "embedding-001")


def supports_dimensions(model_id: str) -> bool:
    m = (model_id or "").lower()
    return not any(tok in m for tok in _NO_DIMENSIONS)


def new_id(prefix: str = "chatcmpl-direct") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


DONE = b"data: [DONE]\n\n"


def chunk(model: str, *, delta: dict | None = None, finish: str | None = None,
          usage: dict | None = None, cid: str | None = None,
          tool_calls: list[dict] | None = None) -> dict:
    """Build one OpenAI ``chat.completion.chunk`` object."""
    d: dict[str, Any] = {}
    if delta is not None:
        d = dict(delta)
    if tool_calls is not None:
        d["tool_calls"] = tool_calls
    choice: dict[str, Any] = {"index": 0, "delta": d, "finish_reason": finish}
    body: dict[str, Any] = {
        "id": cid or new_id(),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [choice],
    }
    if usage is not None:
        # OpenAI emits usage on a final chunk with an empty choices list when
        # stream_options.include_usage is set; our stream reader tolerates either.
        body["usage"] = usage
    return body


def usage_block(input_tokens: int, output_tokens: int) -> dict:
    return {"prompt_tokens": int(input_tokens or 0),
            "completion_tokens": int(output_tokens or 0),
            "total_tokens": int((input_tokens or 0) + (output_tokens or 0))}


def embeddings_body(model_id: str, vectors: list[list[float]],
                    input_tokens: int = 0) -> dict:
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v}
                 for i, v in enumerate(vectors)],
        "model": model_id,
        "usage": {"prompt_tokens": int(input_tokens or 0), "total_tokens": int(input_tokens or 0)},
    }


# ── error normalization ──────────────────────────────────────────────────────
def classify_http(status: int, message: str) -> tuple[int, dict]:
    """Normalize a provider HTTP error → (status, OpenAI-shaped error body).

    Reuses the same mapping the Bifrost path uses so DirectEngine and
    BifrostEngine return byte-identical error taxonomies (rate_limit_exceeded /
    context_length_exceeded / invalid_request_error / provider_auth_error /
    timeout / upstream_error). This is what keeps the swap transparent to callers.
    """
    return errors.map_bifrost_error(status, {"error": {"message": message}})


def exc_to_openai(exc: Exception, provider: str) -> tuple[int, dict]:
    """Normalize an SDK/transport exception → (status, OpenAI-shaped error body).

    Class/structured-field based first (never bare message matching), with a
    Bedrock ``ValidationException`` overflow allowlist as the documented
    exception (Bedrock gives no typed signal for an over-length prompt)."""
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()

    # boto3 ClientError carries a structured error code.
    code = ""
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        code = (resp.get("Error", {}) or {}).get("Code", "") or ""
    code_l = code.lower()

    if "throttl" in code_l or "toomanyrequests" in code_l or "ratelimit" in name.lower() or "429" in msg:
        return 429, errors.openai_error_body(msg, "rate_limit_exceeded")
    if code in ("ExpiredTokenException", "UnrecognizedClientException",
                "AccessDeniedException", "InvalidSignatureException") \
            or "authenticationerror" in name.lower() or "permission" in code_l:
        return 502, errors.openai_error_body(f"Provider authentication failed at gateway: {msg}",
                                             "provider_auth_error")
    if "timeout" in name.lower() or "timed out" in low or "readtimeout" in name.lower():
        return 504, errors.openai_error_body("Upstream request timed out.", "timeout")
    if "connect" in name.lower() or "connection" in low:
        return 502, errors.openai_error_body(f"Cannot reach provider: {msg}", "upstream_error")
    if code == "ValidationException":
        # Bedrock overflow allowlist (stable, documented phrases; specific enough
        # to length/tokens/context that they cannot collide with other validation
        # failures - an "invalid model identifier" stays a bad_request).
        if any(p in low for p in ("input is too long", "maximum context length",
                                  "too many tokens", "too long for requested model")):
            return 400, errors.openai_error_body(msg, "context_length_exceeded")
        return 400, errors.openai_error_body(msg, "invalid_request_error")
    if "notfound" in code_l or "resourcenotfound" in code_l:
        return 404, errors.openai_error_body(msg, "invalid_request_error")
    return 502, errors.openai_error_body(f"Provider error: {msg}", "upstream_error")
