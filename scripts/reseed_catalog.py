"""Force-reseed the model_catalog table from data/provider_catalog.yaml.

seed_catalog_if_empty() only seeds a cold (empty) table. After regenerating the
catalog (scripts/gen_provider_catalog.py) run this once to replace the rows:

    python scripts/reseed_catalog.py

Operator pricing overrides live in the CustomPricing table (separate) and are
NOT touched.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import delete

from gateway.core.model_catalog import invalidate_catalog_cache, load_catalog_rows
from gateway.db.database import async_session
from gateway.db.models import ModelCatalog


async def main() -> None:
    rows = load_catalog_rows()
    async with async_session() as s:
        await s.execute(delete(ModelCatalog))
        for m in rows:
            s.add(ModelCatalog(**m))
        await s.commit()
    invalidate_catalog_cache()
    print(f"reseeded model_catalog: {len(rows)} rows")


if __name__ == "__main__":
    asyncio.run(main())
