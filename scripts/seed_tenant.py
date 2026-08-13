"""Seed an "NovaTech" full tenant - the headline WAVE 17 setup.

A single "NovaTech" workspace governing three real component pipelines (each with
its own component config, alias chain, guardrails, budget). One workspace key
is issued for every component so each can call /v1 with no SDK and no shim
- just `base_url=$GW/v1` + `Authorization: Bearer <key>` +
`X-Gateway-Component: <name>`.

This is the data substrate the centralized-governance demo runs against.
After running, all attribution flows through to /admin/cost?group_by=component
and the dashboard's components panel.

Usage:
    .venv/bin/python scripts/seed_tenant.py
    .venv/bin/python scripts/seed_tenant.py --reset    # wipe + reseed

Outputs a JSON file at scripts/seed_tenant.out.json with the issued keys
(plaintext shown ONCE - store somewhere safe).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

GW = os.getenv("GATEWAY_URL", "http://localhost:8090")
ADMIN = {"X-Admin-Token": os.getenv("PLATFORM_ADMIN_TOKEN", "platform-admin-secret"),
         "Content-Type": "application/json"}

WS_ID = "novatech"
WS_NAME = "NovaTechoration"

# Three components in the NovaTech tenant
COMPONENTS = [
    {
        "name": "agnos",
        "display_name": "Agnos (code → spec)",
        "default_alias": "claude-sonnet-4-5",
        "chat_models": {
            "claude-sonnet-4-5": [
                {"provider": "bedrock", "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "weight": 3},
                {"provider": "anthropic", "model_id": "claude-sonnet-4-5-20250929", "weight": 1},
            ],
        },
        "guardrails": {"mode": "block", "pii_detection": True, "secrets_detection": True},
        "quotas": {"claude-sonnet-4-5": {"rpm": 600, "tpm": 800_000}},
        "budgets": {"workspace_usd": 2000.0, "user_usd": 500.0},
    },
    {
        "name": "codegen",
        "display_name": "SpecToCode (spec → code)",
        "default_alias": "claude-sonnet-4-5",
        "chat_models": {
            "claude-sonnet-4-5": [
                {"provider": "anthropic", "model_id": "claude-sonnet-4-5-20250929", "weight": 1},
                {"provider": "bedrock", "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "weight": 1},
            ],
        },
        "guardrails": {"mode": "block", "secrets_detection": True},
        "quotas": {"claude-sonnet-4-5": {"rpm": 400, "tpm": 600_000}},
        "budgets": {"workspace_usd": 1500.0, "user_usd": 400.0},
    },
    {
        "name": "search-index",
        "display_name": "Search Index (RAG / search)",
        "default_alias": "gemini-flash",
        "chat_models": {
            "gemini-flash": [
                {"provider": "gemini", "model_id": "gemini-2.5-flash", "weight": 1},
                {"provider": "bedrock", "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "weight": 1},
            ],
        },
        "guardrails": {"mode": "redact", "pii_detection": True},
        "quotas": {"gemini-flash": {"rpm": 800, "tpm": 1_200_000}},
        "budgets": {"workspace_usd": 800.0, "user_usd": 200.0},
    },
]


def _provider_configs() -> list[dict]:
    """Return the provider rows to attach to NovaTech. Reads creds from env (.env)."""
    rows: list[dict] = []
    if os.getenv("AWS_ACCESS_KEY_ID"):
        rows.append({
            "provider": "bedrock",
            "credentials": {
                "access_key": os.environ["AWS_ACCESS_KEY_ID"],
                "secret_key": os.environ["AWS_SECRET_ACCESS_KEY"],
            },
            "config": {"region": os.getenv("AWS_REGION_NAME", "us-east-1")},
        })
    if os.getenv("ANTHROPIC_API_KEY"):
        rows.append({
            "provider": "anthropic",
            "credentials": {"api_key": os.environ["ANTHROPIC_API_KEY"]},
            "config": {},
        })
    if os.getenv("GEMINI_API_KEY"):
        rows.append({
            "provider": "gemini",
            "credentials": {"api_key": os.environ["GEMINI_API_KEY"]},
            "config": {},
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="delete the NovaTech workspace first (cascade)")
    ap.add_argument("--out", default="scripts/seed_tenant.out.json")
    args = ap.parse_args()

    # Load env vars
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    with httpx.Client(base_url=GW, headers=ADMIN, timeout=30) as c:
        # 0) Optional reset
        if args.reset:
            r = c.delete(f"/admin/workspaces/{WS_ID}")
            print(f"[0] reset: DELETE {WS_ID} → {r.status_code}")

        # 1) Create the workspace skeleton - chat_models defined at workspace
        # level too, so /v1/models lists all aliases each component knows about.
        # On creation, resolvability check is skipped (providers attached next).
        all_chat: dict = {}
        for comp in COMPONENTS:
            for alias, targets in comp["chat_models"].items():
                if alias not in all_chat:
                    all_chat[alias] = targets
        r = c.post("/admin/workspaces", json={
            "workspace_id": WS_ID,
            "name": WS_NAME,
            "chat_models": all_chat,
            "default_chat_alias": "claude-sonnet-4-5",
            "guardrails": {"mode": "block", "pii_detection": True},
            "budgets": {"workspace_usd": 5000.0, "user_usd": 1000.0},
        })
        if r.status_code == 409:
            print(f"[1] workspace '{WS_ID}' already exists \u2014 reusing (use --reset to wipe)")
        else:
            r.raise_for_status()
            print(f"[1] created workspace '{WS_ID}' \u2014 {WS_NAME}")

        # 2) Attach provider configs (credentials encrypted at rest)
        providers = _provider_configs()
        if not providers:
            print("[2] WARNING: no provider env vars present \u2014 chat calls will 401 upstream")
        for p in providers:
            r = c.post(f"/admin/workspaces/{WS_ID}/providers", json=p)
            if r.status_code != 200:
                print(f"[2] provider {p['provider']}: {r.status_code} {r.text[:200]}")
                continue
            print(f"[2] attached provider '{p['provider']}'")

        # 3) Components are NOT created via admin endpoint (WAVE 20 TRACK 1);
        #    they're auto-registered at runtime when a request carries
        #    X-Gateway-Component: <name>. We skip this step.
        print("[3] skipped component creation (auto-registered at runtime via X-Gateway-Component header)")

        # 4) Issue one workspace key per component (label = component name).
        #    The component identity is carried by the X-Gateway-Component header
        #    or the JWT 'component' claim \u2014 not by the key. Issuing one key per
        #    component just makes attribution + revocation cleaner per-team.
        keys: dict[str, str] = {}
        for comp in COMPONENTS:
            r = c.post(f"/admin/workspaces/{WS_ID}/keys", json={
                "roles": ["member"],
                "expires_at": "2027-12-31",
            })
            r.raise_for_status()
            keys[comp["name"]] = r.json()["api_key"]
            print(f"[4] issued key for component '{comp['name']}' \u2014 {keys[comp['name']][:14]}\u2026")

    out = {"workspace_id": WS_ID, "components": [c["name"] for c in COMPONENTS],
           "keys": keys, "gateway_url": GW + "/v1"}
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"\n\u2713 wrote {args.out} \u2014 plaintext keys are stored ONCE; rotate later via /admin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
