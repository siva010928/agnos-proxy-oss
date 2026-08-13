"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gateway.config import settings
from gateway.db.models import Base

_engine = create_async_engine(settings.db_url, echo=False, pool_pre_ping=True,
                              pool_size=50, max_overflow=50, pool_timeout=30)
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    _engine, expire_on_commit=False
)

# Lightweight additive migrations (no Alembic): add columns to existing tables.
# Each must be idempotent. Keep guarded so a missing-DB never crashes import.
_MIGRATIONS = [
    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS display_name VARCHAR(128)",
    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS client_id VARCHAR(64)",
    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS rate_limits JSON DEFAULT '{}'::jsonb",
    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS engine_overrides JSON DEFAULT '{}'::jsonb",
    "CREATE INDEX IF NOT EXISTS ix_workspaces_client_id ON workspaces(client_id)",
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS key_id INTEGER",
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS event_kind VARCHAR(24) DEFAULT 'completion'",
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS source VARCHAR(16) DEFAULT 'live'",
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS component VARCHAR(64)",
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS client_id VARCHAR(64)",
    "ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS error_detail JSON",  # WAVE 26 - structured why-context
    "CREATE INDEX IF NOT EXISTS ix_request_logs_client_id ON request_logs(client_id)",
    "ALTER TABLE guardrail_violations ADD COLUMN IF NOT EXISTS severity VARCHAR(16) DEFAULT 'medium'",
    "ALTER TABLE guardrail_violations ADD COLUMN IF NOT EXISTS source VARCHAR(16) DEFAULT 'live'",
    "ALTER TABLE guardrail_rules ADD COLUMN IF NOT EXISTS builder_spec JSON",
]


async def init_db() -> None:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _MIGRATIONS:
            try:
                await conn.execute(text(stmt))
            except Exception:  # noqa: BLE001 - non-Postgres or already applied
                pass
