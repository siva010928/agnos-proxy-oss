"""Anthropic DirectEngine adapter (WAVE 25 TRACK 3 + WAVE 26 tool support).

Calls the Anthropic Messages API directly (no Bifrost in the path), translating
OpenAI-shaped requests to Anthropic's format and back. Credentials are decrypted
from our store (same as Bedrock DirectEngine).

Scope:
  ✓ non-streaming chat parity
  ✓ tool calling (OpenAI tools/tool_choice → Anthropic tools/tool_choice;
    Anthropic tool_use blocks → OpenAI tool_calls; multi-turn with tool messages)
  ✗ streaming (deferred - falls back to bifrost in the wrapper)
  ✗ vision (deferred)

This proves we can insource provider-by-provider: rent translation from Bifrost
for most traffic, then flip a workspace/alias to direct when volume/criticality
justifies it - zero component change, identical governance, same capability.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from gateway.core.registry import ResolvedTarget
from gateway.engines.base import EngineResult

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


# ─────────────────────── Request translation ───────────────────────

def _translate_tools(openai_tools: list[dict] | None) -> list[dict]:
    """OpenAI tools schema → Anthropic tools schema.

    OpenAI:
        {"type":"function","function":{"name","description","parameters":{...}}}
    Anthropic:
        {"name","description","input_schema":{...}}
    """
    if not openai_tools:
        return []
    out: list[dict] = []
    for t in openai_tools:
        if t.get("type") != "function":
            continue
        fn = t.get("function", {})
        if not fn.get("name"):
            continue
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


def _translate_tool_choice(openai_choice: Any) -> dict | None:
    """OpenAI tool_choice → Anthropic tool_choice.

    OpenAI: "auto" | "none" | "required" | {"type":"function","function":{"name":"X"}}
    Anthropic: {"type":"auto"} | {"type":"any"} | {"type":"tool","name":"X"} | None for "none"
    """
    if openai_choice is None or openai_choice == "auto":
        return {"type": "auto"}
    if openai_choice == "none":
        return None      # signal: don't send tools at all
    if openai_choice == "required":
        return {"type": "any"}
    if isinstance(openai_choice, dict):
        if openai_choice.get("type") == "function":
            name = openai_choice.get("function", {}).get("name")
            if name:
                return {"type": "tool", "name": name}
    return {"type": "auto"}


def _translate_message(msg: dict) -> dict | None:
    """Translate one OpenAI message → Anthropic message (or None to drop).

    Handles:
      - system: handled separately by caller
      - user / assistant text
      - assistant with tool_calls (→ assistant message with tool_use content blocks)
      - tool role (→ user message with tool_result content block)
    """
    role = msg.get("role")
    content = msg.get("content")

    if role == "system":
        return None   # extracted separately by translator

    # Tool result message (OpenAI: role="tool", tool_call_id, content)
    if role == "tool":
        return {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id"),
                "content": content if isinstance(content, str) else json.dumps(content),
            }],
        }

    # Assistant with tool_calls
    if role == "assistant" and msg.get("tool_calls"):
        blocks: list[dict] = []
        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        for tc in msg["tool_calls"]:
            fn = tc.get("function", {})
            try:
                tool_input = json.loads(fn.get("arguments", "{}") or "{}")
            except Exception:
                tool_input = {}
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": fn.get("name", ""),
                "input": tool_input,
            })
        return {"role": "assistant", "content": blocks}

    # Plain user/assistant text
    if isinstance(content, str):
        return {"role": role, "content": content}
    if isinstance(content, list):
        # OpenAI multipart content (e.g. text + image_url) - pass text only for now (vision deferred)
        text = "".join(p.get("text", "") for p in content if p.get("type") == "text")
        return {"role": role, "content": text}
    return {"role": role, "content": str(content or "")}


def _wants_prompt_cache(openai_request: dict) -> bool:
    """Opt-in Anthropic prompt caching - a provider-native feature the generic
    OpenAI translation layer flattens away. Enabled by a top-level ``prompt_cache``
    flag (OpenAI extra-field passthrough) or the gateway header surfaced onto the
    body as ``_agnos_prompt_cache``."""
    return bool(openai_request.get("prompt_cache") or openai_request.get("_agnos_prompt_cache"))


def _thinking_block(openai_request: dict) -> dict | None:
    """Map extended-thinking intent → Anthropic ``thinking`` (another native
    capability the OpenAI shape has no first-class field for). Honors an explicit
    ``thinking`` object, or OpenAI's ``reasoning_effort`` (low/medium/high)."""
    th = openai_request.get("thinking")
    if isinstance(th, dict) and th.get("type") == "enabled":
        return th
    effort = openai_request.get("reasoning_effort")
    budget = {"low": 2048, "medium": 8192, "high": 16384}.get(str(effort or "").lower())
    if budget:
        return {"type": "enabled", "budget_tokens": budget}
    return None


def _translate_request(openai_request: dict) -> dict:
    """OpenAI chat-completions body → Anthropic Messages body.
    Supports tools, tool_choice, tool-result messages, prompt caching and
    extended thinking (the last two are provider-native features exposed THROUGH
    the boundary without the component changing its OpenAI-shaped request)."""
    messages = openai_request.get("messages", [])
    system_parts = [m["content"] for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str)]
    translated: list[dict] = []
    for m in messages:
        t = _translate_message(m)
        if t is not None:
            translated.append(t)

    body: dict[str, Any] = {
        "model": openai_request.get("model", "claude-sonnet-4-5-20250929"),
        "messages": translated,
        "max_tokens": openai_request.get("max_tokens", 1024),
    }
    cache = _wants_prompt_cache(openai_request)
    if system_parts:
        system_text = "\n".join(system_parts)
        if cache:
            # cache the (usually large, stable) system prompt as an ephemeral block
            body["system"] = [{"type": "text", "text": system_text,
                               "cache_control": {"type": "ephemeral"}}]
        else:
            body["system"] = system_text
    if openai_request.get("temperature") is not None:
        body["temperature"] = openai_request["temperature"]
    if openai_request.get("top_p") is not None:
        body["top_p"] = openai_request["top_p"]

    think = _thinking_block(openai_request)
    if think:
        body["thinking"] = think
        body.pop("temperature", None)  # thinking requires default temperature

    # ── Tools ──
    raw_tools = openai_request.get("tools") or []
    raw_tool_choice = openai_request.get("tool_choice")
    if raw_tools and raw_tool_choice != "none":
        anthropic_tools = _translate_tools(raw_tools)
        if anthropic_tools:
            if cache and anthropic_tools:
                anthropic_tools[-1]["cache_control"] = {"type": "ephemeral"}
            body["tools"] = anthropic_tools
            tc = _translate_tool_choice(raw_tool_choice)
            if tc is not None:
                body["tool_choice"] = tc

    return body


# ─────────────────────── Response translation ───────────────────────

def _translate_response(anthropic_resp: dict, model: str) -> dict:
    """Anthropic Messages response → OpenAI chat-completions shape.
    Handles text + tool_use content blocks."""
    content_blocks = anthropic_resp.get("content", []) or []

    # Concatenate any text blocks; collect any tool_use blocks
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for b in content_blocks:
        btype = b.get("type")
        if btype == "text":
            text_parts.append(b.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append({
                "index": len(tool_calls),
                "id": b.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": b.get("name", ""),
                    "arguments": json.dumps(b.get("input") or {}),
                },
            })

    text = "".join(text_parts)
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = anthropic_resp.get("usage", {})
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    usage_out = {
        "prompt_tokens": inp,
        "completion_tokens": out,
        "total_tokens": inp + out,
    }
    # Surface prompt-cache accounting so the dashboard/demo can SHOW the native
    # cache working (cache_read/creation are Anthropic-specific - the generic
    # OpenAI layer drops them). OpenAI's own prompt_tokens_details mirror shape.
    cache_read = usage.get("cache_read_input_tokens")
    cache_write = usage.get("cache_creation_input_tokens")
    if cache_read is not None or cache_write is not None:
        usage_out["prompt_tokens_details"] = {"cached_tokens": int(cache_read or 0)}
        usage_out["cache_read_input_tokens"] = int(cache_read or 0)
        usage_out["cache_creation_input_tokens"] = int(cache_write or 0)
    return {
        "id": f"chatcmpl-direct-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": _map_stop_reason(anthropic_resp.get("stop_reason")),
        }],
        "usage": usage_out,
    }


def _map_stop_reason(reason: str | None) -> str:
    if reason == "end_turn":
        return "stop"
    if reason == "max_tokens":
        return "length"
    if reason == "tool_use":
        return "tool_calls"
    if reason == "stop_sequence":
        return "stop"
    return "stop"


# ─────────────────────── Caller ───────────────────────

async def anthropic_chat(openai_request: dict, target: ResolvedTarget) -> EngineResult:
    """Call the Anthropic Messages API directly with our stored credentials.
    No Bifrost in the path - credential is decrypted from our encrypted store
    and used for a single httpx call."""
    creds = target.credentials or {}
    api_key = creds.get("api_key", "")
    if not api_key:
        return EngineResult(
            body={"error": {"message": "No api_key in decrypted credentials", "type": "auth_error"}},
            status_code=401)

    request_body = _translate_request(openai_request)
    request_body["model"] = target.model_id   # the provider-specific model id

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(ANTHROPIC_API_URL, headers=headers, json=request_body)

    if r.status_code != 200:
        try:
            err = r.json()
            msg = err.get("error", {}).get("message", r.text[:200])
        except Exception:
            msg = r.text[:200]
        return EngineResult(
            body={"error": {"message": msg, "type": "provider_error"}},
            status_code=r.status_code)

    body = _translate_response(r.json(), target.model_id)
    return EngineResult(body=body, status_code=200)


# ─────────────────────── Streaming ───────────────────────

async def anthropic_chat_stream(openai_request: dict, target: ResolvedTarget):
    """Real Anthropic Messages streaming → OpenAI SSE chunks (no Bifrost).

    Parses Anthropic's native SSE (message_start / content_block_delta /
    message_delta / message_stop, incl. tool_use input_json_delta) and re-emits
    OpenAI ``chat.completion.chunk`` frames + a final usage chunk + [DONE].
    """
    from gateway.engines import direct_common as dc

    creds = target.credentials or {}
    api_key = creds.get("api_key", "")
    model = target.model_id
    if not api_key:
        yield dc.sse(dc.chunk(model, delta={"content": ""}, finish="stop"))
        yield dc.DONE
        return

    request_body = _translate_request(openai_request)
    request_body["model"] = model
    request_body["stream"] = True
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}

    cid = f"chatcmpl-direct-{uuid.uuid4().hex[:12]}"
    in_tok = 0
    out_tok = 0
    cache_read = 0
    finish = "stop"
    # tool-call streaming state: content-block index → assembled tool_call meta
    tool_idx: dict[int, int] = {}
    emitted_role = False

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", ANTHROPIC_API_URL, headers=headers,
                                     json=request_body) as r:
                if r.status_code != 200:
                    raw = (await r.aread()).decode("utf-8", "ignore")
                    _s, body = dc.classify_http(r.status_code, raw[:300])
                    yield dc.sse(dc.chunk(model, delta={"content": body["error"]["message"]}, finish="stop"))
                    yield dc.DONE
                    return
                if not emitted_role:
                    yield dc.sse(dc.chunk(model, delta={"role": "assistant", "content": ""}, cid=cid))
                    emitted_role = True
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    etype = ev.get("type")
                    if etype == "message_start":
                        u = (ev.get("message", {}) or {}).get("usage", {}) or {}
                        in_tok = u.get("input_tokens", 0) or 0
                        cache_read = u.get("cache_read_input_tokens", 0) or 0
                    elif etype == "content_block_start":
                        cb = ev.get("content_block", {}) or {}
                        if cb.get("type") == "tool_use":
                            idx = ev.get("index", 0)
                            n = len(tool_idx)
                            tool_idx[idx] = n
                            yield dc.sse(dc.chunk(model, delta={}, cid=cid, tool_calls=[{
                                "index": n, "id": cb.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                                "type": "function",
                                "function": {"name": cb.get("name", ""), "arguments": ""}}]))
                    elif etype == "content_block_delta":
                        delta = ev.get("delta", {}) or {}
                        dtype = delta.get("type")
                        if dtype == "text_delta" and delta.get("text"):
                            yield dc.sse(dc.chunk(model, delta={"content": delta["text"]}, cid=cid))
                        elif dtype == "input_json_delta":
                            idx = ev.get("index", 0)
                            n = tool_idx.get(idx, 0)
                            yield dc.sse(dc.chunk(model, delta={}, cid=cid, tool_calls=[{
                                "index": n,
                                "function": {"arguments": delta.get("partial_json", "")}}]))
                    elif etype == "message_delta":
                        d = ev.get("delta", {}) or {}
                        finish = _map_stop_reason(d.get("stop_reason")) or finish
                        u = ev.get("usage", {}) or {}
                        out_tok = u.get("output_tokens", out_tok) or out_tok
                    elif etype == "message_stop":
                        break
    except Exception as exc:  # noqa: BLE001
        _s, body = dc.exc_to_openai(exc, "anthropic")
        yield dc.sse(dc.chunk(model, delta={"content": ""}, finish="stop"))
        yield dc.DONE
        return

    usage = dc.usage_block(in_tok, out_tok)
    if cache_read:
        usage["prompt_tokens_details"] = {"cached_tokens": int(cache_read)}
    yield dc.sse(dc.chunk(model, delta={}, finish=finish, usage=usage, cid=cid))
    yield dc.DONE
