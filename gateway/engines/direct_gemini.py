"""DirectEngine - Google Gemini adapter (AI Studio REST, owned, no Bifrost).

Chat (generateContent) + real streaming (streamGenerateContent?alt=sse) +
embeddings (embedContent / batchEmbedContents) via the AI-Studio REST API using
the decrypted api key. Translates OpenAI ⇄ Gemini including tools/function-calls.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from gateway.core.registry import ResolvedTarget
from gateway.engines import direct_common as dc
from gateway.engines.base import EngineResult

_BASE = "https://generativelanguage.googleapis.com/v1beta"
_FINISH = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "content_filter",
           "RECITATION": "content_filter", "OTHER": "stop"}


def _model_path(model_id: str) -> str:
    return model_id if model_id.startswith("models/") else f"models/{model_id}"


def _to_gemini(messages: list[dict]) -> tuple[dict | None, list[dict]]:
    system: dict | None = None
    contents: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system" and isinstance(content, str):
            system = {"parts": [{"text": content}]}
            continue
        if role == "tool":
            contents.append({"role": "user", "parts": [{"functionResponse": {
                "name": m.get("name") or m.get("tool_call_id") or "tool",
                "response": {"result": content if isinstance(content, str) else content}}}]})
            continue
        if role == "assistant" and m.get("tool_calls"):
            parts: list[dict] = []
            if isinstance(content, str) and content:
                parts.append({"text": content})
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}") or "{}")
                except Exception:
                    args = {}
                parts.append({"functionCall": {"name": fn.get("name", ""), "args": args}})
            contents.append({"role": "model", "parts": parts})
            continue
        text = content if isinstance(content, str) else "".join(
            p.get("text", "") for p in (content or []) if isinstance(p, dict) and p.get("type") == "text")
        contents.append({"role": "model" if role == "assistant" else "user",
                         "parts": [{"text": text}]})
    return system, contents


def _tools(openai_request: dict) -> tuple[list[dict] | None, dict | None]:
    tools = openai_request.get("tools") or []
    choice = openai_request.get("tool_choice")
    if not tools or choice == "none":
        return None, None
    decls = []
    for t in tools:
        if t.get("type") != "function":
            continue
        fn = t.get("function", {})
        if fn.get("name"):
            decls.append({"name": fn["name"], "description": fn.get("description", ""),
                          "parameters": fn.get("parameters", {"type": "object", "properties": {}})})
    if not decls:
        return None, None
    mode = "AUTO"
    allowed = None
    if choice == "required":
        mode = "ANY"
    elif isinstance(choice, dict) and choice.get("type") == "function":
        mode, allowed = "ANY", [choice["function"]["name"]]
    fc: dict[str, Any] = {"mode": mode}
    if allowed:
        fc["allowedFunctionNames"] = allowed
    return [{"functionDeclarations": decls}], {"functionCallingConfig": fc}


def _gen_config(openai_request: dict) -> dict:
    gc: dict[str, Any] = {"maxOutputTokens": openai_request.get("max_tokens", 1024)}
    if openai_request.get("temperature") is not None:
        gc["temperature"] = openai_request["temperature"]
    if openai_request.get("top_p") is not None:
        gc["topP"] = openai_request["top_p"]
    return gc


def _build_body(openai_request: dict) -> dict:
    system, contents = _to_gemini(openai_request.get("messages", []))
    body: dict[str, Any] = {"contents": contents, "generationConfig": _gen_config(openai_request)}
    if system:
        body["systemInstruction"] = system
    tools, tool_cfg = _tools(openai_request)
    if tools:
        body["tools"] = tools
        if tool_cfg:
            body["toolConfig"] = tool_cfg
    return body


def _parts_to_openai(cand: dict) -> tuple[str, list[dict]]:
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for p in (cand.get("content", {}) or {}).get("parts", []) or []:
        if "text" in p:
            text_parts.append(p["text"])
        elif "functionCall" in p:
            fcall = p["functionCall"]
            tool_calls.append({"index": len(tool_calls), "id": f"call_{uuid.uuid4().hex[:12]}",
                               "type": "function",
                               "function": {"name": fcall.get("name", ""),
                                            "arguments": json.dumps(fcall.get("args") or {})}})
    return "".join(text_parts), tool_calls


async def gemini_chat(openai_request: dict, target: ResolvedTarget) -> EngineResult:
    from gateway.engines.direct_engine import _openai_body
    api_key = (target.credentials or {}).get("api_key", "")
    if not api_key:
        return EngineResult({"error": {"message": "No api_key for gemini", "type": "provider_auth_error"}}, 502)
    url = f"{_BASE}/{_model_path(target.model_id)}:generateContent?key={api_key}"
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(url, json=_build_body(openai_request))
    except Exception as exc:  # noqa: BLE001
        status, body = dc.exc_to_openai(exc, "gemini")
        return EngineResult(body, status)
    if r.status_code != 200:
        status, body = dc.classify_http(r.status_code, r.text[:300])
        return EngineResult(body, status)
    data = r.json()
    cand = (data.get("candidates") or [{}])[0]
    text, tool_calls = _parts_to_openai(cand)
    um = data.get("usageMetadata", {}) or {}
    finish = "tool_calls" if tool_calls else _FINISH.get(cand.get("finishReason", ""), "stop")
    body = _openai_body(target.model_id, text,
                        {"inputTokens": um.get("promptTokenCount", 0),
                         "outputTokens": um.get("candidatesTokenCount", 0),
                         "totalTokens": um.get("totalTokenCount", 0)}, finish)
    if tool_calls:
        body["choices"][0]["message"]["tool_calls"] = tool_calls
        if not text:
            body["choices"][0]["message"]["content"] = None
    return EngineResult(body)


async def gemini_chat_stream(openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
    model = target.model_id
    cid = f"chatcmpl-direct-{uuid.uuid4().hex[:12]}"
    api_key = (target.credentials or {}).get("api_key", "")
    if not api_key:
        yield dc.sse(dc.chunk(model, delta={"content": ""}, finish="stop"))
        yield dc.DONE
        return
    url = f"{_BASE}/{_model_path(model)}:streamGenerateContent?alt=sse&key={api_key}"
    in_tok = out_tok = 0
    finish = "stop"
    tool_n = -1
    yield dc.sse(dc.chunk(model, delta={"role": "assistant", "content": ""}, cid=cid))
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream("POST", url, json=_build_body(openai_request)) as r:
                if r.status_code != 200:
                    raw = (await r.aread()).decode("utf-8", "ignore")
                    _s, body = dc.classify_http(r.status_code, raw[:300])
                    yield dc.sse(dc.chunk(model, delta={"content": body["error"]["message"]}, cid=cid, finish="stop"))
                    yield dc.DONE
                    return
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    cand = (data.get("candidates") or [{}])[0]
                    for p in (cand.get("content", {}) or {}).get("parts", []) or []:
                        if "text" in p and p["text"]:
                            yield dc.sse(dc.chunk(model, delta={"content": p["text"]}, cid=cid))
                        elif "functionCall" in p:
                            tool_n += 1
                            fcall = p["functionCall"]
                            yield dc.sse(dc.chunk(model, delta={}, cid=cid, tool_calls=[{
                                "index": tool_n, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                                "function": {"name": fcall.get("name", ""),
                                             "arguments": json.dumps(fcall.get("args") or {})}}]))
                            finish = "tool_calls"
                    if cand.get("finishReason") and finish != "tool_calls":
                        finish = _FINISH.get(cand["finishReason"], "stop")
                    um = data.get("usageMetadata", {}) or {}
                    in_tok = um.get("promptTokenCount", in_tok) or in_tok
                    out_tok = um.get("candidatesTokenCount", out_tok) or out_tok
    except Exception as exc:  # noqa: BLE001
        _s, body = dc.exc_to_openai(exc, "gemini")
        yield dc.sse(dc.chunk(model, delta={"content": ""}, cid=cid, finish="stop"))
        yield dc.DONE
        return
    yield dc.sse(dc.chunk(model, delta={}, finish=finish, usage=dc.usage_block(in_tok, out_tok), cid=cid))
    yield dc.DONE


async def gemini_embeddings(openai_request: dict, target: ResolvedTarget) -> EngineResult:
    model = target.model_id
    api_key = (target.credentials or {}).get("api_key", "")
    if not api_key:
        return EngineResult({"error": {"message": "No api_key for gemini", "type": "provider_auth_error"}}, 502)
    inp = openai_request.get("input")
    texts = [inp] if isinstance(inp, str) else [str(x) for x in (inp or [])]
    dims = openai_request.get("dimensions")
    mp = _model_path(model)
    req = {"requests": [{"model": mp, "content": {"parts": [{"text": t}]}} for t in texts]}
    if dims and dc.supports_dimensions(model):
        for r0 in req["requests"]:
            r0["outputDimensionality"] = int(dims)
    url = f"{_BASE}/{mp}:batchEmbedContents?key={api_key}"
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, json=req)
    except Exception as exc:  # noqa: BLE001
        status, body = dc.exc_to_openai(exc, "gemini")
        return EngineResult(body, status)
    if r.status_code != 200:
        status, body = dc.classify_http(r.status_code, r.text[:300])
        return EngineResult(body, status)
    data = r.json()
    vectors = [e.get("values", []) for e in data.get("embeddings", [])]
    return EngineResult(dc.embeddings_body(model, vectors, 0))
