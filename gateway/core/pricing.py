"""Live cost pricing - auto-synced from LiteLLM's open pricing dataset, with a
built-in fallback table and an admin override (CustomPricing).

LiteLLM JSON shape: { "<model>": {"input_cost_per_token": x, "output_cost_per_token": y, ...}, ... }
We normalize to USD-per-1K for our compute path.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

LITELLM_URL = ("https://raw.githubusercontent.com/BerriAI/litellm/"
               "main/model_prices_and_context_window.json")
_CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "model_prices.json"

# normalized: model_substring(lower) -> (input_per_1k, output_per_1k)
_PRICING: dict[str, tuple[float, float]] = {}
_OVERRIDES: dict[str, tuple[float, float]] = {}

# minimal built-in fallback (used until/if the sync completes)
_BUILTIN = {
    "claude-sonnet-4-5": (0.003, 0.015), "claude-sonnet-4-5": (0.003, 0.015),
    "claude-3-5-haiku": (0.0008, 0.004), "titan-embed": (0.00002, 0.0),
    "text-embedding-3-small": (0.00002, 0.0), "gemini-2.0-flash": (0.0001, 0.0004),
    "gpt-4o-mini": (0.00015, 0.0006), "gpt-4o": (0.0025, 0.01),
}


def _ingest(raw: dict) -> None:
    for model, info in raw.items():
        if not isinstance(info, dict):
            continue
        inp = info.get("input_cost_per_token")
        out = info.get("output_cost_per_token")
        if inp is None and out is None:
            continue
        _PRICING[model.lower()] = (float(inp or 0) * 1000.0, float(out or 0) * 1000.0)


async def sync_pricing() -> int:
    """Download + cache LiteLLM pricing. Falls back to cached file, then built-in."""
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(LITELLM_URL)
            r.raise_for_status()
            raw = r.json()
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(raw))
        _ingest(raw)
        return len(_PRICING)
    except Exception:
        if _CACHE_FILE.exists():
            try:
                _ingest(json.loads(_CACHE_FILE.read_text()))
                return len(_PRICING)
            except Exception:
                pass
    return 0


def set_override(model_substr: str, input_per_1k: float, output_per_1k: float) -> None:
    _OVERRIDES[model_substr.lower()] = (input_per_1k, output_per_1k)


def clear_override(model_substr: str) -> None:
    _OVERRIDES.pop(model_substr.lower(), None)


async def load_overrides() -> int:
    """Load operator pricing overrides from the CustomPricing DB table into memory."""
    from sqlalchemy import select
    from gateway.db.database import async_session
    from gateway.db.models import CustomPricing
    try:
        async with async_session() as s:
            rows = (await s.scalars(select(CustomPricing))).all()
        _OVERRIDES.clear()
        for r in rows:
            _OVERRIDES[r.model_substr.lower()] = (r.input_per_1k, r.output_per_1k)
        return len(_OVERRIDES)
    except Exception:  # noqa: BLE001
        return 0


def _resolve(provider_model_id: str) -> tuple[float, float, str]:
    """Resolve (input_per_1k, output_per_1k, source) DETERMINISTICALLY.

    Order: exact key (override > synced) first, then the LONGEST substring match
    (most specific key wins). The old code returned the FIRST substring hit in
    dict order, which was arbitrary - e.g. a model id could pick up an unrelated
    key's price. Longest-match makes a region-prefixed provider id like
    'us.anthropic.claude-opus-4-1-20250805-v1:0' resolve to the most specific
    LiteLLM key it contains, not whatever happened to be iterated first.
    """
    mid = (provider_model_id or "").lower()
    if mid in _OVERRIDES:
        return (*_OVERRIDES[mid], "override")
    if mid in _PRICING:
        return (*_PRICING[mid], "synced")
    for table, src in ((_OVERRIDES, "override"), (_PRICING, "synced"), (_BUILTIN, "builtin")):
        best_key: str | None = None
        for key in table:
            if key and key in mid and (best_key is None or len(key) > len(best_key)):
                best_key = key
        if best_key is not None:
            return (*table[best_key], src)
    return (0.0, 0.0, "none")


def price_source(provider_model_id: str) -> str:
    """Where the effective price comes from: override | synced | builtin | none."""
    return _resolve(provider_model_id)[2]


def price_for(provider_model_id: str) -> tuple[float, float]:
    inp, out, _ = _resolve(provider_model_id)
    return (inp, out)


def compute_cost(provider_model_id: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = price_for(provider_model_id)
    return round((input_tokens / 1000.0) * inp + (output_tokens / 1000.0) * out, 8)
