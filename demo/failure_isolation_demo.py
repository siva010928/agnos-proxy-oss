"""Failure-isolation demo - pause the Postgres governance store mid-traffic and
show that LLM requests KEEP SUCCEEDING (governance is best-effort, off the hot
path via bounded-queue observers).

Run with the gateway + stack up.
"""
from __future__ import annotations

import os
import subprocess
import time

import httpx
from dotenv import load_dotenv

load_dotenv()
B = "http://localhost:8090"
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")


def call() -> int:
    try:
        r = httpx.post(f"{B}/v1/chat/completions", headers={"Authorization": f"Bearer {KEY}"},
                       json={"model": "claude-sonnet-4-5",
                             "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}, timeout=60)
        return r.status_code
    except Exception as e:
        return -1


def main() -> None:
    print("→ baseline call:", call())
    print("→ PAUSING Postgres governance store (docker pause agnos-proxy-gateway-pg)…")
    subprocess.run(["docker", "pause", "agnos-proxy-gateway-pg"], capture_output=True)
    time.sleep(1)
    try:
        codes = [call() for _ in range(3)]
        print(f"→ calls while Postgres is paused: {codes}")
        print("  (200s prove LLM serving survives a governance-store outage.)")
    finally:
        subprocess.run(["docker", "unpause", "agnos-proxy-gateway-pg"], capture_output=True)
        print("→ Postgres unpaused.")
    time.sleep(1)
    print("→ post-recovery call:", call())


if __name__ == "__main__":
    main()
