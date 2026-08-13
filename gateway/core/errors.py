"""Map engine/provider/Bifrost errors → OpenAI-compatible HTTP error responses.

A component using any OpenAI SDK will raise the correct native exception
(RateLimitError, BadRequestError, AuthenticationError, ...) from these shapes.
"""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def openai_error_body(message: str, err_type: str, code: str | None = None,
                      param: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": err_type,
                      "code": code or err_type, "param": param}}


def openai_error_response(status_code: int, message: str, err_type: str,
                          code: str | None = None, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code,
                        content=openai_error_body(message, err_type, code),
                        headers=headers or {})


def map_bifrost_error(status_code: int, payload: dict) -> tuple[int, dict]:
    """Translate Bifrost's {"is_bifrost_error",..,"error":{"error"|"type","message"}} → OpenAI shape."""
    err = payload.get("error", {}) if isinstance(payload, dict) else {}
    message = err.get("message") or err.get("error") or "Upstream provider error."
    upstream_type = err.get("type") or ""   # may be JSON null; coerce so `in`/`.lower()` are safe

    code = status_code or 502
    if code == 401 or "authentication" in upstream_type:
        # Never leak provider-key issues as 401 to the component; it's a gateway/provider config issue.
        return 502, openai_error_body(f"Provider authentication failed at gateway: {message}",
                                      "provider_auth_error")
    if code == 429 or "rate" in upstream_type.lower() or "throttl" in (upstream_type + message).lower():
        return 429, openai_error_body(message, "rate_limit_exceeded")
    if code == 400:
        low = message.lower()
        if "context" in low and ("length" in low or "window" in low or "token" in low):
            return 400, openai_error_body(message, "context_length_exceeded")
        return 400, openai_error_body(message, "invalid_request_error")
    # Anthropic "overloaded" surfaces as 529 - treat as a retryable rate/overload.
    if code == 529 or "overload" in (upstream_type + message).lower():
        return 503, openai_error_body(message or "Provider overloaded; retry shortly.", "upstream_error")
    # Timeouts: classify distinctly (type "timeout") so the trace/UI doesn't bury
    # them under generic "upstream_error". The caller rewrites the message with the
    # ACTUAL configured timeout (engines like Bifrost emit a misleading static
    # "default is 30 seconds" string regardless of the real configured value).
    if code == 504 or "timed out" in message.lower() or "request_timed_out" in upstream_type.lower() \
            or "timeout" in upstream_type.lower():
        return 504, openai_error_body(message or "Upstream request timed out.", "timeout")
    if code in (502, 503, 504):
        return code, openai_error_body(message, "upstream_error")
    # Any other upstream 5xx (e.g. a bare provider 500) is the UPSTREAM failing,
    # not this gateway - surface it as 502 Bad Gateway with a clear type so the
    # component knows it's a transient upstream issue (safe to retry), never a
    # bare 500 that looks like the gateway itself crashed.
    if code >= 500:
        return 502, openai_error_body(message or "Upstream provider error.", "upstream_error")
    return code, openai_error_body(message, "upstream_error")


def map_exception(exc: Exception) -> tuple[int, dict]:
    name = type(exc).__name__.lower()
    msg = str(exc)
    if "timeout" in name:
        return 504, openai_error_body("Upstream request timed out.", "timeout")
    if "connect" in name:
        return 502, openai_error_body(f"Cannot reach backend engine: {msg}", "upstream_error")
    return 502, openai_error_body(f"Gateway error: {msg}", "upstream_error")
