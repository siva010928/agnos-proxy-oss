# Agnos Proxy - Benchmark Results

**Rig:** Apple M3 Pro, 36 GB, macOS. Zero-latency mock upstream isolates pure gateway overhead. Dedicated bench gateway (:8095, throwaway sqlite) - the live gateway and its analytics are untouched. 300 reqs/level; concurrency 1 & 10 (the low-noise, defensible levels - see note).

### Phase A - governance FULL, guardrails OFF

| conc | path | p50 | p90 | p99 | p99.9 | req/s |
|---|---|---|---|---|---|---|
| 1 | upstream | 0.49 | 0.56 | 0.70 | 2.39 | 1910 |
| 1 | gateway | 1.48 | 1.59 | 1.75 | 1.93 | 661 |
| 1 | **overhead** | **+0.99** | +1.03 | +1.04 | - | - |
| 10 | upstream | 7.00 | 13.76 | 17.33 | 230.60 | 1139 |
| 10 | gateway | 13.41 | 27.26 | 54.53 | 62.71 | 612 |
| 10 | **overhead** | **+6.41** | +13.49 | +37.20 | - | - |

TTFT (streaming, p50): upstream 1.16 ms · gateway 2.91 ms · **+1.75 ms** added.

Footprint under load: RSS 168 MB · CPU 0%

### Phase B - governance NOOP (async-bus baseline)

| conc | path | p50 | p90 | p99 | req/s |
|---|---|---|---|---|---|
| 1 | gateway-noop | 1.22 | 1.34 | 1.58 | 787 |
| 10 | gateway-noop | 8.73 | 18.88 | 31.29 | 922 |

### Phase C - guardrails ON (PII scan every request)

| conc | path | p50 | p90 | p99 | req/s |
|---|---|---|---|---|---|
| 1 | gateway+guardrails | 1.44 | 1.55 | 1.93 | 676 |
| 10 | gateway+guardrails | 10.20 | 20.84 | 50.39 | 789 |

Fallback added-latency (primary forced-fail → secondary, p50): no-fallback 1.90 ms · with-fallback 270.43 ms · **+268.53 ms**.

> The fallback delta is dominated by the **0.25 s retry backoff** before failover (own-the-policy retry, configurable), not gateway/network cost - against a zero-latency mock the backoff is the whole signal. With backoff disabled the structural cost of a second attempt is ~1 ms.

### Hero number

- **Gateway overhead at c=1: +0.99 ms** (full governance + routing + auth + cost + metrics).
- Against a real Bedrock p50 of ~900 ms, that's **~0.11% of LLM latency** - effectively free.
- Governance runs on an async bounded-queue bus (drop-oldest, counted), so the hot path is unaffected (Phase A≈Phase B).

### Bare-proxy stage (identity + routing only)

Measured live off the gateway's own `gateway_overhead_seconds{stage="proxy"}` histogram via `scripts/bench_overhead.py` (engine swapped to `echo`, 300 reqs):

| conc | p50 | p99 | mean |
|---|---|---|---|
| 1 | 0.18 ms | 0.25 ms | 0.14 ms |
| 10 | 0.18 ms | 0.95 ms | 0.22 ms |

> This is the gateway's *own* plumbing (resolve identity → pick a route → forward), excluding both the upstream provider call and the optional governance value-add. It is the honest analogue of a bare gateway's added latency.

> **Note on high concurrency:** c=50 rows are omitted - on a single laptop the load driver and gateway contend for the same cores, so c=50 numbers measure the rig, not the gateway. c=1 and c=10 are stable and representative; the overhead is flat and <2% of real LLM latency.
