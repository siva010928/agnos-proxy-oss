"""BVT/surfaces - every OpenAI-compatible surface against EchoEngine.

Covers /v1/chat/completions (non-stream, stream, tools), /v1/embeddings,
/v1/utils/count_tokens, /v1/models, /admin/routing/preview, /v1/routing/resolve.
"""
from __future__ import annotations

import json

import httpx

from .conftest import chat_request


def test_chat_completion_clean_openai_shape(fresh_workspace, gateway_url):
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}", "Content-Type": "application/json"},
        json=chat_request(content="Hello world"),
        timeout=30,
    )
    assert r.status_code == 200
    b = r.json()
    assert b["object"] == "chat.completion"
    assert b["choices"][0]["finish_reason"] in ("stop", "tool_calls")
    assert "usage" in b
    assert b["usage"]["completion_tokens"] >= 1
    # No leak keys at the boundary
    for k in ("extra_fields", "bifrost_config"):
        assert k not in b


def test_chat_streaming_emits_done_marker(fresh_workspace, gateway_url):
    chunks: list[bytes] = []
    with httpx.stream(
        "POST",
        f"{gateway_url}/v1/chat/completions",
        headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}", "Content-Type": "application/json"},
        json=chat_request(content="streamed echo", stream=True),
        timeout=30,
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line:
                chunks.append(line.encode() if isinstance(line, str) else line)

    # First chunk has role, last chunk is "data: [DONE]"
    assert any(b"[DONE]" in c for c in chunks), "missing [DONE] marker"
    assert any(b'"role"' in c for c in chunks), "no role chunk"
    # At least one content delta chunk
    assert any(b'"content"' in c for c in chunks), "no content deltas"


def test_chat_tools_finish_reason_tool_calls(fresh_workspace, gateway_url):
    body = chat_request(
        content="What's the weather in Paris?",
        tools=[{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather for a city",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }],
    )
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}", "Content-Type": "application/json"},
        json=body, timeout=30,
    )
    assert r.status_code == 200
    b = r.json()
    assert b["choices"][0]["finish_reason"] == "tool_calls"
    tcs = b["choices"][0]["message"]["tool_calls"]
    assert len(tcs) >= 1
    assert tcs[0]["function"]["name"] == "get_weather"


def test_embeddings_returns_list_with_vectors(fresh_workspace, gateway_url):
    # Add an embedding alias to the workspace via PATCH
    httpx.patch(
        f"{gateway_url}/admin/workspaces/{fresh_workspace['workspace_id']}",
        headers={"X-Admin-Token": "platform-admin-secret", "Content-Type": "application/json"},
        json={
            "embedding_models": {
                "text-embedding-default": [
                    {"provider": "bedrock", "model_id": "amazon.titan-embed-text-v2:0"}
                ]
            }
        },
        timeout=30,
    )
    r = httpx.post(
        f"{gateway_url}/v1/embeddings",
        headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}", "Content-Type": "application/json"},
        json={"model": "text-embedding-default", "input": ["hello", "world"]},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["object"] == "list"
    assert len(b["data"]) == 2
    assert len(b["data"][0]["embedding"]) == 1024  # Titan-shape
    assert "usage" in b


def test_count_tokens_returns_positive(fresh_workspace, gateway_url):
    r = httpx.post(
        f"{gateway_url}/v1/utils/count_tokens",
        headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}", "Content-Type": "application/json"},
        json={"model": "claude-sonnet-4-5",
              "messages": [{"role": "user", "content": "Count my tokens please"}]},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["estimated_tokens"] > 0
    assert b["method"].startswith("tiktoken")


def test_v1_models_returns_workspace_scoped_list(fresh_workspace, gateway_url):
    r = httpx.get(
        f"{gateway_url}/v1/models",
        headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}"},
        timeout=30,
    )
    assert r.status_code == 200
    b = r.json()
    assert b["object"] == "list"
    aliases = [m["id"] for m in b["data"]]
    assert fresh_workspace["alias"] in aliases


def test_admin_routing_preview_resolves_alias(fresh_workspace, http_admin):
    r = http_admin.get(
        "/admin/routing/preview",
        params={"workspace": fresh_workspace["workspace_id"], "alias": fresh_workspace["alias"]},
    )
    assert r.status_code == 200
    b = r.json()
    assert b["resolved_alias"] == fresh_workspace["alias"]
    assert len(b["resolved_targets"]) == 1
    assert b["resolved_targets"][0]["provider"] == "bedrock"


def test_v1_routing_resolve_with_bearer_key(fresh_workspace, gateway_url):
    """The non-admin /v1/routing/resolve introspection uses the workspace's bearer key."""
    r = httpx.get(
        f"{gateway_url}/v1/routing/resolve",
        headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}"},
        params={"alias": fresh_workspace["alias"]},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["resolved_alias"] == fresh_workspace["alias"]
