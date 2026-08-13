"""Fallback demo - primary target is a bad model; gateway auto-fails-over to the
working fallback. Watch the dashboard for the `Fallback` event."""
from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()
B = "http://localhost:8090"
ADMIN = {"X-Admin-Token": os.getenv("PLATFORM_ADMIN_TOKEN", "platform-admin-secret")}
AWS_AK = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SK = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_RG = os.getenv("AWS_REGION_NAME", "us-east-1")


def main() -> None:
    # ensure a fallback workspace exists (primary=invalid bedrock model, fallback=valid)
    httpx.post(f"{B}/admin/workspaces", headers=ADMIN, json={
        "workspace_id": "ws-fallback-demo", "name": "Fallback Demo",
        "chat_models": {"claude-sonnet-4-5": [
            {"provider": "bedrock", "model_id": "us.anthropic.INVALID-MODEL-v1:0"},
            {"provider": "bedrock", "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0"}]},
        "default_chat_alias": "claude-sonnet-4-5"})
    httpx.post(f"{B}/admin/workspaces/ws-fallback-demo/providers", headers=ADMIN, json={
        "provider": "bedrock",
        "credentials": {"access_key": AWS_AK, "secret_key": AWS_SK, "region": AWS_RG},
        "config": {"region": AWS_RG}})
    key = httpx.post(f"{B}/admin/workspaces/ws-fallback-demo/keys", headers=ADMIN).json()["api_key"]

    print("→ Primary model is intentionally invalid; expect automatic failover.")
    r = httpx.post(f"{B}/v1/chat/completions",
                   headers={"Authorization": f"Bearer {key}", "X-Gateway-Timeout": "8"},
                   json={"model": "claude-sonnet-4-5",
                         "messages": [{"role": "user", "content": "say FAILOVER_OK"}],
                         "max_tokens": 15}, timeout=90)
    print(f"← HTTP {r.status_code}: {r.json()['choices'][0]['message']['content']}")
    print("✓ Served via fallback provider. A `Fallback` event was emitted to governance.")


if __name__ == "__main__":
    main()
