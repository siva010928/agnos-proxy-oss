"""Load driver - measures pure gateway overhead vs direct-to-upstream across a
concurrency sweep, with p50/p90/p99/p99.9, throughput, and overhead framing.

Prereqs:
  1) mock upstream:  poetry run python bench/mock_echo.py
  2) gateway pointed at it:  BIFROST_URL=http://localhost:8077 poetry run python gateway_server.py
Run:  poetry run python bench/run_bench.py
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time

import httpx

GW = os.getenv("BENCH_GW", "http://localhost:8090/v1/chat/completions")
UP = os.getenv("BENCH_UP", "http://localhost:8077/v1/chat/completions")
KEY = os.getenv("WS_KEY_BENCH", "gw-key-bench-001")
TOTAL = int(os.getenv("BENCH_TOTAL", "400"))
CONCURRENCIES = [int(x) for x in os.getenv("BENCH_CONC", "1,10,50,100,200").split(",")]

PAYLOAD = {"model": "claude-sonnet-4-5",
           "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5}
UP_PAYLOAD = {**PAYLOAD, "model": "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"}


def pct(xs, p):
    s = sorted(xs)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


async def _run(client, url, headers, payload, total, conc):
    sem = asyncio.Semaphore(conc)
    lat = []
    errors = 0

    async def one():
        nonlocal errors
        async with sem:
            t = time.perf_counter()
            try:
                r = await client.post(url, headers=headers, json=payload)
                if r.status_code == 200:
                    lat.append((time.perf_counter() - t) * 1000)
                else:
                    errors += 1
            except Exception:
                errors += 1

    t0 = time.perf_counter()
    await asyncio.gather(*[one() for _ in range(total)])
    wall = time.perf_counter() - t0
    ok = len(lat) or 1
    return lat or [0.0], ok / wall, errors


async def main():
    async with httpx.AsyncClient(timeout=30,
                                 limits=httpx.Limits(max_connections=400,
                                                     max_keepalive_connections=400)) as c:
        # warmup
        await c.post(UP, json=UP_PAYLOAD)
        await c.post(GW, headers={"Authorization": f"Bearer {KEY}"}, json=PAYLOAD)

        print(f"\nAgnos Proxy - overhead benchmark (mock upstream, {TOTAL} reqs/level)")
        print(f"{'conc':>5} | {'path':<8} | {'p50':>7} {'p90':>7} {'p99':>7} {'p99.9':>7} | {'req/s':>8}")
        print("-" * 72)
        rows = []
        for conc in CONCURRENCIES:
            up_lat, up_rps, up_err = await _run(c, UP, {}, UP_PAYLOAD, TOTAL, conc)
            gw_lat, gw_rps, gw_err = await _run(c, GW, {"Authorization": f"Bearer {KEY}"}, PAYLOAD, TOTAL, conc)
            for name, lat, rps, err in (("upstream", up_lat, up_rps, up_err),
                                        ("gateway", gw_lat, gw_rps, gw_err)):
                print(f"{conc:>5} | {name:<8} | {pct(lat,50):7.2f} {pct(lat,90):7.2f} "
                      f"{pct(lat,99):7.2f} {pct(lat,99.9):7.2f} | {rps:8.0f}  err={err}")
            ov = pct(gw_lat, 50) - pct(up_lat, 50)
            rows.append((conc, ov, gw_rps))
            print(f"{'':>5} | overhead p50 = {ov:+.2f} ms\n")
        print("Gateway overhead by concurrency (p50):")
        for conc, ov, rps in rows:
            print(f"  c={conc:<4} overhead={ov:+.2f}ms  throughput={rps:.0f} req/s")


if __name__ == "__main__":
    asyncio.run(main())
