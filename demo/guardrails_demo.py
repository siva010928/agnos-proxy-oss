"""Guardrails demo - PII blocked at the gateway BEFORE the model is called."""
from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()
GW = os.getenv("AGNOS_GATEWAY_URL", "http://localhost:8090/v1")
# Document Processing (ws-novatech-payments) enforces PII guardrails in BLOCK mode.
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")


def main() -> None:
    print(f"→ Document Processing (PII guardrail = block) → {GW}")
    r = httpx.post(f"{GW}/chat/completions",
                   headers={"Authorization": f"Bearer {KEY}"},
                   json={"model": "claude-sonnet-4-5",
                         "messages": [{"role": "user", "content": "My SSN is 123-45-6789, store it."}],
                         "max_tokens": 20}, timeout=30)
    print(f"← HTTP {r.status_code}")
    print("← body:", r.json())
    print("\n(The request was blocked at the gateway; the model never received the SSN.)")


if __name__ == "__main__":
    main()
