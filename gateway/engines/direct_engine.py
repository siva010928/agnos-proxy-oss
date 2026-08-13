"""DirectEngine - owned, in-process backend (no Bifrost in the request path).

Dispatches per provider to our own adapters, all speaking the pure OpenAI wire:
  anthropic -> anthropic_direct  (chat + real streaming; prompt caching + extended thinking)
  bedrock   -> direct_bedrock    (Converse + ConverseStream; Titan/Cohere embeddings; 3 auth modes)
  gemini    -> direct_gemini     (generateContent + streaming; embeddings)

Same OpenAI contract, same governance, same dashboard - different engine. This is
the proof the BackendEngine is swappable per provider with ZERO component change:
rent Bifrost's translation for most traffic, insource a provider to DirectEngine
when volume/criticality/trust justifies it.

`_openai_body` keeps the intentional ``extra_fields`` leak so the anti-corruption
boundary strip stays tested (test_backend::test_direct_engine_body_clean_at_boundary),
and it is the single shared OpenAI-body builder used by the bedrock/gemini adapters.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator

from gateway.core.registry import ResolvedTarget
from gateway.engines.base import BackendEngine, EngineResult


def _openai_body(model_id: str, text: str, usage: dict, finish: str) -> dict:
    return {
        "id": f"chatcmpl-direct-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion", "created": int(time.time()), "model": model_id,
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": usage.get("inputTokens", 0),
                  "completion_tokens": usage.get("outputTokens", 0),
                  "total_tokens": usage.get("totalTokens", 0)
                  or (usage.get("inputTokens", 0) + usage.get("outputTokens", 0))},
        "extra_fields": {"engine": "direct"},
    }


# OpenAI-compatible providers served by the generic direct_openai_compat adapter.
_OPENAI_COMPAT = ("openai", "litellm_proxy", "ollama", "hosted_vllm", "lm-studio")


def _unsupported(provider: str, what: str) -> EngineResult:
    return EngineResult({"error": {
        "message": (f"DirectEngine has not insourced {what} for provider '{provider}' yet - "
                    f"route it through the bifrost engine, or add a direct adapter."),
        "type": "invalid_request_error"}}, 400)


class DirectEngine(BackendEngine):
    name = "direct"

    async def chat(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        p = target.provider
        if p == "anthropic":
            from gateway.engines.anthropic_direct import anthropic_chat
            return await anthropic_chat(openai_request, target)
        if p == "bedrock":
            from gateway.engines.direct_bedrock import bedrock_chat
            return await bedrock_chat(openai_request, target)
        if p in ("gemini", "google_genai"):
            from gateway.engines.direct_gemini import gemini_chat
            return await gemini_chat(openai_request, target)
        if p == "vertex_ai":
            from gateway.engines.direct_vertex import vertex_chat
            return await vertex_chat(openai_request, target)
        if p in _OPENAI_COMPAT:
            from gateway.engines.direct_openai_compat import openai_compat_chat
            return await openai_compat_chat(openai_request, target)
        return _unsupported(p, "chat")

    async def chat_stream(self, openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
        p = target.provider
        if p == "anthropic":
            from gateway.engines.anthropic_direct import anthropic_chat_stream
            async for c in anthropic_chat_stream(openai_request, target):
                yield c
            return
        if p == "bedrock":
            from gateway.engines.direct_bedrock import bedrock_chat_stream
            async for c in bedrock_chat_stream(openai_request, target):
                yield c
            return
        if p in ("gemini", "google_genai"):
            from gateway.engines.direct_gemini import gemini_chat_stream
            async for c in gemini_chat_stream(openai_request, target):
                yield c
            return
        if p == "vertex_ai":
            from gateway.engines.direct_vertex import vertex_chat_stream
            async for c in vertex_chat_stream(openai_request, target):
                yield c
            return
        if p in _OPENAI_COMPAT:
            from gateway.engines.direct_openai_compat import openai_compat_chat_stream
            async for c in openai_compat_chat_stream(openai_request, target):
                yield c
            return
        # Unknown provider: preserve the stream contract with a single chunk built
        # from the non-stream path (which returns the _unsupported error body).
        result = await self.chat(openai_request, target)
        msg = result.body.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        chunk = {"id": result.body.get("id"), "object": "chat.completion.chunk",
                 "created": int(time.time()), "model": target.model_id,
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": msg},
                              "finish_reason": "stop"}],
                 "usage": result.body.get("usage")}
        yield f"data: {json.dumps(chunk)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    async def embeddings(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        p = target.provider
        if p == "bedrock":
            from gateway.engines.direct_bedrock import bedrock_embeddings
            return await bedrock_embeddings(openai_request, target)
        if p in ("gemini", "google_genai"):
            from gateway.engines.direct_gemini import gemini_embeddings
            return await gemini_embeddings(openai_request, target)
        if p == "vertex_ai":
            from gateway.engines.direct_vertex import vertex_embeddings
            return await vertex_embeddings(openai_request, target)
        if p in _OPENAI_COMPAT:
            from gateway.engines.direct_openai_compat import openai_compat_embeddings
            return await openai_compat_embeddings(openai_request, target)
        return _unsupported(p, "embeddings")

    async def healthcheck(self) -> bool:
        return True
