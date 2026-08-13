"""BackendEngine - pure OpenAI in/out. Credentials are engine-encapsulated.

Implementations must NOT leak engine-specifics (no x-bf-* headers, no
bifrost_config, no extra_fields semantics) across this boundary.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from gateway.core.registry import ResolvedTarget


class EngineResult:
    """Normalized non-stream result: the raw OpenAI-shaped JSON body + parsed usage.

    Enforces the anti-corruption boundary: any engine-specific keys (Bifrost
    ``extra_fields``, ``bifrost_config``, ``x-bf-*``) are stripped from the body
    so only clean OpenAI-shaped JSON crosses to the caller. Engines may inspect
    their own internals before constructing this; what lands here is the contract.
    """

    # keys that must never cross the BackendEngine boundary to a component
    _LEAK_KEYS = ("extra_fields", "bifrost_config")

    def __init__(self, body: dict[str, Any], status_code: int = 200):
        self.body = self._sanitize(body)
        self.status_code = status_code

    @classmethod
    def _sanitize(cls, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict):
            # A provider may return a non-object body (e.g. Google returns a JSON
            # array on error). Coerce to an OpenAI-shaped error dict so nothing
            # downstream ever calls .get() on a list/str.
            return {"error": {"message": f"provider returned a non-object body: {str(body)[:300]}",
                              "type": "provider_error"}}
        for k in list(body.keys()):
            if k in cls._LEAK_KEYS or k.lower().startswith("x-bf-"):
                body.pop(k, None)
        return body

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and "error" not in self.body

    @property
    def usage(self) -> dict[str, int]:
        u = self.body.get("usage") or {}
        return {
            "input_tokens": u.get("prompt_tokens", 0) or 0,
            "output_tokens": u.get("completion_tokens", 0) or 0,
        }


class BackendEngine(ABC):
    name: str = "base"

    @abstractmethod
    async def chat(self, openai_request: dict, target: ResolvedTarget) -> EngineResult: ...

    @abstractmethod
    def chat_stream(self, openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
        r"""Yield raw OpenAI-format SSE byte lines (data: {...}\n\n ... data: [DONE])."""
        ...

    @abstractmethod
    async def embeddings(self, openai_request: dict, target: ResolvedTarget) -> EngineResult: ...

    @abstractmethod
    async def healthcheck(self) -> bool: ...
