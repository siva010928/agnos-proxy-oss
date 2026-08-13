"""Opt-in context truncation. Off by default. Enabled per-request via
X-Gateway-Auto-Truncate: true or per-workspace guardrails/settings.

tiktoken is an estimate (OpenAI-accurate only); labeled as such in headers.
"""
from __future__ import annotations

from gateway.core.tokens import _content_text, count_message_tokens

SAFETY_MARGIN = 1024


def apply_truncation(messages: list[dict], context_window: int) -> tuple[list[dict], dict]:
    """Drop oldest non-system messages until within budget. Returns (messages, info)."""
    original = count_message_tokens(messages)
    budget = context_window - SAFETY_MARGIN
    if original <= budget:
        return messages, {"truncated": False, "original_tokens": original, "sent_tokens": original}

    system = [m for m in messages if m.get("role") == "system"]
    other = [m for m in messages if m.get("role") != "system"]
    remaining = budget - count_message_tokens(system)
    kept: list[dict] = []
    for m in reversed(other):
        c = count_message_tokens([m])
        if remaining - c < 0:
            break
        kept.insert(0, m)
        remaining -= c
    final = system + kept
    sent = count_message_tokens(final)
    return final, {"truncated": True, "original_tokens": original, "sent_tokens": sent,
                   "dropped_messages": len(messages) - len(final)}
