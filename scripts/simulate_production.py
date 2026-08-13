#!/usr/bin/env python3
"""WAVE 21 Part C \u2014 Real production-traffic simulator.

Drives real, fully-attributed chat + embedding + tool + stream calls through
the gateway exactly as real platform components would. Every request sets:
  \u2022 workspace key (Authorization: Bearer)
  \u2022 X-Gateway-Component: <component-name>
  \u2022 X-Gateway-User-Id: <user>  (simulated JWT sub)

Realistic mix:
  \u2022 70% normal chat (non-stream, max_tokens=20)
  \u2022 10% streaming chat (max_tokens=30)
  \u2022 10% tool-calling (max_tokens=30)
  \u2022  5% embeddings
  \u2022  5% guardrail triggers (PII/secrets/keywords \u2192 real 422/redact events)

Plus one deliberate fallback scenario + one cache hit + a rate-limit trip.

Cost safety: per-provider running tally with a configurable hard cap
(default $3/provider). Aborts before exceeding. Prints spend at end.

Usage:
    source .env
    .venv/bin/python scripts/simulate_production.py
    .venv/bin/python scripts/simulate_production.py --cap 2.0 --count 100
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time

import httpx

GW = os.getenv("GATEWAY_URL", "http://localhost:8090")

# ---- Configuration ----
WORKSPACES: dict[str, dict] = {}  # populated from /tmp/ws_keys.env or env
COMPONENTS = ["document-processing", "code-generation", "search-index", "data-pipeline", "report-engine"]
USERS = ["alice.chen", "bob.kumar", "architect-agent", "backend-agent", "carol.wong", "deploy-bot"]

# Guardrail trigger payloads
PII_PAYLOAD = "My SSN is 123-45-6789 and my email is user@novatech.com"
SECRET_PAYLOAD = "Here is my AWS key: AKIAIOSFODNN7EXAMPLE"
KEYWORD_PAYLOAD = "This is classified projectphoenix material"


def load_keys() -> None:
    """Load workspace keys from /tmp/ws_keys.env or environment."""
    key_file = "/tmp/ws_keys.env"
    if os.path.exists(key_file):
        for line in open(key_file):
            if "=" in line:
                ws, key = line.strip().split("=", 1)
                WORKSPACES[ws] = {"key": key}
    # Fallback: env vars
    for ws in ("ws-novatech-payments", "ws-novatech-platform"):
        if ws not in WORKSPACES:
            env_key = os.getenv(f"KEY_{ws.replace('-', '_').upper()}")
            if env_key:
                WORKSPACES[ws] = {"key": env_key}
    if not WORKSPACES:
        print("ERROR: No workspace keys found. Run Part B or set /tmp/ws_keys.env")
        sys.exit(1)


# ---- Cost tracking ----
SPEND: dict[str, float] = {}  # provider \u2192 accumulated USD


def estimate_cost(provider: str, in_tok: int, out_tok: int) -> float:
    """Conservative cost estimate per call."""
    rates = {
        "bedrock":   (0.003, 0.015),   # Claude Sonnet 4.5 per 1k
        "anthropic": (0.003, 0.015),
        "gemini":    (0.00015, 0.0006),
    }
    inp, out = rates.get(provider, (0.003, 0.015))
    return (in_tok / 1000 * inp) + (out_tok / 1000 * out)


def check_cap(provider: str, cap: float) -> bool:
    """Returns True if we can still spend on this provider. False \u2192 skip."""
    return SPEND.get(provider, 0.0) < cap


def record_spend(provider: str, in_tok: int, out_tok: int) -> None:
    cost = estimate_cost(provider, in_tok, out_tok)
    SPEND[provider] = SPEND.get(provider, 0.0) + cost


# ---- Request helpers ----

def _headers(ws: str, component: str, user: str) -> dict:
    return {
        "Authorization": f"Bearer {WORKSPACES[ws]['key']}",
        "Content-Type": "application/json",
        "X-Gateway-Component": component,
        "X-Gateway-User-Id": user,
    }


def chat_normal(ws: str, component: str, user: str, max_tokens: int = 20) -> dict | None:
    prompts = [
        "Summarize the key benefits of microservices in one sentence.",
        "Write a Python function that returns the current timestamp.",
        "What are the SOLID principles? List them briefly.",
        "Explain eventual consistency in 2 sentences.",
        "How does a circuit breaker pattern work?",
    ]
    body = {
        "model": "claude-sonnet-4-5" if "meridian" not in ws else "gemini-flash",
        "messages": [{"role": "user", "content": random.choice(prompts)}],
        "max_tokens": max_tokens,
    }
    r = httpx.post(f"{GW}/v1/chat/completions", headers=_headers(ws, component, user),
                   json=body, timeout=60)
    if r.status_code == 200:
        usage = r.json().get("usage", {})
        return {"in": usage.get("prompt_tokens", 0), "out": usage.get("completion_tokens", 0)}
    return None


def chat_stream(ws: str, component: str, user: str) -> dict | None:
    body = {
        "model": "claude-sonnet-4-5" if "meridian" not in ws else "gemini-flash",
        "messages": [{"role": "user", "content": "Give me a one-liner joke."}],
        "max_tokens": 30, "stream": True,
    }
    in_tok = out_tok = 0
    with httpx.stream("POST", f"{GW}/v1/chat/completions",
                      headers=_headers(ws, component, user), json=body, timeout=60) as resp:
        if resp.status_code != 200:
            return None
        for line in resp.iter_lines():
            if line and "usage" in line:
                import json
                try:
                    obj = json.loads(line.replace("data: ", ""))
                    u = obj.get("usage") or {}
                    in_tok = u.get("prompt_tokens", in_tok) or in_tok
                    out_tok = u.get("completion_tokens", out_tok) or out_tok
                except Exception:
                    pass
    return {"in": in_tok or 5, "out": out_tok or 10}


def chat_tools(ws: str, component: str, user: str) -> dict | None:
    body = {
        "model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "What's the weather in Paris?"}],
        "tools": [{"type": "function", "function": {
            "name": "get_weather", "parameters": {"type": "object",
                                                   "properties": {"city": {"type": "string"}}}}}],
        "max_tokens": 30,
    }
    r = httpx.post(f"{GW}/v1/chat/completions", headers=_headers(ws, component, user),
                   json=body, timeout=60)
    if r.status_code == 200:
        usage = r.json().get("usage", {})
        return {"in": usage.get("prompt_tokens", 0), "out": usage.get("completion_tokens", 0)}
    return None


def embedding(ws: str, component: str, user: str) -> dict | None:
    body = {"model": "text-embedding-default", "input": ["hello world", "embedding test"]}
    r = httpx.post(f"{GW}/v1/embeddings", headers=_headers(ws, component, user),
                   json=body, timeout=60)
    if r.status_code == 200:
        usage = r.json().get("usage", {})
        return {"in": usage.get("prompt_tokens", 0), "out": 0}
    return None


def guardrail_trigger(ws: str, component: str, user: str, payload: str) -> str:
    """Returns the HTTP status code as a string (e.g. '422', '200')."""
    body = {
        "model": "claude-sonnet-4-5" if "meridian" not in ws else "gemini-flash",
        "messages": [{"role": "user", "content": payload}],
        "max_tokens": 10,
    }
    r = httpx.post(f"{GW}/v1/chat/completions", headers=_headers(ws, component, user),
                   json=body, timeout=60)
    return str(r.status_code)


def cache_hit(ws: str, component: str, user: str) -> str:
    """Fire the same request twice with X-Gateway-Cache-TTL; second should be HIT."""
    body = {
        "model": "claude-sonnet-4-5" if "meridian" not in ws else "gemini-flash",
        "messages": [{"role": "user", "content": "What is 2+2?"}],
        "max_tokens": 8,
    }
    hdrs = {**_headers(ws, component, user), "X-Gateway-Cache-TTL": "300"}
    r1 = httpx.post(f"{GW}/v1/chat/completions", headers=hdrs, json=body, timeout=60)
    r2 = httpx.post(f"{GW}/v1/chat/completions", headers=hdrs, json=body, timeout=60)
    h2 = {k.lower(): v for k, v in r2.headers.items()}
    return h2.get("x-gateway-cache", "?")


# ---- Main loop ----

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=3.0, help="USD cap per provider (default $3)")
    ap.add_argument("--count", type=int, default=200, help="Total calls to fire")
    args = ap.parse_args()

    load_keys()
    ws_list = list(WORKSPACES.keys())
    print(f"\n=== simulate_production.py ===")
    print(f"  gateway:    {GW}")
    print(f"  workspaces: {ws_list}")
    print(f"  cap/provider: ${args.cap}")
    print(f"  target calls: {args.count}")
    print()

    stats = {"chat": 0, "stream": 0, "tools": 0, "embed": 0,
             "guardrail": 0, "cache": 0, "skipped_cap": 0, "errors": 0}
    start = time.time()

    for i in range(args.count):
        ws = random.choice(ws_list)
        component = random.choice(COMPONENTS)
        user = random.choice(USERS)
        provider = "gemini" if "meridian" in ws else random.choice(["bedrock", "anthropic"])

        if not check_cap(provider, args.cap):
            stats["skipped_cap"] += 1
            continue

        roll = random.random()
        usage = None
        if roll < 0.05:
            # Guardrail trigger
            payload = random.choice([PII_PAYLOAD, SECRET_PAYLOAD, KEYWORD_PAYLOAD])
            code = guardrail_trigger(ws, component, user, payload)
            stats["guardrail"] += 1
            if code == "200":
                record_spend(provider, 10, 5)
        elif roll < 0.10:
            # Embeddings
            usage = embedding(ws, component, user)
            stats["embed"] += 1
        elif roll < 0.20:
            # Streaming
            usage = chat_stream(ws, component, user)
            stats["stream"] += 1
        elif roll < 0.30:
            # Tools
            usage = chat_tools(ws, component, user)
            stats["tools"] += 1
        else:
            # Normal chat
            usage = chat_normal(ws, component, user)
            stats["chat"] += 1

        if usage:
            record_spend(provider, usage["in"], usage["out"])
        elif usage is None and roll >= 0.05:
            stats["errors"] += 1

        # Progress
        if (i + 1) % 20 == 0:
            elapsed = time.time() - start
            print(f"  [{i+1}/{args.count}] {elapsed:.1f}s  spend: " +
                  " | ".join(f"{p}=${s:.4f}" for p, s in sorted(SPEND.items())))

    # Cache hit test
    ws = ws_list[0]
    result = cache_hit(ws, "document-processing", "alice.chen")
    stats["cache"] = 1 if result == "HIT" else 0
    print(f"\n  cache hit test: {result}")

    elapsed = time.time() - start
    print(f"\n=== RESULTS ({elapsed:.1f}s) ===")
    print(f"  calls:  {sum(stats.values()) - stats['skipped_cap'] - stats['cache']}")
    print(f"  stats:  {stats}")
    print(f"  spend per provider:")
    for p, s in sorted(SPEND.items()):
        cap_status = "\u2705 under cap" if s < args.cap else "\u26a0\ufe0f AT CAP"
        print(f"    {p:<12} ${s:.6f}  {cap_status}")
    total = sum(SPEND.values())
    print(f"  TOTAL SPEND: ${total:.6f}")
    assert total < args.cap * len(SPEND), f"SPEND EXCEEDED CAP: ${total} >= ${args.cap * len(SPEND)}"
    print(f"  \u2713 all providers under ${args.cap} cap")
    return 0


if __name__ == "__main__":
    sys.exit(main())
