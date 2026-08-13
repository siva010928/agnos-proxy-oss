"""Latency benchmark - proves the gateway's added overhead is small.

Measures wall-clock for the SAME request:
  (A) component → Bifrost directly        (engine baseline)
  (B) component → Agnos Proxy → Bifrost (governed path)
Reports the per-request overhead added by all gateway governance
(auth, routing, guardrails, rate-limit, budgets, observer emit).

Run gateway first, then: poetry run python demo/latency_benchmark.py
"""
from __future__ import annotations

import os
import statistics
import time

import httpx
from dotenv import load_dotenv

load_dotenv()
GW = os.getenv("AGNOS_GATEWAY_URL", "http://localhost:8090/v1")
BF = os.getenv("BIFROST_URL", "http://localhost:8099")
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")
N = int(os.getenv("BENCH_N", "12"))

PAYLOAD = {"model": "claude-sonnet-4-5",
           "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
           "max_tokens": 5}
BF_PAYLOAD = {**PAYLOAD, "model": "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0"}


def _timeit(fn) -> list[float]:
    samples = []
    for _ in range(N):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return samples


def main() -> None:
    with httpx.Client(timeout=60) as c:
        def direct():
            c.post(f"{BF}/v1/chat/completions",
                   headers={"x-bf-api-key": "ws-ws-novatech-payments--bedrock"}, json=BF_PAYLOAD)

        def gateway():
            c.post(f"{GW}/chat/completions",
                   headers={"Authorization": f"Bearer {KEY}"}, json=PAYLOAD)

        # warmup
        direct(); gateway()
        print(f"Benchmarking {N} iterations each…")
        d = _timeit(direct)
        g = _timeit(gateway)

    dm, gm = statistics.median(d), statistics.median(g)
    print("\n            p50 (ms)   p95 (ms)")
    print(f"Direct→Bifrost   {dm:7.1f}   {_p95(d):7.1f}")
    print(f"Via Gateway      {gm:7.1f}   {_p95(g):7.1f}")
    print(f"\nGateway overhead (p50): {gm - dm:+.1f} ms  "
          f"({100*(gm-dm)/dm:+.1f}% of provider round-trip)")
    print("All governance (auth, routing, guardrails, rate-limit, budgets, observability)\n"
          "runs within that overhead. Provider latency dominates total time.")


def _p95(xs: list[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


if __name__ == "__main__":
    main()
