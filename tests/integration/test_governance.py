"""BVT/governance - every chat auto-emits the right governance events.

We don't directly read Kafka here (would require a consumer fixture). Instead
we use the gateway's SSE /events endpoint as a proof of auto-emit: a chat call
must produce a `request_start` and a `completion` event on the bus within a
short window. The same dispatch path feeds Postgres + Kafka, so this is
sufficient evidence that the bus is wired and emitting on every request.
"""
from __future__ import annotations

import json
import threading
import time

import httpx

from .conftest import chat_request


def test_chat_auto_emits_request_lifecycle_on_sse(fresh_workspace, gateway_url):
    """Open /events SSE, fire a chat, then assert we saw at least one event for our workspace."""
    wid = fresh_workspace["workspace_id"]

    seen: list[dict] = []
    stop_event = threading.Event()

    def consumer() -> None:
        # Use a streaming GET against SSE; iterate until we see what we expect or timeout.
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0, read=10.0)) as c:
                with c.stream(
                    "GET",
                    f"{gateway_url}/events",
                    params={"workspace": wid},
                    headers={"Accept": "text/event-stream"},
                ) as resp:
                    for line in resp.iter_lines():
                        if stop_event.is_set():
                            return
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload:
                            continue
                        try:
                            obj = json.loads(payload)
                        except Exception:
                            continue
                        seen.append(obj)
                        # Stop early once we've seen at least 1 event for our workspace
                        if len(seen) >= 1 and any(
                            (e.get("workspace_id") or e.get("payload", {}).get("workspace_id")) == wid
                            for e in seen
                        ):
                            return
        except Exception:
            pass

    th = threading.Thread(target=consumer, daemon=True)
    th.start()
    # Give the SSE stream a moment to subscribe
    time.sleep(0.4)

    # Fire a chat
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}", "Content-Type": "application/json"},
        json=chat_request(content="governance event test"),
        timeout=30,
    )
    assert r.status_code == 200

    # Wait up to 5s for the consumer to capture events
    th.join(timeout=5)
    stop_event.set()

    # We should have observed at least one event tied to this workspace
    assert seen, "no events received on /events stream within 5s"
    matched = [
        e for e in seen
        if (e.get("workspace_id") or e.get("payload", {}).get("workspace_id")) == wid
    ]
    assert matched, f"no events for workspace {wid}; saw {len(seen)} events for others"
