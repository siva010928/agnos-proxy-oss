"""Bare-proxy overhead benchmark for the Agnos Proxy.

Measures the gateway's *own* added latency - identity resolution + routing
decision - excluding the upstream provider call and excluding the optional
governance work (guardrails / budget / rate-limit / event emit). This is the
honest analogue of a bare gateway's "added latency" (cf. Bifrost's ~11µs in Go).

Methodology (mirrors a controlled gateway benchmark):
  1. Swap the BackendEngine to ``echo`` (a $0 in-process upstream) so there is
     NO network or provider latency polluting the measurement.
  2. Snapshot the server-side ``gateway_overhead_seconds`` histogram (stages
     ``proxy`` = bare plumbing, and ``total`` = end-to-end minus provider).
  3. Fire N requests through /v1/chat/completions with a real workspace key.
  4. Snapshot again and report the mean + p50/p99 of the *proxy* stage (and the
     total overhead, for context) computed from the histogram delta.
  5. Restore the engine to ``bifrost``.

The numbers come straight from the gateway's Prometheus histogram, so they are
exactly what the dashboard shows - no client-side timing games.

Usage:
  python scripts/bench_overhead.py \
      --base-url http://localhost:8090 \
      --key gw-eshop-sale-xxxxxxxx \
      --workspace ws-novatech-payments \
      --model default \
      --n 500 --concurrency 25
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

import httpx


def _parse_buckets(metrics_text: str, stage: str) -> list[tuple[float, float]]:
    """Return sorted [(le, cumulative_count)] for gateway_overhead_seconds{stage=}."""
    out: list[tuple[float, float]] = []
    needle = f'gateway_overhead_seconds_bucket{{le="'
    for line in metrics_text.splitlines():
        if not line.startswith(needle):
            continue
        if f'stage="{stage}"' not in line:
            continue
        try:
            le_str = line.split('le="', 1)[1].split('"', 1)[0]
            val = float(line.rsplit(" ", 1)[1])
        except (IndexError, ValueError):
            continue
        le = float("inf") if le_str == "+Inf" else float(le_str)
        out.append((le, val))
    out.sort(key=lambda x: x[0])
    return out


def _sum_count(metrics_text: str, stage: str) -> tuple[float, float]:
    s = c = 0.0
    for line in metrics_text.splitlines():
        if f'stage="{stage}"' not in line:
            continue
        if line.startswith("gateway_overhead_seconds_sum"):
            s = float(line.rsplit(" ", 1)[1])
        elif line.startswith("gateway_overhead_seconds_count"):
            c = float(line.rsplit(" ", 1)[1])
    return s, c


def _quantile(buckets: list[tuple[float, float]], q: float) -> float:
    """Prometheus-style histogram quantile with linear interpolation."""
    if not buckets:
        return 0.0
    total = buckets[-1][1]
    if not total:
        return 0.0
    rank = q * total
    prev_le = prev_cum = 0.0
    for le, cum in buckets:
        if cum >= rank:
            if le == float("inf"):
                return prev_le
            in_bucket = cum - prev_cum
            if in_bucket <= 0:
                return le
            frac = (rank - prev_cum) / in_bucket
            return prev_le + frac * (le - prev_le)
        prev_le = le if le != float("inf") else prev_le
        prev_cum = cum
    return prev_le


async def _metrics(client: httpx.AsyncClient, base: str) -> str:
    r = await client.get(f"{base}/metrics")
    r.raise_for_status()
    return r.text


async def _set_engine(client: httpx.AsyncClient, base: str, engine: str) -> None:
    r = await client.post(f"{base}/admin/engine", json={"engine": engine})
    r.raise_for_status()


async def _login(client: httpx.AsyncClient, base: str, user: str, pw: str) -> None:
    r = await client.post(f"{base}/auth/login", json={"username": user, "password": pw})
    r.raise_for_status()


async def _fire(client: httpx.AsyncClient, base: str, key: str, model: str,
                use_case: str) -> int:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
        "metadata": {"use_case": use_case},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "X-Gateway-Component": "benchmark",
        "X-Gateway-User": "bench",
    }
    try:
        r = await client.post(f"{base}/v1/chat/completions", json=body, headers=headers)
        return r.status_code
    except Exception:  # noqa: BLE001
        return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8090")
    ap.add_argument("--key", required=True, help="workspace API key (gw-...)")
    ap.add_argument("--model", default="default", help='model alias or "default"')
    ap.add_argument("--use-case", default="benchmark.overhead")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--concurrency", type=int, default=25)
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", default="agnos")
    ap.add_argument("--keep-engine", action="store_true",
                    help="don't swap the engine (measure whatever is active)")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    async with httpx.AsyncClient(timeout=30.0) as client:
        await _login(client, base, args.admin_user, args.admin_pass)

        if not args.keep_engine:
            print("• Swapping engine → echo (no provider/network latency)…")
            await _set_engine(client, base, "echo")

        try:
            # warm-up (caches: auth key, routing, component) - excluded from stats
            print("• Warming up (20 requests)…")
            await asyncio.gather(*[
                _fire(client, base, args.key, args.model, args.use_case) for _ in range(20)
            ])

            before = await _metrics(client, base)
            s0_proxy, c0_proxy = _sum_count(before, "proxy")
            s0_total, c0_total = _sum_count(before, "total")
            b0_proxy = _parse_buckets(before, "proxy")

            print(f"• Firing {args.n} requests @ concurrency {args.concurrency}…")
            sem = asyncio.Semaphore(args.concurrency)
            statuses: list[int] = []

            async def _bounded() -> None:
                async with sem:
                    statuses.append(await _fire(client, base, args.key, args.model, args.use_case))

            t0 = time.perf_counter()
            await asyncio.gather(*[_bounded() for _ in range(args.n)])
            wall = time.perf_counter() - t0

            await asyncio.sleep(0.5)  # let the last observations flush
            after = await _metrics(client, base)
            s1_proxy, c1_proxy = _sum_count(after, "proxy")
            s1_total, c1_total = _sum_count(after, "total")
            b1_proxy = _parse_buckets(after, "proxy")

            ok = sum(1 for s in statuses if s == 200)
            d_proxy_c = c1_proxy - c0_proxy
            mean_proxy = ((s1_proxy - s0_proxy) / d_proxy_c * 1e6) if d_proxy_c else 0.0  # µs
            d_total_c = c1_total - c0_total
            mean_total = ((s1_total - s0_total) / d_total_c * 1e3) if d_total_c else 0.0  # ms

            # delta histogram for proxy percentiles
            cum0 = {le: v for le, v in b0_proxy}
            delta = [(le, v - cum0.get(le, 0.0)) for le, v in b1_proxy]
            p50 = _quantile(delta, 0.50) * 1e6  # µs
            p99 = _quantile(delta, 0.99) * 1e6  # µs

            print("\n" + "=" * 56)
            print("  BARE PROXY OVERHEAD  (identity + routing, excl. provider)")
            print("=" * 56)
            print(f"  requests sent        : {args.n}  (200 OK: {ok})")
            print(f"  wall time            : {wall:.2f}s  ({args.n / wall:,.0f} req/s)")
            print(f"  mean proxy overhead  : {mean_proxy:,.1f} µs")
            print(f"  p50  proxy overhead  : {p50:,.1f} µs")
            print(f"  p99  proxy overhead  : {p99:,.1f} µs")
            print(f"  mean TOTAL overhead  : {mean_total:,.2f} ms  (incl. governance, excl. provider)")
            print("=" * 56)
            print("  Bare proxy = the gateway's own plumbing. TOTAL adds the")
            print("  governance value-add (guardrails/PII, budget, attribution)")
            print("  that a bare proxy doesn't do. Provider latency excluded.")
            print("=" * 56)
        finally:
            if not args.keep_engine:
                print("• Restoring engine → bifrost…")
                await _set_engine(client, base, "bifrost")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
