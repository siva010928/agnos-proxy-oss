"""Raw HTTP demo - proves ANY language/client works (no framework needed).

Run the gateway first, then: poetry run python demo/raw_http_demo.py
"""
from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()
GW = os.getenv("AGNOS_GATEWAY_URL", "http://localhost:8090/v1")
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")


def main() -> None:
    print(f"→ raw httpx → {GW}/chat/completions  (workspace key, no provider creds)")
    r = httpx.post(f"{GW}/chat/completions",
                   headers={"Authorization": f"Bearer {KEY}",
                            "X-Gateway-Use-Case": "demo.raw_http"},
                   json={"model": "claude-sonnet-4-5",
                         "messages": [{"role": "user", "content": "In one short sentence, what is an LLM gateway?"}],
                         "max_tokens": 60}, timeout=60)
    r.raise_for_status()
    d = r.json()
    print("← model:", d["model"])
    print("← text :", d["choices"][0]["message"]["content"])
    print("← usage:", d["usage"])


if __name__ == "__main__":
    main()
