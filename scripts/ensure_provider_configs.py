"""One-off: ensure every seeded NovaTech workspace has its provider configs
(bedrock/anthropic/gemini) attached, reading credentials from .env.

The cold-start seed() only fires on a completely empty DB, so a DB that was
first provisioned before AWS creds were present ends up missing the bedrock
WorkspaceProviderConfig rows - which makes every admin PATCH fail validation
("provider 'bedrock' is not configured for this workspace").

This calls the idempotent _seed_tenant() upsert (it never deletes), so it only
fills in the missing provider rows. Safe to run repeatedly.

    .venv/bin/python scripts/ensure_provider_configs.py
"""
from __future__ import annotations

import asyncio

# Load .env BEFORE importing gateway.config (settings read os.getenv at import).
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


async def main() -> None:
    from gateway.db.database import async_session
    from gateway.db.seed import _seed_tenant

    async with async_session() as s:
        await _seed_tenant(s)
        await s.commit()
    print("✓ provider configs ensured for all seeded NovaTech workspaces")


if __name__ == "__main__":
    asyncio.run(main())
