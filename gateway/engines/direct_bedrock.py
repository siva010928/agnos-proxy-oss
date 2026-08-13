"""DirectEngine - AWS Bedrock adapter (owned, in-process, no Bifrost).

Bedrock Converse (chat) + ConverseStream (real streaming) + Titan/Cohere
embeddings via boto3, with credentials decrypted from our store. Supports the
three auth modes Agnos Proxy uses today (static keys / Bedrock bearer API key /
SSO profile), region-from-inference-profile-prefix resolution, and tool calling.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from gateway.config import settings
from gateway.core.registry import ResolvedTarget
from gateway.engines import direct_common as dc
from gateway.engines.base import EngineResult

# inference-profile prefix → default region (overridden by explicit creds/config)
_PREFIX_REGION = {"us": "us-east-1", "eu": "eu-west-1", "ap": "ap-northeast-1",
                  "au": "ap-southeast-2", "global": "us-east-1"}


def _region(creds: dict, cfg: dict, model_id: str) -> str:
    r = creds.get("region") or cfg.get("region")
    if r:
        return r
    prefix = (model_id or "").split(".", 1)[0]
    return _PREFIX_REGION.get(prefix, settings.aws_region_name or "us-east-1")


def _session(creds: dict, cfg: dict):
    import boto3
    profile = creds.get("profile_name") or cfg.get("profile_name")
    if (creds.get("auth_type") == "sso" or cfg.get("auth_type") == "sso") and profile:
        return boto3.Session(profile_name=profile)
    bearer = creds.get("bedrock_api_key") or creds.get("api_key") or settings.aws_bedrock_api_key
    # A Bedrock bearer key is only usable when there are NO static keys (static
    # wins if present). boto3 reads AWS_BEARER_TOKEN_BEDROCK from the env.
    if bearer and not creds.get("access_key"):
        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = bearer
        return boto3.Session()
    return boto3.Session(
        aws_access_key_id=creds.get("access_key"),
        aws_secret_access_key=creds.get("secret_key"),
        aws_session_token=creds.get("session_token"),
    )


# Cache boto3 clients so we don't pay session/client construction (and the
# one-time botocore data load, ~1-3s) on every call. This is part of why the
# in-process DirectEngine is faster than the rented Bifrost hop: a warm client +
# no extra network round-trip. Keyed by (service, region, auth-signature).
_CLIENT_CACHE: dict[tuple, object] = {}


def _client(target: ResolvedTarget, svc: str = "bedrock-runtime"):
    creds = target.credentials or {}
    cfg = target.config or {}
    region = _region(creds, cfg, target.model_id)
    sig = (svc, region, creds.get("auth_type") or "",
           (creds.get("access_key") or creds.get("bedrock_api_key") or creds.get("profile_name") or "")[:8])
    cached = _CLIENT_CACHE.get(sig)
    if cached is not None:
        return cached
    client = _session(creds, cfg).client(svc, region_name=region)
    _CLIENT_CACHE[sig] = client
    return client


def prewarm() -> None:
    """Best-effort: build a Bedrock client from env creds at startup so the
    one-time botocore data load (~1-3s) doesn't land on the first real DirectEngine
    call (which otherwise shows up as a cold-start latency outlier)."""
    try:
        import boto3
        region = settings.aws_region_name or "us-east-1"
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            sess = boto3.Session(aws_access_key_id=settings.aws_access_key_id,
                                 aws_secret_access_key=settings.aws_secret_access_key)
        elif settings.aws_bedrock_api_key:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = settings.aws_bedrock_api_key
            sess = boto3.Session()
        else:
            return
        sess.client("bedrock-runtime", region_name=region)  # triggers botocore data load
    except Exception:  # noqa: BLE001
        pass


# ── request/response translation (OpenAI ⇄ Bedrock Converse) ─────────────────
def _blocks(content: Any) -> list[dict]:
    if isinstance(content, str):
        return [{"text": content}]
    out: list[dict] = []
    for p in content or []:
        if isinstance(p, dict) and p.get("type") == "text":
            out.append({"text": p.get("text", "")})
    return out or [{"text": ""}]


def _to_converse(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    system: list[dict] = []
    conv: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if isinstance(content, str):
                system.append({"text": content})
            continue
        if role == "tool":
            conv.append({"role": "user", "content": [{"toolResult": {
                "toolUseId": m.get("tool_call_id"),
                "content": [{"text": content if isinstance(content, str) else json.dumps(content)}],
            }}]})
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks: list[dict] = []
            if isinstance(content, str) and content:
                blocks.append({"text": content})
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                try:
                    inp = json.loads(fn.get("arguments", "{}") or "{}")
                except Exception:
                    inp = {}
                blocks.append({"toolUse": {"toolUseId": tc.get("id") or f"tool_{uuid.uuid4().hex[:8]}",
                                           "name": fn.get("name", ""), "input": inp}})
            conv.append({"role": "assistant", "content": blocks})
            continue
        conv.append({"role": role if role in ("user", "assistant") else "user",
                     "content": _blocks(content)})
    return system, conv


def _tool_config(openai_request: dict) -> dict | None:
    tools = openai_request.get("tools") or []
    choice = openai_request.get("tool_choice")
    if not tools or choice == "none":
        return None
    specs = []
    for t in tools:
        if t.get("type") != "function":
            continue
        fn = t.get("function", {})
        if not fn.get("name"):
            continue
        specs.append({"toolSpec": {"name": fn["name"], "description": fn.get("description", ""),
                                   "inputSchema": {"json": fn.get("parameters", {"type": "object", "properties": {}})}}})
    if not specs:
        return None
    cfg: dict[str, Any] = {"tools": specs}
    if choice == "required":
        cfg["toolChoice"] = {"any": {}}
    elif isinstance(choice, dict) and choice.get("type") == "function":
        cfg["toolChoice"] = {"tool": {"name": choice["function"]["name"]}}
    else:
        cfg["toolChoice"] = {"auto": {}}
    return cfg


def _inference_config(openai_request: dict) -> dict:
    ic: dict[str, Any] = {"maxTokens": openai_request.get("max_tokens", 1024)}
    if openai_request.get("temperature") is not None:
        ic["temperature"] = openai_request["temperature"]
    if openai_request.get("top_p") is not None:
        ic["topP"] = openai_request["top_p"]
    return ic


_STOP = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls",
         "stop_sequence": "stop", "content_filtered": "content_filter"}


def _converse_message_to_openai(msg: dict) -> tuple[str, list[dict]]:
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for b in msg.get("content", []) or []:
        if "text" in b:
            text_parts.append(b["text"])
        elif "toolUse" in b:
            tu = b["toolUse"]
            tool_calls.append({"index": len(tool_calls), "id": tu.get("toolUseId"),
                               "type": "function",
                               "function": {"name": tu.get("name", ""),
                                            "arguments": json.dumps(tu.get("input") or {})}})
    return "".join(text_parts), tool_calls


def _build_kwargs(openai_request: dict, target: ResolvedTarget) -> dict:
    system, conv = _to_converse(openai_request.get("messages", []))
    kwargs: dict[str, Any] = {"modelId": target.model_id, "messages": conv,
                              "inferenceConfig": _inference_config(openai_request)}
    if system:
        kwargs["system"] = system
    tc = _tool_config(openai_request)
    if tc:
        kwargs["toolConfig"] = tc
    return kwargs


# ── chat (non-stream) ────────────────────────────────────────────────────────
async def bedrock_chat(openai_request: dict, target: ResolvedTarget) -> EngineResult:
    from gateway.engines.direct_engine import _openai_body  # extra_fields lives there

    def _call():
        return _client(target).converse(**_build_kwargs(openai_request, target))

    try:
        resp = await asyncio.to_thread(_call)
    except Exception as exc:  # noqa: BLE001
        status, body = dc.exc_to_openai(exc, "bedrock")
        return EngineResult(body, status)

    out_msg = resp.get("output", {}).get("message", {})
    text, tool_calls = _converse_message_to_openai(out_msg)
    finish = _STOP.get(resp.get("stopReason", ""), "stop")
    usage = resp.get("usage", {}) or {}
    body = _openai_body(target.model_id, text,
                        {"inputTokens": usage.get("inputTokens", 0),
                         "outputTokens": usage.get("outputTokens", 0),
                         "totalTokens": usage.get("totalTokens", 0)},
                        "tool_calls" if tool_calls else finish)
    if tool_calls:
        body["choices"][0]["message"]["tool_calls"] = tool_calls
        if not text:
            body["choices"][0]["message"]["content"] = None
    return EngineResult(body)


# ── chat (real streaming via converse_stream) ────────────────────────────────
async def bedrock_chat_stream(openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
    model = target.model_id
    cid = f"chatcmpl-direct-{uuid.uuid4().hex[:12]}"
    q: "queue.Queue" = queue.Queue()
    _SENTINEL = object()

    def _worker():
        try:
            resp = _client(target).converse_stream(**_build_kwargs(openai_request, target))
            for ev in resp.get("stream", []):
                q.put(ev)
        except Exception as exc:  # noqa: BLE001
            q.put(("__error__", exc))
        finally:
            q.put(_SENTINEL)

    threading.Thread(target=_worker, daemon=True).start()

    yield dc.sse(dc.chunk(model, delta={"role": "assistant", "content": ""}, cid=cid))
    in_tok = out_tok = 0
    finish = "stop"
    tool_n = -1
    loop = asyncio.get_event_loop()
    while True:
        ev = await loop.run_in_executor(None, q.get)
        if ev is _SENTINEL:
            break
        if isinstance(ev, tuple) and ev and ev[0] == "__error__":
            _s, body = dc.exc_to_openai(ev[1], "bedrock")
            yield dc.sse(dc.chunk(model, delta={"content": body["error"]["message"]}, cid=cid, finish="stop"))
            break
        if "contentBlockStart" in ev:
            start = ev["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                tool_n += 1
                tu = start["toolUse"]
                yield dc.sse(dc.chunk(model, delta={}, cid=cid, tool_calls=[{
                    "index": tool_n, "id": tu.get("toolUseId"), "type": "function",
                    "function": {"name": tu.get("name", ""), "arguments": ""}}]))
        elif "contentBlockDelta" in ev:
            delta = ev["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                yield dc.sse(dc.chunk(model, delta={"content": delta["text"]}, cid=cid))
            elif "toolUse" in delta and tool_n >= 0:
                yield dc.sse(dc.chunk(model, delta={}, cid=cid, tool_calls=[{
                    "index": tool_n, "function": {"arguments": delta["toolUse"].get("input", "")}}]))
        elif "messageStop" in ev:
            finish = _STOP.get(ev["messageStop"].get("stopReason", ""), "stop")
        elif "metadata" in ev:
            u = ev["metadata"].get("usage", {}) or {}
            in_tok = u.get("inputTokens", in_tok)
            out_tok = u.get("outputTokens", out_tok)

    yield dc.sse(dc.chunk(model, delta={}, finish=finish, usage=dc.usage_block(in_tok, out_tok), cid=cid))
    yield dc.DONE


# ── embeddings (Titan + Cohere) ──────────────────────────────────────────────
def _embed_inputs(openai_request: dict) -> list[str]:
    inp = openai_request.get("input")
    if isinstance(inp, str):
        return [inp]
    return [str(x) for x in (inp or [])]


async def bedrock_embeddings(openai_request: dict, target: ResolvedTarget) -> EngineResult:
    model = target.model_id
    texts = _embed_inputs(openai_request)
    dims = openai_request.get("dimensions")
    is_cohere = "cohere" in model.lower()

    def _call() -> tuple[list[list[float]], int]:
        client = _client(target)
        vectors: list[list[float]] = []
        total_tokens = 0
        if is_cohere:
            payload = {"texts": texts, "input_type": "search_document"}
            resp = client.invoke_model(modelId=model, body=json.dumps(payload))
            data = json.loads(resp["body"].read())
            vectors = data.get("embeddings", [])
        else:  # Amazon Titan - one text per invoke
            for t in texts:
                payload: dict[str, Any] = {"inputText": t}
                if dims and dc.supports_dimensions(model):
                    payload["dimensions"] = int(dims)
                    payload["normalize"] = True
                resp = client.invoke_model(modelId=model, body=json.dumps(payload))
                data = json.loads(resp["body"].read())
                vectors.append(data.get("embedding", []))
                total_tokens += int(data.get("inputTextTokenCount", 0) or 0)
        return vectors, total_tokens

    try:
        vectors, tokens = await asyncio.to_thread(_call)
    except Exception as exc:  # noqa: BLE001
        status, body = dc.exc_to_openai(exc, "bedrock")
        return EngineResult(body, status)
    return EngineResult(dc.embeddings_body(model, vectors, tokens))
