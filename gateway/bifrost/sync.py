"""RETIRED (Feb-2026 stateless migration) - Bifrost managed-key sync is disabled.

Previously this registered a managed provider key per (workspace, provider) INTO
Bifrost's own store and selected it per request via x-bf-api-key, making Bifrost
STATEFUL. We no longer do that: the Bifrost engine is now STATELESS and passes the
provider credential PER REQUEST via Bifrost's Direct API Key
(x-bf-direct-key: true + the raw key), bypassing the key pool entirely
(see gateway/engines/bifrost_engine.py). Nothing is stored in the engine.

These functions are kept as safe no-ops so existing callers (admin CRUD, startup)
do not break; they intentionally do nothing.
"""
from __future__ import annotations


def key_name(workspace_id: str, provider: str) -> str:
    """Legacy managed-key name (no longer used for routing; kept for compatibility)."""
    return f"ws-{workspace_id}--{provider}"


async def sync_one(config_id: int) -> tuple[str | None, str | None]:
    # No managed key is registered anymore; the provider key is injected per request.
    return (None, None)


async def delete_key(internal_provider: str, name: str | None) -> None:
    return None


async def reconcile_all() -> int:
    return 0
