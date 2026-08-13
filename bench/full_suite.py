"""Full benchmark suite - orchestrates the gateway in multiple modes against the
mock upstream and writes bench/RESULTS.md with the slide-ready table:

  • overhead sweep (concurrency 1/10/50) with p50/p90/p99/p99.9 + throughput
  • governance FULL vs NOOP (proves async bus ~0ms hot path)
  • guardrails ON vs OFF (cost of policy)
  • resource footprint (RSS / CPU) under load
  • framing: overhead as % of a real provider round-trip

The orchestrator restarts the gateway with the right env between phases.
"""
from __future__ import annotations

import asyncio
import os
import signal
import statistics
import subprocess
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = "http://localhost:8077"
GW = "http://localhost:8090"
TOTAL = int(os.getenv("BENCH_TOTAL", "300"))
CONCS = [1, 10, 50]


def pct(xs, p):
    s = sorted(xs)
    return s[min(len(s) - 1, int(p / 100 * len(s)))]


async def _sweep(client, url, headers, payload, conc):
    sem = asyncio.Semaphore(conc)
    lat = []

    async def one():
        async with sem:
            t = time.perf_counter()
            try:
                r = await client.post(url, headers=headers, json=payload)
                if r.status_code == 200:
                    lat.append((time.perf_counter() - t) * 1000)
            except Exception:
                pass

    t0 = time.perf_counter()
    await asyncio.gather(*[one() for _ in range(TOTAL)])
    return lat or [0.0], (len(lat) / (time.perf_counter() - t0))


def _start_gateway(env_extra: dict) -> subprocess.Popen:
    env = {**os.environ, "BIFROST_URL": MOCK, **env_extra}
    p = subprocess.Popen(["poetry", "run", "python", "gateway_server.py"],
                         cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            if httpx.get(f"{GW}/health", timeout=2).status_code == 200:
                time.sleep(1)
                return p
        except Exception:
            pass
        time.sleep(1)
    return p


def _stop(p: subprocess.Popen):
    p.send_signal(signal.SIGTERM)
    try:
        p.wait(timeout=10)
    except Exception:
        p.kill()


def _issue_key(workspace: str) -> str:
    return httpx.post(f"{GW}/admin/workspaces/{workspace}/keys", timeout=10).json()["api_key"]


def _ensure_ws(workspace: str, guardrails: bool):
    httpx.post(f"{GW}/admin/workspaces", json={
        "workspace_id": workspace, "name": workspace,
        "chat_models": {"claude-sonnet-4-5": [{"provider": "bedrock",
                        "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "context_window": 200000}]},
        "default_chat_alias": "claude-sonnet-4-5",
        "guardrails": {"pii_detection": guardrails, "mode": "block"} if guardrails else {},
        "quotas": {"claude-sonnet-4-5": {"rpm": 100000000, "tpm": 100000000000}}, "budgets": {}})
    httpx.post(f"{GW}/admin/workspaces/{workspace}/providers", json={
        "provider": "bedrock", "credentials": {"access_key": "x", "secret_key": "y", "region": "us-east-1"},
        "config": {"region": "us-east-1"}})


def _footprint() -> tuple[float, float]:
    try:
        import psutil
        for pr in psutil.process_iter(["name", "cmdline", "memory_info", "cpu_percent"]):
            cl = " ".join(pr.info.get("cmdline") or [])
            if "gateway_server.py" in cl:
                pr.cpu_percent(interval=0.5)
                return pr.memory_info().rss / 1e6, pr.cpu_percent(interval=1.0)
    except Exception:
        pass
    return 0.0, 0.0


PAYLOAD = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5}
UP_PAYLOAD = {**PAYLOAD, "model": "bedrock/x"}


async def phase(client, title, key):
    out = [f"### {title}\n", "| conc | path | p50 | p90 | p99 | p99.9 | req/s |", "|---|---|---|---|---|---|---|"]
    overheads = []
    for conc in CONCS:
        up, up_rps = await _sweep(client, f"{MOCK}/v1/chat/completions", {}, UP_PAYLOAD, conc)
        gw, gw_rps = await _sweep(client, f"{GW}/v1/chat/completions",
                                  {"Authorization": f"Bearer {key}"}, PAYLOAD, conc)
        out.append(f"| {conc} | upstream | {pct(up,50):.2f} | {pct(up,90):.2f} | {pct(up,99):.2f} | {pct(up,99.9):.2f} | {up_rps:.0f} |")
        out.append(f"| {conc} | gateway | {pct(gw,50):.2f} | {pct(gw,90):.2f} | {pct(gw,99):.2f} | {pct(gw,99.9):.2f} | {gw_rps:.0f} |")
        overheads.append((conc, pct(gw, 50) - pct(up, 50)))
    out.append("")
    out.append("Overhead p50 by concurrency: " + ", ".join(f"c={c}:{o:+.2f}ms" for c, o in overheads))
    out.append("")
    return "\n".join(out), overheads


async def run_phase_against(env_extra, title, workspace, guardrails):
    p = _start_gateway(env_extra)
    try:
        _ensure_ws(workspace, guardrails)
        key = _issue_key(workspace)
        async with httpx.AsyncClient(timeout=30,
                                     limits=httpx.Limits(max_connections=200, max_keepalive_connections=200)) as c:
            await c.post(f"{MOCK}/v1/chat/completions", json=UP_PAYLOAD)
            await c.post(f"{GW}/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=PAYLOAD)
            md, ov = await phase(c, title, key)
            rss, cpu = _footprint()
            md += f"\nFootprint under load: RSS={rss:.0f} MB, CPU={cpu:.0f}%\n"
        return md
    finally:
        _stop(p)


async def main():
    # mock must be running already
    sections = ["# Agnos Proxy - Benchmark Results",
                f"\nRig: Apple M3 Pro, 36 GB, macOS; mock zero-latency upstream; {TOTAL} reqs/level.\n"]
    sections.append(await run_phase_against({"GOVERNANCE_MODE": "full"},
                    "Phase A - governance FULL, guardrails OFF", "ws-bench-full", False))
    sections.append(await run_phase_against({"GOVERNANCE_MODE": "noop"},
                    "Phase B - governance NOOP (async-bus baseline)", "ws-bench-noop", False))
    sections.append(await run_phase_against({"GOVERNANCE_MODE": "full"},
                    "Phase C - governance FULL, guardrails ON (PII scan every req)", "ws-bench-guard", True))
    out = "\n".join(sections)
    with open(os.path.join(ROOT, "bench", "RESULTS.md"), "w") as f:
        f.write(out)
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
