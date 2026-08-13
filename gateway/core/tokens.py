"""Token counting (estimate). tiktoken is OpenAI-accurate only; for other
providers this is an approximation, surfaced as such in headers/utils."""
from __future__ import annotations

from typing import Any

import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return str(content)


def count_message_tokens(messages: list[dict]) -> int:
    total = 0
    for m in messages:
        total += len(_ENC.encode(_content_text(m.get("content")))) + 4
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            total += len(_ENC.encode(fn.get("name", ""))) + len(_ENC.encode(str(fn.get("arguments", ""))))
    return total


def estimate_tokens(text: str) -> int:
    """Approximate token count for a single string (cl100k_base). Used to
    backfill output tokens when a streamed response omits a usage chunk."""
    if not text:
        return 0
    return len(_ENC.encode(text))
