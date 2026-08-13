"""Opt-in live smoke tests (real gateway + real providers).

Run explicitly:   pytest -m live -v
Default `pytest` runs skip these automatically if the gateway is unreachable.
"""
from __future__ import annotations

import os
import httpx
import pytest

pytestmark = pytest.mark.live

GW = os.getenv("GATEWAY_URL", "http://localhost:8090")
KEYS = {
    "bedrock": (os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001"), "claude-sonnet-4-5"),
    "anthropic": (os.getenv("WS_KEY_PRIMARY", "gw-key-primary-001"), "claude-sonnet-4-5"),
    "gemini": (os.getenv("WS_KEY_GEMINI", "gw-key-gemini-001"), "gemini-flash"),
}


@pytest.fixture(scope="session", autouse=True)
def _require_gateway():
    try:
        r = httpx.get(f"{GW}/health", timeout=3)
        if r.status_code != 200:
            pytest.skip("gateway not healthy")
    except Exception:
        pytest.skip("gateway unreachable")


def _chat(key, model, **extra):
    # 256 tokens so "thinking" models (Gemini 2.5-flash) have budget for a final answer
    body = {"model": model, "messages": [{"role": "user", "content": "reply OK"}], "max_tokens": 256, **extra}
    return httpx.post(f"{GW}/v1/chat/completions", json=body,
                      headers={"Authorization": f"Bearer {key}"}, timeout=90)


@pytest.mark.parametrize("provider", ["bedrock", "anthropic", "gemini"])
def test_live_chat(provider):
    key, model = KEYS[provider]
    r = _chat(key, model)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["object"] == "chat.completion"
    assert "extra_fields" not in d            # anti-corruption boundary
    assert d["usage"]["total_tokens"] > 0     # usage recorded for governance
    assert d.get("choices") and d["choices"][0]["message"]["content"]


def test_live_tools_bedrock():
    key, model = KEYS["bedrock"]
    r = httpx.post(f"{GW}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, timeout=90,
                   json={"model": model, "max_tokens": 200,
                         "messages": [{"role": "user", "content": "weather in Paris? use the tool"}],
                         "tools": [{"type": "function", "function": {"name": "get_weather",
                                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                                                   "required": ["city"]}}}], "tool_choice": "auto"})
    assert r.status_code == 200, r.text
    tc = r.json()["choices"][0]["message"].get("tool_calls")
    assert tc and tc[0]["function"]["name"] == "get_weather"


def test_live_stream_bedrock():
    key, model = KEYS["bedrock"]
    with httpx.stream("POST", f"{GW}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                      timeout=90, json={"model": model, "max_tokens": 20, "stream": True,
                                        "messages": [{"role": "user", "content": "count one two three"}]}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "data:" in body and "[DONE]" in body
    assert "extra_fields" not in body


def test_live_embeddings_bedrock():
    key, _ = KEYS["bedrock"]
    r = httpx.post(f"{GW}/v1/embeddings", headers={"Authorization": f"Bearer {key}"}, timeout=60,
                   json={"model": "text-embedding-default", "input": "governance gateway"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["data"][0]["embedding"]) > 100
    assert d["usage"]["prompt_tokens"] > 0


def test_live_guardrail_block_pii():
    key, model = KEYS["bedrock"]   # Document Processing → PII block
    r = _chat(key, model, messages=[{"role": "user", "content": "my SSN is 123-45-6789"}])
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "guardrail_violation"


def test_live_engine_swap_parity():
    """Direct engine must return the identical OpenAI contract."""
    admin = {"X-Admin-Token": os.getenv("PLATFORM_ADMIN_TOKEN", "platform-admin-secret")}
    key, model = KEYS["bedrock"]
    try:
        httpx.post(f"{GW}/admin/engine", json={"engine": "direct"}, headers=admin, timeout=10)
        r = _chat(key, model)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["object"] == "chat.completion" and d["choices"][0]["message"]["content"]
        assert "extra_fields" not in d
    finally:
        httpx.post(f"{GW}/admin/engine", json={"engine": "bifrost"}, headers=admin, timeout=10)
