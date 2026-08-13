"""Exact-match response cache + Idempotency-Key dedupe.

Redis-backed when REDIS_URL is set, else a bounded in-memory TTL map (fail-safe).
- Response cache: opt-in per request via `X-Gateway-Cache-TTL: <seconds>` (or
  workspace flag); keyed on (workspace, alias, messages, max_tokens, temperature).
  A hit emits a cache_hit governance event + gateway_cache_hits_total metric and
  returns instantly at $0 cost.
- Idempotency: `Idempotency-Key` header dedupes retried writes - the first
  response is replayed for repeats within the TTL.
"""
from __future__ import annotations

import hashlib
import json
import time

from gateway.config import settings

# in-memory fallback: key -> (value_json, expiry)
_MEM: dict[str, tuple[str, float]] = {}
_MEM_MAX = 5000

_redis = None


def _client():
    global _redis
    if _redis is None and settings.redis_url:
        import redis.asyncio as redis
        _redis = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def cache_key(workspace_id: str, alias: str, body: dict) -> str:
    payload = json.dumps({
        "ws": workspace_id, "alias": alias, "messages": body.get("messages"),
        "max_tokens": body.get("max_tokens"), "temperature": body.get("temperature"),
        "tools": body.get("tools"),
    }, sort_keys=True, default=str)
    return "cache:" + hashlib.sha256(payload.encode()).hexdigest()


async def get(key: str) -> dict | None:
    r = _client()
    if r is not None:
        try:
            v = await r.get(key)
            return json.loads(v) if v else None
        except Exception:
            return None
    hit = _MEM.get(key)
    if hit and hit[1] > time.time():
        return json.loads(hit[0])
    return None


async def put(key: str, value: dict, ttl: int) -> None:
    data = json.dumps(value, default=str)
    r = _client()
    if r is not None:
        try:
            await r.set(key, data, ex=ttl)
            return
        except Exception:
            pass
    if len(_MEM) > _MEM_MAX:
        _MEM.clear()
    _MEM[key] = (data, time.time() + ttl)
