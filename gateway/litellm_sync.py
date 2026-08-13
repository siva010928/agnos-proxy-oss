"""RETIRED (Feb-2026 stateless migration) - LiteLLM key/model sync is disabled.

Previously this synced our decrypted provider keys INTO the LiteLLM proxy's own
Postgres (via /model/new, store_model_in_db), making that engine STATEFUL. We no
longer do that: the LiteLLM engine is now STATELESS and receives the provider
credential PER REQUEST via clientside credentials (see gateway/engines/litellm_engine.py
and infra/litellm-engine/config.yaml). Nothing is stored in the engine.

These functions are kept as safe no-ops so existing callers (admin CRUD, startup)
do not break; they intentionally do nothing.
"""
from __future__ import annotations

_PREFIX = "ws-"


def model_ref(workspace_id: str, provider: str, model_id: str) -> str:
    """Legacy scoped-model name (no longer used for routing; kept for compatibility)."""
    return f"{_PREFIX}{workspace_id}--{provider}--{model_id}"


async def healthy() -> bool:
    # Report "not healthy" so the startup reconcile short-circuits and does nothing.
    return False


async def reconcile_all() -> int:
    return 0


async def sync_provider_row(config_id: int) -> int:
    return 0


async def delete_provider_models(workspace_id: str, provider: str) -> int:
    return 0
