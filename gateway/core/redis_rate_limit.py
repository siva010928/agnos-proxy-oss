"""Distributed rate limiting via Redis (fixed-window counters), so multiple
gateway replicas share one RPM/TPM budget. Falls back to in-memory if no Redis.
"""
from __future__ import annotations

import time

from gateway.config import settings

_redis = None


def _client():
    global _redis
    if _redis is None and settings.redis_url:
        import redis.asyncio as redis
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


class RedisRateLimiter:
    """Fixed-window per-minute counters keyed by (workspace, alias)."""

    async def check(self, workspace_id: str, alias: str, quota: dict,
                    est_tokens: int) -> tuple[bool, str, float]:
        r = _client()
        if r is None:
            return True, "", 0.0
        window = int(time.time() // 60)
        rpm, tpm = quota.get("rpm"), quota.get("tpm")
        base = f"rl:{workspace_id}:{alias}:{window}"
        try:
            if rpm:
                n = await r.incr(f"{base}:r")
                if n == 1:
                    await r.expire(f"{base}:r", 90)
                if n > rpm:
                    return False, "rpm", 60 - (time.time() % 60)
            if tpm:
                t = await r.incrby(f"{base}:t", max(1, est_tokens))
                if t == max(1, est_tokens):
                    await r.expire(f"{base}:t", 90)
                if t > tpm:
                    return False, "tpm", 60 - (time.time() % 60)
        except Exception:
            return True, "", 0.0  # fail-open on Redis trouble
        return True, "", 0.0


    async def check_multi_scope(
        self, *, client_id, workspace_id, user_id, alias,
        client_rl, workspace_rl, model_quota, est_tokens,
    ) -> tuple[bool, str, str, float]:
        """Distributed multi-scope check (User → Workspace → Client → Model),
        first violation wins. Fixed per-minute windows shared across replicas via
        Redis INCR/INCRBY. Returns (allowed, scope, limit_type, retry_after)."""
        r = _client()
        if r is None:
            return True, "", "", 0.0
        window = int(time.time() // 60)
        retry = round(60 - (time.time() % 60), 1)
        need = max(1, est_tokens)

        async def _scope(ident: str, rpm, tpm) -> str | None:
            base = f"rl:{ident}:{window}"
            try:
                if rpm:
                    n = await r.incr(f"{base}:r")
                    if n == 1:
                        await r.expire(f"{base}:r", 90)
                    if n > rpm:
                        return "rpm"
                if tpm:
                    t = await r.incrby(f"{base}:t", need)
                    if t == need:
                        await r.expire(f"{base}:t", 90)
                    if t > tpm:
                        return "tpm"
            except Exception:
                return None  # fail-open on Redis trouble
            return None

        if user_id and workspace_rl:
            uq = workspace_rl.get("user") or {}
            lt = await _scope(f"user:{workspace_id}:{user_id}", uq.get("rpm"), uq.get("tpm"))
            if lt:
                return False, "user", lt, retry
        if workspace_rl:
            lt = await _scope(f"workspace:{workspace_id}", workspace_rl.get("rpm"), workspace_rl.get("tpm"))
            if lt:
                return False, "workspace", lt, retry
        if client_id and client_rl:
            lt = await _scope(f"client:{client_id}", client_rl.get("rpm"), client_rl.get("tpm"))
            if lt:
                return False, "client", lt, retry
        if model_quota:
            lt = await _scope(f"model:{workspace_id}:{alias}", model_quota.get("rpm"), model_quota.get("tpm"))
            if lt:
                return False, "model", lt, retry
        return True, "", "", 0.0


redis_limiter = RedisRateLimiter()


def using_redis() -> bool:
    return bool(settings.redis_url)
