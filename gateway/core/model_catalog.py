"""Model Catalog (WAVE 19 TRACK C4).

The operator-curated catalog of upstream models. Surfaces on /v1/models per
workspace (only `enabled=true` rows that the workspace has a target for),
drives the eligibility check inside the governance flow (a workspace's
chat alias must resolve to a model the catalog flags `enabled=true`), and
feeds capability flags (supports_tools / supports_images / supports_reasoning)
to the UI.

Lookup is by (provider, model_id) primary key. Two helpers:
  * ``is_eligible(provider, model_id)`` \u2014 ``(allowed, reason)``.  When the
    model is missing from the catalog entirely we currently allow it (admin
    has full freedom to use uncatalogued ids); when the row exists with
    ``enabled=false`` we reject. This avoids breaking demos that point at a
    custom model id while still giving operators a hard kill switch.
  * ``catalog_for_workspace(ws)`` \u2014 returns the eligible model rows surfaced
    on that workspace's /v1/models. Filters to providers the workspace has
    configured.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select

from gateway.db.database import async_session
from gateway.db.models import ModelCatalog


@dataclass(frozen=True)
class EligibilityResult:
    allowed: bool
    reason: str = ""
    catalog_row: dict | None = None


# Tiny TTL cache: full catalog read once per few seconds (read-mostly).
_CACHE: dict[str, tuple[list[ModelCatalog], float]] = {}
_TTL = 5.0


async def _all_rows() -> list[ModelCatalog]:
    import time as _t
    hit = _CACHE.get("all")
    if hit and hit[1] > _t.monotonic():
        return hit[0]
    async with async_session() as s:
        rows = (await s.scalars(select(ModelCatalog))).all()
    _CACHE["all"] = (list(rows), _t.monotonic() + _TTL)
    return list(rows)


def invalidate_catalog_cache() -> None:
    _CACHE.pop("all", None)


async def is_eligible(provider: str, model_id: str) -> EligibilityResult:
    """Catalog eligibility for a (provider, model_id). Missing row \u21d2 allow
    (operator hasn't curated this id yet); enabled=false \u21d2 reject."""
    rows = await _all_rows()
    for r in rows:
        if r.provider == provider and r.model_id == model_id:
            if not r.enabled:
                return EligibilityResult(
                    allowed=False,
                    reason=f"model '{provider}/{model_id}' is disabled in the model catalog",
                    catalog_row=_row_dict(r),
                )
            return EligibilityResult(allowed=True, catalog_row=_row_dict(r))
    return EligibilityResult(allowed=True)


async def catalog_for_workspace(configured_providers: set[str]) -> list[dict]:
    """Return eligible catalog rows for providers the workspace has configured."""
    rows = await _all_rows()
    return [_row_dict(r) for r in rows
            if r.enabled and r.provider in configured_providers]


def _row_dict(r: ModelCatalog) -> dict:
    return {
        "id": r.id, "provider": r.provider, "model_id": r.model_id,
        "display_name": r.display_name,
        "context_window": r.context_window,
        "supports_tools": r.supports_tools,
        "supports_images": r.supports_images,
        "supports_reasoning": r.supports_reasoning,
        "supports_streaming": r.supports_streaming,
        "input_per_1k": r.input_per_1k, "output_per_1k": r.output_per_1k,
        "enabled": r.enabled, "notes": r.notes or "",
    }


# ── Catalog seed source: the repo's own provider catalog (no hardcoded model
# data in code). Capabilities are inferred per model; prices come from the
# synced pricing table (LiteLLM dataset), never hardcoded here. ──
import os
_CATALOG_FILE = os.environ.get(
    "PROVIDER_CATALOG_FILE",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "provider_catalog.yaml"))

# lightweight capability inference (operator can override any row via /admin/model-catalog)
_EMBED_HINTS = ("embed", "embedding")
_NO_REASONING = ("titan", "cohere", "llama", "mistral", "j2-", "gpt-3.5", "command", "instant")


def _infer_row(provider: str, model_id: str, is_embed: bool) -> dict:
    from gateway.core.pricing import price_for
    mid = model_id.lower()
    inp, out = price_for(model_id)          # synced pricing (per-1k); 0 if unknown
    ctx = 1_000_000 if provider == "gemini" and not is_embed else (8_192 if is_embed else 200_000)
    reasoning = (not is_embed) and not any(h in mid for h in _NO_REASONING)
    return {
        "provider": provider, "model_id": model_id,
        "display_name": model_id,
        "context_window": ctx,
        "supports_tools": not is_embed,
        "supports_images": not is_embed,
        "supports_reasoning": reasoning,
        "supports_streaming": not is_embed,
        "input_per_1k": inp, "output_per_1k": out,
        "enabled": True,
    }


def load_catalog_rows() -> list[dict]:
    """Read data/provider_catalog.yaml → inferred ModelCatalog row dicts."""
    try:
        import yaml
        with open(_CATALOG_FILE) as f:
            cat = yaml.safe_load(f) or {}
    except Exception:
        return []
    rows: list[dict] = []
    for provider, spec in cat.items():
        if not isinstance(spec, dict):
            continue
        for m in (spec.get("chat") or []):
            rows.append(_infer_row(provider, m, is_embed=False))
        for m in (spec.get("embedding") or []):
            rows.append(_infer_row(provider, m, is_embed=True))
    return rows


async def seed_catalog_if_empty() -> int:
    """Cold-start: seed the model catalog from data/provider_catalog.yaml if the
    table is empty. Idempotent. Model ids come from the catalog file; prices from
    the synced pricing table - no hardcoded model data in code."""
    rows = load_catalog_rows()
    if not rows:
        return 0
    async with async_session() as s:
        existing = await s.scalar(select(ModelCatalog).limit(1))
        if existing is not None:
            return 0
        for m in rows:
            s.add(ModelCatalog(**m))
        await s.commit()
    invalidate_catalog_cache()
    return len(rows)
