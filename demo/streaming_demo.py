"""Streaming demo - OpenAI SSE chunks streamed through the gateway."""
from __future__ import annotations
import json, os
import httpx
from dotenv import load_dotenv
load_dotenv()
GW = os.getenv("AGNOS_GATEWAY_URL", "http://localhost:8090/v1")
KEY = os.getenv("WS_KEY_SECONDARY", "gw-key-secondary-001")

def main():
    print(f"→ streaming via {GW}")
    with httpx.stream("POST", f"{GW}/chat/completions",
                      headers={"Authorization": f"Bearer {KEY}"},
                      json={"model":"claude-sonnet-4-5",
                            "messages":[{"role":"user","content":"Count one to five."}],
                            "max_tokens":40,"stream":True}, timeout=60) as r:
        for line in r.iter_lines():
            if not line or not line.startswith("data: "): continue
            d = line[6:].strip()
            if d == "[DONE]": print("\n[done]"); break
            try:
                delta = json.loads(d)["choices"][0]["delta"]
                if "content" in delta: print(delta["content"], end="", flush=True)
            except Exception: pass

if __name__ == "__main__":
    main()
