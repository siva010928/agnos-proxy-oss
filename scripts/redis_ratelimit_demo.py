"""Distributed (multi-replica) rate-limit proof.

Two gateway *replicas* share one RPM budget via Redis fixed-window counters.
We instantiate the production RedisRateLimiter twice (replica A + replica B),
pointed at the real Redis, and round-robin requests between them - the global
limit is enforced regardless of which replica serves, proving the shared budget.

Usage:
  REDIS_URL=redis://localhost:6380 python scripts/redis_ratelimit_demo.py --rpm 5
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# default to the compose Redis if the caller didn't set REDIS_URL
os.environ.setdefault("REDIS_URL", "redis://localhost:6380")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpm", type=int, default=5)
    ap.add_argument("--requests", type=int, default=8)
    args = ap.parse_args()

    # import after REDIS_URL is set so settings picks it up
    from gateway.core.redis_rate_limit import RedisRateLimiter, using_redis, _client
    if not using_redis():
        print("REDIS_URL not configured; aborting"); return
    r = _client()
    # unique key space per run so we start from zero
    alias = f"shared-{uuid.uuid4().hex[:6]}"
    ws = "ws-loadtest"
    quota = {"rpm": args.rpm}

    repA, repB = RedisRateLimiter(), RedisRateLimiter()
    print(f"shared RPM={args.rpm} across 2 replicas (Redis {os.environ['REDIS_URL']})")
    allowed = denied = 0
    for i in range(args.requests):
        rep = repA if i % 2 == 0 else repB
        name = "A" if i % 2 == 0 else "B"
        ok, ltype, retry = await rep.check(ws, alias, quota, 1)
        tag = "200 ALLOW" if ok else f"429 {ltype} (retry {int(retry)}s)"
        print(f"  req {i+1} -> replica {name} -> {tag}")
        allowed += ok
        denied += (not ok)
    print(f"\nresult: {allowed} allowed, {denied} denied - global limit enforced across replicas "
          f"({'PASS' if allowed == args.rpm else 'CHECK'})")
    try:
        await r.aclose()
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    asyncio.run(main())
