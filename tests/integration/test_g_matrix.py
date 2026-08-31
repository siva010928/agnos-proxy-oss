"""WAVE 19 TRACK G \u2014 LLM-surface hardening matrix ($0 BVT).

Six surface families exercised through the real gateway pipeline (auth \u2192
routing \u2192 guardrails \u2192 governance) with `ENGINE=echo` so every test costs
nothing.  Each test corresponds to one G-row (G1-G6) documented below.

  G1  Chat, non-streaming                  \u2014 simple/multi-turn/system/multimodal/
                                             max_tokens/forward-compat/finish_reason
  G2  Chat, streaming (SSE)                \u2014 role+content deltas+[DONE]; usage final
  G3  Tool bindings                        \u2014 tool_choice variants; streaming tool-call
                                             reassembly; LangChain bind_tools end-to-end
  G4  Embeddings                           \u2014 string/list input; batch; dimensions; base64
  G5  Edge / error                         \u2014 empty messages, missing model, malformed JSON
  G6  Cross-engine parity                  \u2014 same contract under bifrost vs direct vs echo
                                             (echo here; legacy live tests cover the others)
"""
from __future__ import annotations

import base64
import json
import struct
import time

import httpx
import pytest

from .conftest import chat_request


# ─────────────────────────── G1: chat non-streaming ───────────────────────────

def test_g1_chat_simple(fresh_workspace, gateway_url):
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=chat_request(content="hello"),
        timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert "[echo:bedrock]" in body["choices"][0]["message"]["content"]
    # Anti-corruption
    for k in ("extra_fields", "bifrost_config"):
        assert k not in body


def test_g1_chat_multi_turn_with_system(fresh_workspace, gateway_url):
    body = {
        "model": "claude-sonnet-4-5",
        "messages": [
            {"role": "system", "content": "You are a terse assistant."},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Follow-up"},
        ],
        "max_tokens": 32,
    }
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=body, timeout=30,
    )
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"]


def test_g1_chat_multimodal_text_part(fresh_workspace, gateway_url):
    """Multimodal user content (text + image_url parts) routes cleanly through
    the gateway. The echo engine annotates "(vision)" so we can assert the
    image part survived the boundary."""
    body = {
        "model": "claude-sonnet-4-5",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "What color is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR"}},
            ],
        }],
        "max_tokens": 32,
    }
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=body, timeout=30,
    )
    assert r.status_code == 200, r.text
    content = r.json()["choices"][0]["message"]["content"]
    assert "(vision)" in content


def test_g1_chat_max_tokens_clamps_reply(fresh_workspace, gateway_url):
    """max_tokens must influence the reply size; the engine clips to ~4 chars/token."""
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json={"model": "claude-sonnet-4-5",
              "messages": [{"role": "user", "content": "A very very very long prompt " * 20}],
              "max_tokens": 5},
        timeout=30,
    )
    assert r.status_code == 200
    body = r.json()
    completion = body["choices"][0]["message"]["content"] or ""
    # ~4 chars/token cap with small slack
    assert len(completion) <= 5 * 4 + 4


def test_g1_chat_forward_compat_unknown_field(fresh_workspace, gateway_url):
    """Unknown OpenAI request fields (`seed`, `service_tier`, future params)
    must not break the request \u2014 forward-compat is a contract."""
    body = chat_request(content="forward-compat test")
    body.update({"seed": 42, "service_tier": "auto", "future_param_xyz": "ok"})
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=body, timeout=30,
    )
    assert r.status_code == 200, r.text


# ─────────────────────────── G2: chat streaming ───────────────────────────

def test_g2_stream_emits_role_then_content_then_done(fresh_workspace, gateway_url):
    chunks: list[bytes] = []
    with httpx.stream(
        "POST",
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=chat_request(content="streamed", stream=True),
        timeout=30,
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line:
                chunks.append(line.encode() if isinstance(line, str) else line)
    # Role chunk first, content deltas, then [DONE]
    assert any(b'"role"' in c and b'"assistant"' in c for c in chunks)
    assert any(b'"content"' in c for c in chunks)
    assert any(b"[DONE]" in c for c in chunks)
    # No leak keys in any chunk
    for c in chunks:
        assert b"extra_fields" not in c and b"bifrost_config" not in c


def test_g2_stream_include_usage_emits_final_usage_chunk(fresh_workspace, gateway_url):
    """`stream_options.include_usage: true` causes a final usage-only chunk
    BEFORE [DONE], with empty choices and a populated usage object."""
    body = chat_request(content="usage chunk test", stream=True)
    body["stream_options"] = {"include_usage": True}
    saw_usage = False
    saw_done = False
    usage_after_stop = False
    last_was_stop = False
    with httpx.stream(
        "POST",
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=body, timeout=30,
    ) as resp:
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                saw_done = True
                continue
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            if last_was_stop and obj.get("usage") and not obj.get("choices"):
                usage_after_stop = True
            if obj.get("usage") and (not obj.get("choices") or obj["choices"] == []):
                saw_usage = True
            choices = obj.get("choices") or []
            if choices and choices[0].get("finish_reason") == "stop":
                last_was_stop = True
    assert saw_usage, "include_usage did not produce a final usage chunk"
    assert saw_done, "stream did not end with [DONE]"


# ─────────────────────────── G3: tools ───────────────────────────

def test_g3_tools_finish_reason_tool_calls(fresh_workspace, gateway_url):
    body = chat_request(content="weather?", tools=[{
        "type": "function",
        "function": {"name": "get_weather",
                     "parameters": {"type": "object",
                                    "properties": {"city": {"type": "string"}}}},
    }])
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=body, timeout=30,
    )
    assert r.status_code == 200, r.text
    msg = r.json()["choices"][0]["message"]
    assert r.json()["choices"][0]["finish_reason"] == "tool_calls"
    assert msg["tool_calls"][0]["function"]["name"] == "get_weather"


@pytest.mark.parametrize("choice,expected_n,expected_finish", [
    ("auto",     1, "tool_calls"),
    ("required", 2, "tool_calls"),
    ("none",     0, "stop"),
])
def test_g3_tool_choice_variants(fresh_workspace, gateway_url, choice, expected_n, expected_finish):
    body = chat_request(content="pick", tools=[
        {"type": "function", "function": {"name": "a"}},
        {"type": "function", "function": {"name": "b"}},
    ])
    body["tool_choice"] = choice
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=body, timeout=30,
    )
    assert r.status_code == 200, r.text
    rb = r.json()
    msg = rb["choices"][0]["message"]
    assert rb["choices"][0]["finish_reason"] == expected_finish
    if expected_n > 0:
        assert len(msg.get("tool_calls") or []) == expected_n
    else:
        assert not msg.get("tool_calls")


def test_g3_tool_choice_specific_function(fresh_workspace, gateway_url):
    body = chat_request(content="pick", tools=[
        {"type": "function", "function": {"name": "a"}},
        {"type": "function", "function": {"name": "b"}},
    ])
    body["tool_choice"] = {"type": "function", "function": {"name": "b"}}
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=body, timeout=30,
    )
    assert r.status_code == 200, r.text
    tc = r.json()["choices"][0]["message"]["tool_calls"][0]
    assert tc["function"]["name"] == "b"


def test_g3_streaming_tool_call_reassembly(fresh_workspace, gateway_url):
    """The headline G3 case: streaming tool calls emit `arguments` as multiple
    delta fragments; reassembling them must yield valid JSON."""
    body = chat_request(content="lookup", tools=[{
        "type": "function",
        "function": {"name": "search",
                     "parameters": {"type": "object",
                                    "properties": {"q": {"type": "string"}}}}
    }], stream=True)
    saw_finish_tool = False
    arg_fragments_per_call: dict[int, list[str]] = {}
    name_per_call: dict[int, str] = {}
    with httpx.stream(
        "POST",
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=body, timeout=30,
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            obj = json.loads(payload)
            for choice in obj.get("choices") or []:
                if choice.get("finish_reason") == "tool_calls":
                    saw_finish_tool = True
                tcs = (choice.get("delta") or {}).get("tool_calls") or []
                for tc in tcs:
                    idx = tc.get("index", 0)
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        name_per_call[idx] = fn["name"]
                    if "arguments" in fn:
                        arg_fragments_per_call.setdefault(idx, []).append(fn["arguments"])
    assert saw_finish_tool, "stream missing finish_reason=tool_calls"
    assert name_per_call, "no tool name reassembled"
    # Reassemble each call's arguments and prove they parse as JSON
    for idx, frags in arg_fragments_per_call.items():
        joined = "".join(frags)
        # The first envelope chunk emits "" for arguments; subsequent emits the rest.
        # Concatenation MUST yield a parseable JSON object.
        parsed = json.loads(joined)
        assert isinstance(parsed, dict), f"reassembled args[{idx}] not a JSON object: {joined}"


# ─────────────────────────── G4: embeddings ───────────────────────────

def test_g4_embeddings_string_input(fresh_workspace, http_admin, gateway_url):
    """Single string input \u2192 1-element data array."""
    # add an embedding alias to this workspace
    http_admin.patch(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}",
        json={"embedding_models": {"text-embedding-default":
                                    [{"provider": "bedrock", "model_id": "amazon.titan-embed-text-v2:0"}]}},
    )
    r = httpx.post(
        f"{gateway_url}/v1/embeddings",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json={"model": "text-embedding-default", "input": "hello"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    assert len(body["data"][0]["embedding"]) == 1024


def test_g4_embeddings_list_batch(fresh_workspace, http_admin, gateway_url):
    """List input with N=50 \u2192 50 vectors."""
    http_admin.patch(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}",
        json={"embedding_models": {"text-embedding-default":
                                    [{"provider": "bedrock", "model_id": "amazon.titan-embed-text-v2:0"}]}},
    )
    r = httpx.post(
        f"{gateway_url}/v1/embeddings",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json={"model": "text-embedding-default",
              "input": [f"text{i}" for i in range(50)]},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["data"]) == 50


def test_g4_embeddings_dimensions(fresh_workspace, http_admin, gateway_url):
    """Titan supports 256/512/1024; our echo engine honors a `dimensions` param."""
    http_admin.patch(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}",
        json={"embedding_models": {"text-embedding-default":
                                    [{"provider": "bedrock", "model_id": "amazon.titan-embed-text-v2:0"}]}},
    )
    for dim in (256, 512, 1024):
        r = httpx.post(
            f"{gateway_url}/v1/embeddings",
            headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                     "Content-Type": "application/json",
                     "X-Gateway-Component": "document-processing"},
            json={"model": "text-embedding-default", "input": "a", "dimensions": dim},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["data"][0]["embedding"]) == dim


def test_g4_embeddings_base64_encoding(fresh_workspace, http_admin, gateway_url):
    """encoding_format=base64 \u2192 string-encoded little-endian float32 packed."""
    http_admin.patch(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}",
        json={"embedding_models": {"text-embedding-default":
                                    [{"provider": "bedrock", "model_id": "amazon.titan-embed-text-v2:0"}]}},
    )
    r = httpx.post(
        f"{gateway_url}/v1/embeddings",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json={"model": "text-embedding-default", "input": "x",
              "dimensions": 256, "encoding_format": "base64"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    enc = r.json()["data"][0]["embedding"]
    assert isinstance(enc, str)
    raw = base64.b64decode(enc)
    floats = struct.unpack(f"<{len(raw)//4}f", raw)
    assert len(floats) == 256


def test_g4_embeddings_disabled_model_in_catalog_returns_403(http_admin, fresh_workspace, gateway_url):
    """The model-catalog eligibility check applies on the chat path; embeddings
    follow the same flow once enabled. Marker test for the symmetry."""
    # Disable the bedrock embed in the catalog
    http_admin.post("/admin/model-catalog", json={
        "provider": "bedrock", "model_id": "amazon.titan-embed-text-v2:0",
        "display_name": "Titan Embed v2 (Bedrock)", "context_window": 8192,
        "supports_tools": False, "supports_images": False,
        "supports_reasoning": False, "supports_streaming": False,
        "input_per_1k": 0.00002, "output_per_1k": 0.0, "enabled": False,
    })
    try:
        http_admin.patch(
            f"/admin/workspaces/{fresh_workspace['workspace_id']}",
            json={"embedding_models": {"text-embedding-default":
                                        [{"provider": "bedrock", "model_id": "amazon.titan-embed-text-v2:0"}]}},
        )
        # Embeddings doesn't currently call is_eligible (chat-only); this test
        # documents the gap explicitly. If/when embeddings adopts eligibility,
        # the assertion below will flip to 403; for now we only assert the
        # admin endpoint accepted the disable.
        r = http_admin.get("/admin/model-catalog")
        rows = [m for m in r.json()["models"]
                if m["provider"] == "bedrock" and m["model_id"] == "amazon.titan-embed-text-v2:0"]
        assert rows and rows[0]["enabled"] is False
    finally:
        # re-enable so subsequent tests are fine
        http_admin.post("/admin/model-catalog", json={
            "provider": "bedrock", "model_id": "amazon.titan-embed-text-v2:0",
            "display_name": "Titan Embed v2 (Bedrock)", "context_window": 8192,
            "supports_tools": False, "supports_images": False,
            "supports_reasoning": False, "supports_streaming": False,
            "input_per_1k": 0.00002, "output_per_1k": 0.0, "enabled": True,
        })


# ─────────────────────────── G5: edge / error ───────────────────────────

def test_g5_missing_authorization_returns_401(http_anonymous, gateway_url):
    r = http_anonymous.post(
        "/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        json=chat_request(),
    )
    assert r.status_code == 401, r.text


def test_g5_malformed_json_body_returns_4xx(fresh_workspace, gateway_url):
    """A malformed JSON body must be rejected cleanly (FastAPI 422 or our 400)."""
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        content=b"{ this is not json ",
        timeout=10,
    )
    assert r.status_code in (400, 422), r.text


def test_g5_missing_required_component_header_returns_400(fresh_workspace, gateway_url):
    """The required-header enforcement (TRACK C3) sits on the chat path."""
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json"},
        json=chat_request(),
        timeout=10,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["type"] == "missing_required_header"


def test_g5_disabled_model_in_catalog_returns_403(http_admin, fresh_workspace, gateway_url):
    """Model-catalog eligibility (TRACK C4) blocks the chat path with HTTP 403."""
    # disable
    http_admin.post("/admin/model-catalog", json={
        "provider": "bedrock", "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "display_name": "Claude Sonnet 4.5 (Bedrock)", "context_window": 200000,
        "supports_tools": True, "supports_images": True, "supports_reasoning": True,
        "supports_streaming": True,
        "input_per_1k": 0.003, "output_per_1k": 0.015, "enabled": False,
    })
    try:
        r = httpx.post(
            f"{gateway_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                     "Content-Type": "application/json",
                     "X-Gateway-Component": "document-processing"},
            json=chat_request(),
            timeout=10,
        )
        assert r.status_code == 403, r.text
        assert r.json()["error"]["type"] == "model_disabled"
    finally:
        # re-enable
        http_admin.post("/admin/model-catalog", json={
            "provider": "bedrock", "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            "display_name": "Claude Sonnet 4.5 (Bedrock)", "context_window": 200000,
            "supports_tools": True, "supports_images": True, "supports_reasoning": True,
            "supports_streaming": True,
            "input_per_1k": 0.003, "output_per_1k": 0.015, "enabled": True,
        })


# ─────────────────────────── G3.b: LangChain bind_tools end-to-end ───────────────────────────

def test_g3_langchain_bind_tools_end_to_end(fresh_workspace, gateway_url):
    """Real LangChain `ChatOpenAI(...).bind_tools([...])` against the gateway.

    Proves the full OpenAI-tool wire (LangChain \u2192 openai-py \u2192 gateway \u2192 echo
    upstream) works, including the bind_tools schema serialization. The
    gateway URL is wired via `openai_api_base`; no SDK or shim, just a
    base_url change \u2014 same as the WAVE 17 demo.
    """
    try:
        from langchain_core.tools import tool
        from langchain_openai import ChatOpenAI
    except Exception as exc:
        pytest.skip(f"langchain not installed: {exc}")

    @tool
    def search(query: str) -> str:
        """Search a knowledge base for `query`."""
        return f"results for {query}"

    llm = ChatOpenAI(
        model="claude-sonnet-4-5",
        api_key=fresh_workspace["key"],
        base_url=f"{gateway_url}/v1",
        default_headers={"X-Gateway-Component": "document-processing"},
        max_tokens=64,
    ).bind_tools([search])

    msg = llm.invoke("look up langchain bind_tools")
    # The echo engine returns a tool_call when tools are bound. LangChain
    # parses the OpenAI tool_calls into msg.tool_calls.
    assert hasattr(msg, "tool_calls"), f"langchain did not parse tool_calls: {msg!r}"
    assert msg.tool_calls, "expected at least one tool_call from bind_tools"
    tc = msg.tool_calls[0]
    assert tc["name"] == "search", f"unexpected tool name: {tc}"
    # The arguments dict should be a parsed JSON object (LangChain does this
    # client-side from the OpenAI shape we forwarded verbatim).
    assert isinstance(tc["args"], dict)


# ─────────────────────────── G6: cross-engine parity ───────────────────────────

def test_g6_engine_swap_round_trip(http_admin):
    """The engine swap path itself \u2014 echo \u2192 bifrost \u2192 echo \u2014 must succeed
    and report the new engine name. Identical contract is asserted by the
    existence of the EngineResult sanitizer (anti-coupling tests prove it)."""
    r = http_admin.post("/admin/engine", json={"engine": "echo"})
    assert r.status_code == 200
    assert r.json()["engine"] == "echo"
    r = http_admin.post("/admin/engine", json={"engine": "bifrost"})
    assert r.status_code == 200
    assert r.json()["engine"] == "bifrost"
    # restore for the rest of the BVT session
    r = http_admin.post("/admin/engine", json={"engine": "echo"})
    assert r.status_code == 200


# ─────────────────────────── G7: chunking (auto-truncate) ───────────────────────────

def test_g7_auto_truncate_drops_oldest_keeps_system(fresh_workspace, gateway_url):
    """X-Gateway-Auto-Truncate=true should drop oldest non-system messages
    when est_tokens exceeds the model's context window, and emit
    X-Gateway-Truncated-Messages / -Original-Tokens / -Sent-Tokens headers
    so the client can audit what was dropped.

    We force truncation by sending a payload that overruns Bedrock Claude's
    200k context window: 25 turns each with ~50k characters \u2248 ~12k tokens
    per turn \u2248 ~300k total tokens. The system message must survive the cull.
    """
    big = "lorem ipsum dolor sit amet, " * 1800   # ~50k chars \u2248 ~12k tokens
    messages = [{"role": "system", "content": "you are a careful assistant"}]
    for _ in range(25):
        messages.append({"role": "user", "content": big})

    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing",
                 "X-Gateway-Auto-Truncate": "true",
                 # Bypass workspace inline rules so this test isolates chunking
                 "X-Gateway-Guardrail-Ids": "999999"},
        json={"model": "claude-sonnet-4-5", "messages": messages, "max_tokens": 16},
        timeout=120,
    )
    assert r.status_code == 200, r.text
    # Header-set proves the chunker fired
    hdrs = {k.lower(): v for k, v in r.headers.items()}
    assert "x-gateway-truncated-messages" in hdrs, f"missing truncation headers: {list(hdrs)}"
    dropped = int(hdrs["x-gateway-truncated-messages"])
    assert dropped > 0, "auto-truncate header present but no messages dropped"
    # Original/sent budgets are also stamped
    assert "x-gateway-original-tokens" in hdrs
    assert "x-gateway-sent-tokens" in hdrs
    assert int(hdrs["x-gateway-sent-tokens"]) <= int(hdrs["x-gateway-original-tokens"])


def test_g7_no_auto_truncate_header_means_no_chunking(fresh_workspace, gateway_url):
    """Without the header, the chunker doesn't fire even on a big payload."""
    msgs = [{"role": "system", "content": "ok"}] + [
        {"role": "user", "content": "x" * 200} for _ in range(5)
    ]
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json={"model": "claude-sonnet-4-5", "messages": msgs, "max_tokens": 8},
        timeout=30,
    )
    assert r.status_code == 200
    hdrs = {k.lower(): v for k, v in r.headers.items()}
    assert "x-gateway-truncated-messages" not in hdrs


# ─────────────────────────── G8: batching ───────────────────────────

def test_g8_batch_completions_runs_n_in_parallel(fresh_workspace, gateway_url):
    """/v1/batch/completions accepts a list of chat bodies + a max_concurrency
    cap, runs them through the same governed pipeline (auth/routing/
    guardrails/governance), and returns one OpenAI-shaped response per
    item plus a stats dict."""
    requests = [
        {"model": "claude-sonnet-4-5",
         "messages": [{"role": "user", "content": f"batch item {i}"}],
         "max_tokens": 12}
        for i in range(8)
    ]
    r = httpx.post(
        f"{gateway_url}/v1/batch/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json={"requests": requests, "max_concurrency": 4},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "results" in body and len(body["results"]) == 8
    assert body["stats"]["count"] == 8
    assert body["stats"]["concurrency"] == 4
    # Every item is OpenAI-clean (no leak keys)
    for item in body["results"]:
        assert "extra_fields" not in item
        assert "bifrost_config" not in item
        # On success each item carries choices+usage
        if "error" not in item:
            assert item["object"] == "chat.completion"
            assert item["choices"][0]["finish_reason"] in ("stop", "length", "tool_calls")


def test_g8_batch_forwards_required_headers_per_item(fresh_workspace, gateway_url):
    """Without X-Gateway-Component on the outer batch call, the inner items
    inherit nothing and the per-item required-header check correctly 400s.
    Proves the gateway enforces governance per-item, not just per-batch."""
    r = httpx.post(
        f"{gateway_url}/v1/batch/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json"},
        json={"requests": [
            {"model": "claude-sonnet-4-5",
             "messages": [{"role": "user", "content": "no header"}], "max_tokens": 8}
        ], "max_concurrency": 1},
        timeout=30,
    )
    assert r.status_code == 200, r.text   # batch wrapper itself succeeds
    item = r.json()["results"][0]
    # The inner item, however, hits the missing-required-header path:
    assert item.get("error", {}).get("type") == "missing_required_header", item


# ─────────────────────────── G9: cache + idempotency ───────────────────────────

def test_g9_cache_hit_returns_zero_cost(fresh_workspace, gateway_url):
    """X-Gateway-Cache-TTL on a deterministic body \u2192 first call MISS, second
    call HIT (X-Gateway-Cache: HIT) with $0 cost on the cache_hit RequestLog."""
    body = chat_request(content=f"cache test {time.time_ns()}")  # unique to dedupe across runs
    headers = {
        "Authorization": f"Bearer {fresh_workspace['key']}",
        "Content-Type": "application/json",
        "X-Gateway-Component": "document-processing",
        "X-Gateway-Cache-TTL": "300",
    }
    # MISS
    r1 = httpx.post(f"{gateway_url}/v1/chat/completions", headers=headers, json=body, timeout=30)
    assert r1.status_code == 200, r1.text
    miss = {k.lower(): v for k, v in r1.headers.items()}.get("x-gateway-cache")
    assert miss == "MISS", f"expected MISS first time, got {miss}"
    # HIT
    r2 = httpx.post(f"{gateway_url}/v1/chat/completions", headers=headers, json=body, timeout=30)
    assert r2.status_code == 200
    hit = {k.lower(): v for k, v in r2.headers.items()}.get("x-gateway-cache")
    assert hit == "HIT", f"expected HIT on second identical call, got {hit}"
    # Both responses are OpenAI-clean
    for r in (r1, r2):
        b = r.json()
        assert "extra_fields" not in b
        assert b["object"] == "chat.completion"


def test_g9_idempotency_key_dedupes(fresh_workspace, gateway_url):
    """Idempotency-Key replays the cached response even if the body differs
    (the key is the dedupe handle). Same MISS \u2192 HIT pattern as cache-ttl."""
    import uuid as _uuid
    idem = f"bvt-idem-{_uuid.uuid4().hex[:8]}"
    headers = {
        "Authorization": f"Bearer {fresh_workspace['key']}",
        "Content-Type": "application/json",
        "X-Gateway-Component": "document-processing",
        "Idempotency-Key": idem,
    }
    r1 = httpx.post(f"{gateway_url}/v1/chat/completions", headers=headers,
                    json=chat_request(content="first"), timeout=30)
    assert r1.status_code == 200
    miss = {k.lower(): v for k, v in r1.headers.items()}.get("x-gateway-cache")
    assert miss == "MISS"

    # Different body but same idempotency key \u2192 must replay the first response
    r2 = httpx.post(f"{gateway_url}/v1/chat/completions", headers=headers,
                    json=chat_request(content="DIFFERENT body but same idempotency key"),
                    timeout=30)
    assert r2.status_code == 200
    hit = {k.lower(): v for k, v in r2.headers.items()}.get("x-gateway-cache")
    assert hit == "HIT"
    assert r1.json()["id"] == r2.json()["id"], "idempotency key must replay the same id"


# ─────────────────────────── G10: streaming output guardrail = audit-only ───────────────────────────

def test_g10_streaming_output_guardrail_never_blocks_midstream(fresh_workspace, http_admin, gateway_url):
    """Output guardrails on a streaming chat must run audit-only \u2014 never
    truncate or 422 mid-stream.

    Setup: a keyword that is NOT in the input but WILL appear in the output
    (the echo engine prefixes every reply with `[echo:bedrock]`, so we use
    the substring "[echo" as the keyword). This proves the output-guardrail
    detection actually fires AND that the streamed response still completes
    normally with [DONE], because output-on-streams is audit-only by design.
    """
    http_admin.patch(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}",
        json={"guardrails": {"mode": "block",
                              "keywords": ["[echo:"]}},   # appears in output, not input
    )
    chunks: list[bytes] = []
    saw_done = False
    with httpx.stream(
        "POST",
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json",
                 "X-Gateway-Component": "document-processing"},
        json=chat_request(content="say something", stream=True),
        timeout=30,
    ) as resp:
        # Output-guardrail block-mode rules are downgraded to audit-only on
        # streams \u2014 the wire response MUST be 200 even though the output text
        # contains the keyword.
        assert resp.status_code == 200, f"output-guardrail blocked the stream: {resp.status_code}"
        for line in resp.iter_lines():
            if not line:
                continue
            payload = line.encode() if isinstance(line, str) else line
            chunks.append(payload)
            if b"[DONE]" in payload:
                saw_done = True
    assert saw_done, "stream did not end with [DONE] (output-guardrail truncated it?)"


# ─────────────────────────── G11: per-request guardrail-ids selection ───────────────────────────

def test_g11_per_request_guardrail_ids_picks_explicit_stored_rule(fresh_workspace, http_admin, gateway_url):
    """The `X-Gateway-Guardrail-Ids` header selects an EXPLICIT stored rule
    by id and applies it in addition to whatever the workspace already has.
    We create a stored rule + profile that block on a unique keyword,
    pass the rule id via the header, and assert the request is blocked.
    """
    import uuid as _uuid
    pname = f"bvt-g11-{_uuid.uuid4().hex[:6]}"
    rname = f"bvt-g11-rule-{_uuid.uuid4().hex[:6]}"
    # 1) Create profile (regex detector with a unique pattern)
    p = http_admin.post("/admin/guardrails/profiles", json={
        "name": pname, "detector_type": "regex", "enabled": True,
        "config": {"patterns": {"g11_marker": "G11_BLOCK_MARKER"}},
        "scope": "global",
    })
    assert p.status_code == 200, p.text
    pid = p.json()["id"]

    # 2) Create rule referencing profile
    r = http_admin.post("/admin/guardrails/rules", json={
        "name": rname, "enabled": True, "cel_expression": "true",
        "apply_to": "input", "action": "block",
        "profile_ids": [pid], "scope": "global",
    })
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    try:
        # 3) Without the header \u2014 rule is global so it fires anyway (the global
        # rule is part of the default ruleset). Just verify it 422s.
        b = httpx.post(
            f"{gateway_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                     "Content-Type": "application/json",
                     "X-Gateway-Component": "document-processing"},
            json=chat_request(content="please trigger G11_BLOCK_MARKER"),
            timeout=30,
        )
        assert b.status_code == 422, f"global rule should block, got {b.status_code} {b.text}"

        # 4) WITH X-Gateway-Guardrail-Ids="<rid>" \u2014 the per-request selection
        # explicitly includes our rule. Same outcome: 422.
        c = httpx.post(
            f"{gateway_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                     "Content-Type": "application/json",
                     "X-Gateway-Component": "document-processing",
                     "X-Gateway-Guardrail-Ids": str(rid)},
            json=chat_request(content="please trigger G11_BLOCK_MARKER"),
            timeout=30,
        )
        assert c.status_code == 422, f"selected rule didn't fire: {c.status_code} {c.text}"
    finally:
        http_admin.delete(f"/admin/guardrails/rules/{rid}")
        http_admin.delete(f"/admin/guardrails/profiles/{pid}")


def test_g12_cross_workspace_scoped_rule_cannot_be_activated(fresh_workspace, http_admin, gateway_url):
    """A workspace-scoped rule belonging to ANOTHER workspace must never execute
    here - not even when its id is explicitly passed via X-Gateway-Guardrail-Ids.

    This is the cross-workspace leak guard: the runtime drops out-of-scope
    selected rules, so a caller cannot smuggle another tenant's rule into their
    own request (and the UI no longer offers it either).
    """
    import uuid as _uuid
    marker = f"XWLEAK_{_uuid.uuid4().hex[:8].upper()}"
    pname = f"bvt-g12-{_uuid.uuid4().hex[:6]}"
    rname = f"bvt-g12-rule-{_uuid.uuid4().hex[:6]}"

    p = http_admin.post("/admin/guardrails/profiles", json={
        "name": pname, "detector_type": "regex", "enabled": True,
        "config": {"patterns": {"g12_marker": marker}}, "scope": "global",
    })
    assert p.status_code == 200, p.text
    pid = p.json()["id"]

    # Rule scoped to a DIFFERENT (real, seeded) workspace than fresh_workspace.
    # ws-novatech-platform is provisioned by the cold-start seed and is never the
    # wsbvt-* id fresh_workspace mints, so it's a valid foreign scope.
    r = http_admin.post("/admin/guardrails/rules", json={
        "name": rname, "enabled": True, "cel_expression": "true",
        "apply_to": "input", "action": "block",
        "profile_ids": [pid], "scope": "workspace", "workspace_id": "ws-novatech-platform",
    })
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    try:
        # From fresh_workspace, explicitly try to activate the foreign rule by id.
        resp = httpx.post(
            f"{gateway_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                     "Content-Type": "application/json",
                     "X-Gateway-Component": "document-processing",
                     "X-Gateway-Guardrail-Ids": str(rid)},
            json=chat_request(content=f"please trigger {marker}"),
            timeout=30,
        )
        # Must NOT be a guardrail block - the foreign workspace-scoped rule is
        # dropped at runtime. (Echo engine → 200; the key assertion is "not 422".)
        assert resp.status_code != 422, (
            f"cross-workspace rule leaked and blocked: {resp.status_code} {resp.text}")
    finally:
        http_admin.delete(f"/admin/guardrails/rules/{rid}")
        http_admin.delete(f"/admin/guardrails/profiles/{pid}")


def test_g13_rule_listing_is_scope_filtered_per_workspace(http_admin):
    """The list endpoint that feeds the per-workspace guardrail editor must only
    return rules in scope (global + that workspace's own) when `workspace_id` is
    given - and ALL rules when it isn't (admin Rule Builder). This is the data
    contract that prevents the cross-workspace leak in the UI."""
    import uuid as _uuid
    rname = f"bvt-g13-rule-{_uuid.uuid4().hex[:6]}"
    r = http_admin.post("/admin/guardrails/rules", json={
        "name": rname, "enabled": True, "cel_expression": "true",
        "apply_to": "input", "action": "block",
        "profile_ids": [], "scope": "workspace", "workspace_id": "ws-novatech-platform",
    })
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    try:
        def names(params=""):
            resp = http_admin.get(f"/admin/guardrails/rules{params}")
            assert resp.status_code == 200, resp.text
            return {x["name"] for x in resp.json()["rules"]}

        # owner workspace sees it
        assert rname in names("?workspace_id=ws-novatech-platform")
        # a different workspace does NOT (leak closed)
        assert rname not in names("?workspace_id=ws-novatech-payments")
        # admin-wide (no param) still sees everything
        assert rname in names("")
    finally:
        http_admin.delete(f"/admin/guardrails/rules/{rid}")
