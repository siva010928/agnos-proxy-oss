"""EchoEngine \u2014 deterministic, $0 upstream for integration tests and dev.

Returns OpenAI-shaped chat/embedding responses without contacting any provider.
Honors `stream`, `tools`, `model`, `max_tokens`, `stream_options.include_usage`,
embedding `dimensions` and `encoding_format=base64`, and multimodal text parts.
Unknown request fields (`seed`, `service_tier`, future params) are
forward-compat by being ignored \u2014 passthrough is the boundary contract.

Selected via `ENGINE=echo`; the entire WAVE 19 BVT suite runs against this.
"""
from __future__ import annotations

import asyncio
import base64
import json
import struct
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from gateway.core.registry import ResolvedTarget
from gateway.engines.base import BackendEngine, EngineResult


def _approx_tokens(text: str) -> int:
    """Cheap, deterministic token approximation (~4 chars/token; min 1)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _last_user_message(openai_request: dict) -> str:
    msgs = openai_request.get("messages") or []
    for m in reversed(msgs):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # multimodal: extract text parts only
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                return " ".join(parts)
    return ""


def _has_image(openai_request: dict) -> bool:
    """True if any user message contains an image_url part (multimodal)."""
    for m in openai_request.get("messages") or []:
        content = m.get("content")
        if isinstance(content, list):
            for p in content:
                if isinstance(p, dict) and p.get("type") in ("image_url", "image"):
                    return True
    return False


def _build_chat_body(openai_request: dict, target: ResolvedTarget, *, stream: bool = False) -> dict[str, Any]:
    user_text = _last_user_message(openai_request)
    # Deterministic echo with a small prefix so tests can assert routing reached us.
    suffix = " (vision)" if _has_image(openai_request) else ""
    reply = f"[echo:{target.provider}]{suffix} {user_text}".strip()
    # Honor max_tokens by clipping the reply length (~4 chars per token).
    max_tokens = openai_request.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        reply = reply[: max_tokens * 4]

    prompt_tokens = sum(_approx_tokens(str(m.get("content", ""))) for m in openai_request.get("messages") or [])
    completion_tokens = _approx_tokens(reply)

    finish_reason = "stop"
    tool_calls = None
    if openai_request.get("tools"):
        tool_choice = openai_request.get("tool_choice", "auto")
        # Only emit tool_calls when allowed by tool_choice
        if tool_choice != "none":
            wanted = openai_request["tools"]
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                # Specific function requested
                fname = (tool_choice.get("function") or {}).get("name")
                wanted = [t for t in wanted if (t.get("function") or {}).get("name") == fname] or wanted[:1]
            n_calls = 1
            if tool_choice == "required":
                n_calls = min(2, len(wanted)) if len(wanted) > 1 else 1
            tool_calls = []
            for i, tool_def in enumerate(wanted[:n_calls]):
                fn = (tool_def or {}).get("function") or {}
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": fn.get("name", "echo_tool"),
                        "arguments": json.dumps({"echo": user_text[:64], "n": i}),
                    },
                })
            finish_reason = "tool_calls"

    message: dict[str, Any] = {"role": "assistant", "content": None if tool_calls else reply}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": f"chatcmpl-echo-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk" if stream else "chat.completion",
        "created": int(time.time()),
        "model": target.model_id,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class EchoEngine(BackendEngine):
    """Deterministic in-process upstream. No network, no cost."""

    name = "echo"

    async def chat(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        # Tiny await so the call is genuinely async and yields to the loop
        # (matches real engines' scheduling shape; lets tests observe ordering).
        await asyncio.sleep(0)
        body = _build_chat_body(openai_request, target, stream=False)
        return EngineResult(body=body, status_code=200)

    async def chat_stream(self, openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
        # Build full response first \u2014 then stream it as deltas.
        full = _build_chat_body(openai_request, target, stream=True)
        choice = full["choices"][0]
        message = choice["message"]
        tool_calls = message.get("tool_calls")
        reply = (message.get("content") or "")

        base = {
            "id": full["id"],
            "object": "chat.completion.chunk",
            "created": full["created"],
            "model": full["model"],
        }
        # Role chunk first (always)
        role_chunk = {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
        yield f"data: {json.dumps(role_chunk)}\n\n".encode()

        if tool_calls:
            # ── Streaming tool-call argument reassembly (G3) ──
            # Emit each tool_call's `arguments` as MULTIPLE delta fragments so
            # tests prove the client/gateway can reassemble JSON across chunks.
            for tc_idx, tc in enumerate(tool_calls):
                # First: emit the structural envelope (id/type/function.name) once.
                first_delta = {
                    "index": 0,
                    "delta": {"tool_calls": [{
                        "index": tc_idx,
                        "id": tc["id"],
                        "type": tc["type"],
                        "function": {"name": tc["function"]["name"], "arguments": ""},
                    }]},
                    "finish_reason": None,
                }
                yield f"data: {json.dumps({**base, 'choices': [first_delta]})}\n\n".encode()
                # Then: split the arguments JSON string into 4 fragments
                args = tc["function"]["arguments"]
                step = max(1, len(args) // 4)
                fragments = [args[i:i+step] for i in range(0, len(args), step)]
                for frag in fragments:
                    await asyncio.sleep(0)
                    delta = {
                        "index": 0,
                        "delta": {"tool_calls": [{
                            "index": tc_idx,
                            "function": {"arguments": frag},
                        }]},
                        "finish_reason": None,
                    }
                    yield f"data: {json.dumps({**base, 'choices': [delta]})}\n\n".encode()
            stop_chunk = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
            yield f"data: {json.dumps(stop_chunk)}\n\n".encode()
        else:
            # Content delta chunks (~6 chunks for visible streaming behavior)
            chunks: list[str] = []
            n = max(1, min(6, len(reply) // 4 or 1))
            if reply:
                step = max(1, len(reply) // n)
                chunks = [reply[i:i+step] for i in range(0, len(reply), step)]
            for piece in chunks:
                await asyncio.sleep(0)
                chunk = {**base, "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                yield f"data: {json.dumps(chunk)}\n\n".encode()
            stop_chunk = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(stop_chunk)}\n\n".encode()

        # ── stream_options.include_usage \u2192 final usage chunk before [DONE] ──
        opts = openai_request.get("stream_options") or {}
        if opts.get("include_usage"):
            usage_chunk = {
                **base,
                "choices": [],   # OpenAI: empty choices on the usage-only chunk
                "usage": full["usage"],
            }
            yield f"data: {json.dumps(usage_chunk)}\n\n".encode()

        yield b"data: [DONE]\n\n"

    async def embeddings(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        await asyncio.sleep(0)
        inputs = openai_request.get("input")
        if isinstance(inputs, str):
            inputs = [inputs]
        if not isinstance(inputs, list):
            inputs = [str(inputs or "")]

        # Honor `dimensions` parameter (Titan supports 256/512/1024; OpenAI-shape).
        # Default 1024 (Titan v2). Clamp to 64\u20134096 for sanity.
        dim_req = openai_request.get("dimensions")
        dim = 1024
        if isinstance(dim_req, int) and 64 <= dim_req <= 4096:
            dim = dim_req

        # Deterministic vector: seeded by text+idx; values normalised to [-1, 1].
        def vec_for(text: str, idx: int) -> list[float]:
            seed = (hash(text) ^ (idx * 2654435761)) & 0xFFFFFFFF
            out: list[float] = []
            for _k in range(dim):
                seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
                out.append((seed / 0x3FFFFFFF) - 1.0)
            return out

        # encoding_format: "float" (default) or "base64"
        enc = (openai_request.get("encoding_format") or "float").lower()

        def encode(vec: list[float]):
            if enc == "base64":
                packed = struct.pack(f"<{len(vec)}f", *vec)
                return base64.b64encode(packed).decode("ascii")
            return vec

        data = [
            {"object": "embedding", "index": i, "embedding": encode(vec_for(text, i))}
            for i, text in enumerate(inputs)
        ]
        prompt_tokens = sum(_approx_tokens(t) for t in inputs)
        body = {
            "object": "list",
            "data": data,
            "model": target.model_id,
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        }
        return EngineResult(body=body, status_code=200)

    async def healthcheck(self) -> bool:
        return True
