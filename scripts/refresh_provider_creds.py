"""Refresh every stored WorkspaceProviderConfig credential from the CURRENT .env.

The local Postgres can hold stale provider keys from an earlier seed (the seed is
idempotent and never overwrites existing creds). This re-encrypts the live values
from settings into every row so all engines use fresh keys. Run:

    PYTHONPATH=. .venv/bin/python scripts/refresh_provider_creds.py
"""
import asyncio

from sqlalchemy import select

from gateway.config import settings
from gateway.db.database import async_session
from gateway.db.models import WorkspaceProviderConfig
from gateway.secrets.store import cipher


def creds_for(provider: str) -> dict:
    if provider == "bedrock":
        return {"access_key": settings.aws_access_key_id or "",
                "secret_key": settings.aws_secret_access_key or "",
                "region": settings.aws_region_name}
    if provider in ("anthropic",):
        return {"api_key": settings.anthropic_api_key or ""}
    if provider in ("gemini", "google_genai"):
        return {"api_key": settings.gemini_api_key or ""}
    if provider == "openai":
        return {"api_key": settings.openai_api_key or ""}
    if provider == "azure":
        return {"api_key": settings.azure_openai_api_key or "",
                "endpoint": settings.azure_openai_endpoint or ""}
    return {}


async def main():
    c = cipher()
    updated, skipped = 0, 0
    async with async_session() as s:
        rows = (await s.scalars(select(WorkspaceProviderConfig))).all()
        for row in rows:
            fresh = creds_for(row.provider)
            if not fresh or not any(fresh.values()):
                skipped += 1
                continue
            # merge onto existing config so region/endpoint stay consistent
            cfg = dict(row.config or {})
            if row.provider == "bedrock":
                cfg["region"] = fresh["region"]
            if row.provider == "azure" and fresh.get("endpoint"):
                cfg.setdefault("endpoint", fresh["endpoint"])
                cfg.setdefault("api_version", settings.azure_openai_api_version)
            row.encrypted_credentials = c.encrypt(fresh)
            row.config = cfg
            updated += 1
        await s.commit()
    print(f"refreshed {updated} provider-config row(s); skipped {skipped} (no key in .env)")


asyncio.run(main())
