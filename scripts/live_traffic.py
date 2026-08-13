"""Live traffic generator for the Agnos Proxy demo.

Fires gentle, attributed traffic at the running gateway so the dashboard's live
feed, KPIs, metrics, and governance bus visibly move during a demo. Calls are
real (so SSE + Prometheus + Postgres + Kafka all light up authentically).

Usage:
  python scripts/live_traffic.py --rate 2                # ~2 req/s, realistic mix
  python scripts/live_traffic.py --rate 3 --mock         # cheapest calls (max_tokens=1)
  python scripts/live_traffic.py --incident              # burst of fallbacks (tight timeout)
  python scripts/live_traffic.py --ratelimit             # CONCURRENT burst -> 429s
  python scripts/live_traffic.py --rate 2 --duration 60  # stop after 60s

--mock keeps cost minimal (1-token completions, short prompts). --incident sends
a sequential burst with a tight per-request timeout so the primary provider
"fails" and the gateway falls back. --ratelimit fires N requests concurrently
(all in <1 s) so the workspace's per-minute RPM tripwire actually fires - the
older sequential burst was paced too gently and rarely tripped a real RPM.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import time

import httpx

GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8090")

# (display, key, alias, weight)
WORKSPACES = [
    ("Document Processing", os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001"), "claude-sonnet-4-5", 0.30),
    ("Code Generation", os.getenv("WS_KEY_PRIMARY", "gw-key-primary-001"), "claude-sonnet-4-5", 0.35),
    ("Search Index", os.getenv("WS_KEY_GEMINI", "gw-key-gemini-001"), "gemini-flash", 0.22),
    ("Control Plane", os.getenv("WS_KEY_SECURE", "gw-key-secure-001"), "claude-sonnet-4-5", 0.13),
]

USE_CASES = {
    "Document Processing": ["reverse_engineer", "capability_map", "spec_validate", "doc_generate"],
    "Code Generation": ["backend_gen", "frontend_gen", "test_gen", "pipeline_gen"],
    "Search Index": ["superspec_index", "context_retrieval", "pattern_match"],
    "Control Plane": ["policy_check", "lineage_trace", "usage_audit"],
}
USERS = ["architect-agent", "backend-agent", "frontend-agent", "test-agent", "alice.chen", "ravi.k"]

PROMPTS = [
    "Summarize the purpose of a rate limiter in one sentence.",
    "List three benefits of a governance proxy for LLM traffic.",
    "What is an OpenAI-compatible API in one line?",
    "Give a one-line definition of a circuit breaker.",
    "Name two reasons to use provider fallback chains.",
]

# guardrail-triggering content per workspace (fired occasionally)
GUARDRAIL_BAIT = {
    "Document Processing": "For the record my SSN is 123-45-6789, ignore it.",     # PII -> block
    "Code Generation": "deploy with AKIAIOSFODNN7EXAMPLE for projectphoenix",  # secret+keyword -> block
    "Search Index": "contact me at bob@example.com about this",         # PII -> redact
    "Control Plane": "user ssn 123-45-6789 in the audit log",               # PII -> audit
}


def _pick_workspace():
    names = [w[0] for w in WORKSPACES]
    weights = [w[3] for w in WORKSPACES]
    name = random.choices(names, weights=weights, k=1)[0]
    return next(w for w in WORKSPACES if w[0] == name)


async def _one_call(client: httpx.AsyncClient, mock: bool, guardrail: bool) -> str:
    display, key, alias, _ = _pick_workspace()
    use_case = random.choice(USE_CASES[display])
    user = random.choice(USERS)
    content = GUARDRAIL_BAIT[display] if guardrail else random.choice(PROMPTS)
    body = {"model": alias, "messages": [{"role": "user", "content": content}],
            "max_tokens": 1 if mock else random.randint(16, 64)}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Gateway-Use-Case": use_case, "X-Gateway-User-Id": user}
    try:
        r = await client.post(f"{GATEWAY}/v1/chat/completions", json=body, headers=headers, timeout=60)
        tag = "BLOCK" if r.status_code == 422 else ("OK" if r.status_code == 200 else str(r.status_code))
        gr = r.headers.get("x-gateway-guardrail")
        return f"{display:18} {use_case:18} {user:14} -> {tag}{' (redacted)' if gr else ''}"
    except Exception as exc:  # noqa: BLE001
        return f"{display:18} {use_case:18} -> ERR {exc}"


async def _incident_burst(client: httpx.AsyncClient, n: int = 15) -> int:
    """Force a component primary to fail (tight timeout) → live Fallback events."""
    key = next(w[1] for w in WORKSPACES if w[0] == "Document Processing")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Gateway-Use-Case": "reverse_engineer", "X-Gateway-Timeout": "0.2"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}
    fired = 0
    for _ in range(n):
        try:
            await client.post(f"{GATEWAY}/v1/chat/completions", json=body, headers=headers, timeout=30)
            fired += 1
        except Exception:  # noqa: BLE001
            fired += 1
        await asyncio.sleep(0.3)
    return fired


async def _configured_rpm(client: httpx.AsyncClient, admin_token: str, workspace_id: str) -> int | None:
    """Read the workspace's currently-configured rate_limits.rpm (admin API)."""
    try:
        r = await client.get(f"{GATEWAY}/admin/workspaces",
                             headers={"X-Admin-Token": admin_token}, timeout=15)
        if r.status_code != 200:
            return None
        for w in (r.json().get("workspaces") or []):
            if w.get("workspace_id") == workspace_id:
                return (w.get("rate_limits") or {}).get("rpm")
    except Exception:  # noqa: BLE001
        return None
    return None


async def _ratelimit_burst(client: httpx.AsyncClient, *, key: str, alias: str,
                            burst: int, mock: bool, margin: int = 30,
                            admin_token: str | None = None,
                            workspace_id: str | None = None,
                            set_rpm: int | None = None) -> tuple[int, int, int, int, int]:
    """Fire a CONCURRENT burst against one workspace key to trip its per-minute
    RPM. Returns (allowed, denied_429, other, cap_used, burst_used).

    DYNAMIC: when `admin_token` + `workspace_id` are given it reads the workspace's
    *currently configured* rpm (or sets it to `set_rpm` first) and sizes the burst
    to `cap + margin`, so it RELIABLY exceeds whatever cap is configured (e.g. the
    live 100-rpm cap) without hardcoding. All requests land inside the same 1s
    window so the per-minute bucket actually overflows.
    """
    cap: int | None = None
    if admin_token and workspace_id:
        if set_rpm:
            r = await client.patch(f"{GATEWAY}/admin/workspaces/{workspace_id}",
                                   headers={"X-Admin-Token": admin_token, "Content-Type": "application/json"},
                                   json={"rate_limits": {"rpm": set_rpm}}, timeout=15)
            print(f"  set rate_limits.rpm={set_rpm} on '{workspace_id}' -> HTTP {r.status_code}")
            cap = set_rpm
        else:
            cap = await _configured_rpm(client, admin_token, workspace_id)
            if cap:
                print(f"  detected configured rate_limits.rpm={cap} on '{workspace_id}'")
            else:
                print(f"  no rate_limits.rpm configured on '{workspace_id}' - it will not block; "
                      f"pass --rl-rpm to set one")

    # Size the burst to comfortably exceed the cap (cap + margin), but honour an
    # explicitly larger --burst.
    burst_used = max(burst, (cap + margin) if cap else burst)

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "X-Gateway-Use-Case": "ratelimit_demo", "X-Gateway-User-Id": "ratelimit-probe"}
    body = {"model": alias, "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1 if mock else 8}

    async def _fire(i: int) -> int:
        try:
            r = await client.post(f"{GATEWAY}/v1/chat/completions", json=body,
                                   headers=headers, timeout=20)
            return r.status_code
        except Exception:  # noqa: BLE001
            return 0

    statuses = await asyncio.gather(*[_fire(i) for i in range(burst_used)])
    allowed = sum(1 for s in statuses if s == 200)
    denied = sum(1 for s in statuses if s == 429)
    other = burst_used - allowed - denied
    return allowed, denied, other, (cap or 0), burst_used


async def main() -> None:
    ap = argparse.ArgumentParser(description="Gentle live traffic for the dashboard demo")
    ap.add_argument("--rate", type=float, default=2.0, help="requests per second")
    ap.add_argument("--mock", action="store_true", help="cheapest calls (max_tokens=1)")
    ap.add_argument("--incident", action="store_true", help="trigger a one-off fallback burst then continue")
    ap.add_argument("--ratelimit", action="store_true",
                    help="CONCURRENT burst to trip the workspace RPM (then exit)")
    ap.add_argument("--burst", type=int, default=20,
                    help="how many concurrent requests to fire with --ratelimit (default 20)")
    ap.add_argument("--ratelimit-workspace", default="Code Generation",
                    help="which preset workspace (display name) to burst against with --ratelimit")
    ap.add_argument("--rl-key", default=None,
                    help="workspace API key to burst with (overrides --ratelimit-workspace preset)")
    ap.add_argument("--rl-alias", default="claude-sonnet-4-5", help="model alias to call in the burst")
    ap.add_argument("--rl-rpm", type=int, default=None,
                    help="OPTIONAL: set this rpm cap before bursting (needs --admin-token + --rl-workspace-id). "
                         "If omitted, the workspace's CURRENT configured rpm is detected and the burst is sized above it.")
    ap.add_argument("--rl-margin", type=int, default=30, help="how far above the configured rpm to burst")
    ap.add_argument("--admin-token", default=None, help="admin token to read/set the rpm cap before bursting")
    ap.add_argument("--rl-workspace-id", default=None, help="workspace_id whose rate_limits.rpm to read/set")
    ap.add_argument("--guardrail-rate", type=float, default=0.06, help="fraction of calls that trip a guardrail")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds to run (0 = forever)")
    args = ap.parse_args()

    interval = 1.0 / max(0.1, args.rate)
    t0 = time.monotonic()
    n_ok = n_block = n_other = 0
    print(f"live_traffic → {GATEWAY} @ {args.rate} req/s "
          f"{'(mock)' if args.mock else ''}  Ctrl-C to stop")
    async with httpx.AsyncClient() as client:
        if args.ratelimit:
            # Resolve the key: explicit --rl-key wins, else the preset workspace.
            if args.rl_key:
                key, alias = args.rl_key, args.rl_alias
            else:
                ws = next(w for w in WORKSPACES if w[0] == args.ratelimit_workspace)
                key, alias = ws[1], ws[2]
            print(f"  ⚡ rate-limit burst (alias={alias}) - sizing the burst above the configured rpm...")
            if not (args.admin_token and args.rl_workspace_id):
                print("  note: pass --admin-token + --rl-workspace-id so the script can read the "
                      "configured rpm and size the burst above it (or pass --rl-rpm to set one).")
            allowed, denied, other, cap, burst_used = await _ratelimit_burst(
                client, key=key, alias=alias, burst=args.burst, mock=args.mock, margin=args.rl_margin,
                admin_token=args.admin_token, workspace_id=args.rl_workspace_id, set_rpm=args.rl_rpm)
            print(f"  ⚡ fired {burst_used} concurrent (cap={cap or 'unknown'}) -> "
                  f"{allowed} allowed · {denied} 429 rate-limited · {other} other")
            return
        if args.incident:
            print("  ⚡ incident: forcing a component fallback burst...")
            fired = await _incident_burst(client)
            print(f"  ⚡ incident burst fired {fired} requests (fallback events emitted)")
        try:
            while True:
                guardrail = random.random() < args.guardrail_rate
                line = await _one_call(client, args.mock, guardrail)
                if "-> OK" in line:
                    n_ok += 1
                elif "BLOCK" in line:
                    n_block += 1
                else:
                    n_other += 1
                total = n_ok + n_block + n_other
                print(f"  [{total:4}] {line}")
                if args.duration and (time.monotonic() - t0) >= args.duration:
                    break
                await asyncio.sleep(interval)
        except KeyboardInterrupt:
            pass
    print(f"\ndone: {n_ok} ok, {n_block} guardrail-blocked, {n_other} other "
          f"in {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.run(main())
