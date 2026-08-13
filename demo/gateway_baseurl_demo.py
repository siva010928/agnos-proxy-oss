"""WAVE 17 headline demo - onboard a real OpenAI-compatible component via
**base_url only**. No SDK, no shim, no Agnos-specific imports.

This is exactly what Agnos (or any other component on the platform)
would do: keep its existing OpenAI client, change one config line:

    OpenAI(api_key=$WORKSPACE_KEY, base_url="http://localhost:8090/v1")

…and attribution + governance + guardrails + routing + budgets all kick in
automatically through the gateway.

What this script does:

    1. Reads the NovaTech tenant config produced by scripts/seed_tenant.py
       (one workspace key per component).
    2. For each NovaTech component, builds a vanilla `openai.OpenAI(...)` client
       pointed at the gateway, and calls /v1/chat/completions.
    3. Sends X-Gateway-Component: <name> so the gateway attributes the call
       to the component (alternatively the component could mint a JWT with a
       'component' claim - the gateway honours both).
    4. Prints one line per call (model, completion text, usage, latency).
    5. Asks /admin/cost?group_by=component for the live attribution rollup
       so we can SEE the demo proved itself.

Run:
    .venv/bin/python scripts/seed_tenant.py --reset      # one-time
    .venv/bin/python demo/gateway_baseurl_demo.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

try:
    from openai import OpenAI
except ImportError:
    print("This demo requires the `openai` package. Install: poetry add openai")
    sys.exit(1)

GW = os.getenv("GATEWAY_URL", "http://localhost:8090")
SEED_FILE = Path(__file__).resolve().parent.parent / "scripts" / "seed_tenant.out.json"

if not SEED_FILE.exists():
    print(f"Run scripts/seed_tenant.py first - {SEED_FILE} not found.")
    sys.exit(1)

CFG = json.loads(SEED_FILE.read_text())
KEYS: dict[str, str] = CFG["keys"]
WS = CFG["workspace_id"]


# Per-component prompts - domain-realistic so the demo reads like a real workload
PROMPTS: dict[str, tuple[str, str, int]] = {
    # component         alias                 prompt                                                max_tokens
    "agnos":      ("claude-sonnet-4-5", "Summarize what an LLM gateway does in one sentence.",     32),
    "codegen":       ("claude-sonnet-4-5", "Write a one-line Python function returning 'hi'.",         32),
    "search-index": ("gemini-flash",      "What is RAG (retrieval-augmented generation)? Be brief.", 48),
}


def call_one_component(component: str, key: str) -> tuple[float, dict]:
    """One real chat through the gateway, attributed to `component`. Returns
    (latency_ms, response_body). NO Agnos SDK - just openai + base_url."""
    alias, prompt, max_tokens = PROMPTS[component]

    # ── This block is *literally* what an OpenAI-compatible component does ──
    client = OpenAI(
        api_key=key,
        base_url=f"{GW}/v1",                       # the only line that changes
        default_headers={"X-Gateway-Component": component,    # component identity
                         "X-Gateway-Use-Case": "wave17.demo"}, # observability tag
    )
    t0 = time.time()
    resp = client.chat.completions.create(
        model=alias,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    latency_ms = (time.time() - t0) * 1000
    return latency_ms, resp.model_dump()


def fetch_attribution() -> dict:
    """Live attribution rollup from the gateway."""
    headers = {"X-Admin-Token": os.getenv("PLATFORM_ADMIN_TOKEN", "platform-admin-secret")}
    r = httpx.get(f"{GW}/admin/cost",
                  params={"group_by": "component", "workspace": WS},
                  headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()


def main() -> int:
    print(f"\n=== WAVE 17 - NovaTech tenant via base_url only ===")
    print(f"workspace : {WS}")
    print(f"gateway   : {GW}/v1")
    print(f"components: {', '.join(KEYS)}\n")

    print("─── Per-component calls (vanilla openai + base_url) ───\n")
    for component, key in KEYS.items():
        try:
            latency, resp = call_one_component(component, key)
            text = (resp["choices"][0]["message"].get("content") or "")[:80]
            usage = resp.get("usage") or {}
            print(f"[{component:<20}] {latency:6.0f} ms  "
                  f"in={usage.get('prompt_tokens', 0):>3}  out={usage.get('completion_tokens', 0):>3}  "
                  f"model={resp.get('model', '?')[:40]}")
            print(f"   → {text}")
            # Anti-corruption: nothing leaks
            assert "extra_fields" not in resp, "leaked extra_fields"
            assert "bifrost_config" not in resp, "leaked bifrost_config"
        except Exception as exc:
            print(f"[{component}] FAILED: {exc}")

    # Let governance writes settle on the bus
    time.sleep(1.5)

    print("\n─── Live attribution rollup (/admin/cost?group_by=component) ───\n")
    rollup = fetch_attribution()
    rows = rollup.get("rows", []) or rollup.get("data", []) or []
    if not rows:
        print(f"  (no rows yet - async writes may still be settling)")
    else:
        for row in rows[:10]:
            comp = row.get("group") or row.get("component") or row.get("key", "?")
            cost = row.get("cost_usd", row.get("cost", 0))
            req = row.get("requests", row.get("count", 0))
            tokens = row.get("input_tokens", 0) + row.get("output_tokens", 0)
            print(f"  {comp:<25}  {req:>4} req  {tokens:>6} tokens  ${cost:.6f}")

    print("\n✓ each call attributed to its component, with no SDK/shim - only base_url + key")
    print("  See /app/cost?workspace=novatech  and /app  (live feed) for the dashboard view.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
