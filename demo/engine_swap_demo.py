"""Engine-swap demo - same workspace key, same governance, different engine.

Usage:
  # terminal 1 (default Bifrost engine)
  poetry run python gateway_server.py
  poetry run python demo/engine_swap_demo.py        # shows engine=bifrost

  # terminal 1 (swap to in-process DirectEngine - boto3 Bedrock)
  ENGINE=direct poetry run python gateway_server.py
  poetry run python demo/engine_swap_demo.py        # shows engine=direct

The component code below NEVER changes between runs.
"""
from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv

load_dotenv()
B = "http://localhost:8090"
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")


def main() -> None:
    engine = httpx.get(f"{B}/health/providers", timeout=10).json().get("engine")
    print(f"→ gateway engine currently: {engine}")
    r = httpx.post(f"{B}/v1/chat/completions", headers={"Authorization": f"Bearer {KEY}"},
                   json={"model": "claude-sonnet-4-5",
                         "messages": [{"role": "user", "content": "say ENGINE_SWAP_OK"}],
                         "max_tokens": 15}, timeout=60)
    d = r.json()
    print(f"← [{engine}] {d['choices'][0]['message']['content']} | usage={d.get('usage')}")
    print("✓ Identical OpenAI contract + governance regardless of engine.")


if __name__ == "__main__":
    main()
