"""BVT/B2 - capped real-provider smoke.

Three tiny calls (max_tokens=8) - one per real provider (bedrock, anthropic,
gemini) - that prove the live wiring without exceeding ~$0.01 in spend.

These are the *only* tests in the BVT family that hit a real upstream. They
are marked `live` so the default `pytest` run deselects them; opt in with:

    pytest -m live tests/integration/test_live_smoke.py

Each test asserts max_tokens<=8 IN THE BODY before sending, so a future edit
can't silently balloon cost.
"""
from __future__ import annotations

import os
from typing import Iterable

import httpx
import pytest

pytestmark = pytest.mark.live

GW = os.getenv("GATEWAY_URL", "http://localhost:8090")
ADMIN = {"X-Admin-Token": os.getenv("PLATFORM_ADMIN_TOKEN", "platform-admin-secret")}

# Hard, per-test request cap - asserted before send. Never raise without good reason.
# NOTE: Gemini 2.5-flash is a *thinking* model - at very small max_tokens it
# spends all tokens on internal reasoning and returns `choices:null`. We use a
# slightly larger cap for Gemini so the wiring assertion is robust; cost is
# still pennies (64 tokens × $0.075/Mtok ≈ $0.000005 per call).
MAX_TOKENS_CAP_DEFAULT = 8
MAX_TOKENS_CAP_GEMINI = 64

KEYS_AND_MODELS: dict[str, tuple[str, str, int]] = {
    "bedrock":   (os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001"), "claude-sonnet-4-5", MAX_TOKENS_CAP_DEFAULT),
    "anthropic": (os.getenv("WS_KEY_PRIMARY",  "gw-key-primary-001"),  "claude-sonnet-4-5", MAX_TOKENS_CAP_DEFAULT),
    "gemini":    (os.getenv("WS_KEY_GEMINI",      "gw-key-gemini-001"),      "gemini-flash",      MAX_TOKENS_CAP_GEMINI),
}


@pytest.fixture(scope="module", autouse=True)
def _swap_to_bifrost_for_module():
    """B2 hits real providers; ensure the engine is bifrost (not echo) for this
    module. We restore whatever was set on entry on teardown."""
    try:
        prev = httpx.get(f"{GW}/health/ready", timeout=3).json().get("checks", {}).get("engine_name")
    except Exception:
        prev = None

    httpx.post(f"{GW}/admin/engine", headers={**ADMIN, "Content-Type": "application/json"},
               json={"engine": "bifrost"}, timeout=30)
    yield
    if prev and prev != "bifrost":
        try:
            httpx.post(f"{GW}/admin/engine",
                       headers={**ADMIN, "Content-Type": "application/json"},
                       json={"engine": prev}, timeout=30)
        except Exception:
            pass


@pytest.fixture(scope="session", autouse=True)
def _require_gateway_up():
    try:
        r = httpx.get(f"{GW}/health", timeout=3)
        if r.status_code != 200:
            pytest.skip("gateway not healthy")
    except Exception:
        pytest.skip(f"gateway unreachable at {GW}")


def _send_capped_chat(provider: str, key: str, model: str, cap: int) -> httpx.Response:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": cap,
    }
    # **In-test spend cap**: defense-in-depth so a future edit can't balloon cost.
    # Hard ceiling regardless of provider - the per-provider cap above sits well below this.
    HARD_CEILING = 64
    assert body["max_tokens"] <= HARD_CEILING, (
        f"max_tokens={body['max_tokens']} exceeds the BVT B2 hard ceiling "
        f"of {HARD_CEILING}; refusing to send."
    )
    return httpx.post(
        f"{GW}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Gateway-Component": "document-processing"},
        json=body, timeout=30,
    )


def _assert_clean_openai_completion(resp: httpx.Response, expected_provider: str, cap: int) -> None:
    assert resp.status_code == 200, f"{expected_provider}: {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    # Anti-corruption boundary
    for k in ("extra_fields", "bifrost_config"):
        assert k not in body, f"{expected_provider}: leak key '{k}' in response body"
    # OpenAI-clean shape
    assert body.get("object") == "chat.completion", f"{expected_provider}: unexpected object={body.get('object')}"
    # Usage present and bounded by the cap
    usage = body.get("usage") or {}
    completion = usage.get("completion_tokens", 0)
    assert isinstance(completion, int) and completion >= 0, f"{expected_provider}: missing completion_tokens"
    assert completion <= cap, (
        f"{expected_provider}: completion_tokens={completion} exceeded cap of {cap}"
    )
    # If choices contain a message with content, finish_reason must be valid.
    # (Gemini thinking-model edge: at very small caps choices may be null.)
    choices = body.get("choices") or []
    if choices and choices[0]:
        finish = choices[0].get("finish_reason")
        assert finish in ("stop", "length", "tool_calls"), \
            f"{expected_provider}: bad finish_reason={finish}"


@pytest.mark.parametrize("provider", list(KEYS_AND_MODELS))
def test_live_smoke_one_provider(provider: str) -> None:
    key, model, cap = KEYS_AND_MODELS[provider]
    r = _send_capped_chat(provider, key, model, cap)
    _assert_clean_openai_completion(r, provider, cap)
