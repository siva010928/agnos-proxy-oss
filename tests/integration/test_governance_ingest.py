"""BVT/governance ingest - POST /governance/events accepts external producer
events and routes them through the same bus as auto-emitted events.

Covers: happy path (admin token + workspace key), validation negatives, and
cross-tenant rejection (workspace key cannot emit for a different workspace).
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import httpx


def test_ingest_happy_path_admin_token(http_admin, gateway_url):
    cid = f"req-bvt-ingest-{uuid.uuid4().hex[:8]}"
    r = http_admin.post(
        "/governance/events",
        json={
            "event_kind": "completion",
            "correlation_id": cid,
            "payload": {
                "workspace_id": "ws-novatech-payments",
            "client_id": "novatech",
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "input_tokens": 120,
                "output_tokens": 80,
                "cost_usd": 0.005,
                "latency_ms": 380,
                "component": "bvt-external-component",
            },
        },
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["correlation_id"] == cid
    assert body["event_kind"] == "completion"


def test_ingest_workspace_key_can_emit_for_own_workspace(fresh_workspace, gateway_url):
    cid = f"req-bvt-ingest-{uuid.uuid4().hex[:8]}"
    r = httpx.post(
        f"{gateway_url}/governance/events",
        headers={
            "Authorization": f"Bearer {fresh_workspace['key']}",
            "Content-Type": "application/json",
        },
        json={
            "event_kind": "cache_hit",
            "correlation_id": cid,
            "payload": {
                "workspace_id": fresh_workspace["workspace_id"],
                "provider": "bedrock",
                "model": "claude-sonnet-4-5",
                "input_tokens": 100,
                "output_tokens": 0,
                "cost_saved_usd": 0.001,
            },
        },
        timeout=30,
    )
    assert r.status_code == 202, r.text


def test_ingest_workspace_key_rejected_for_other_workspace(fresh_workspace, gateway_url):
    r = httpx.post(
        f"{gateway_url}/governance/events",
        headers={
            "Authorization": f"Bearer {fresh_workspace['key']}",
            "Content-Type": "application/json",
        },
        json={
            "event_kind": "completion",
            "payload": {
                "workspace_id": "ws-novatech-platform",   # NOT fresh_workspace's id
                "provider": "bedrock",
                "model": "x",
            },
        },
        timeout=30,
    )
    assert r.status_code == 403, r.text


def test_ingest_no_auth_returns_401(gateway_url):
    r = httpx.post(
        f"{gateway_url}/governance/events",
        headers={"Content-Type": "application/json"},
        json={
            "event_kind": "completion",
            "payload": {"workspace_id": "ws-x", "provider": "bedrock", "model": "y"},
        },
        timeout=30,
    )
    assert r.status_code == 401, r.text


def test_ingest_bad_event_kind_returns_422(http_admin):
    r = http_admin.post(
        "/governance/events",
        json={"event_kind": "explosion", "payload": {"workspace_id": "ws-x"}},
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("event_kind" in d["loc"] for d in detail)


def test_ingest_missing_required_payload_field_returns_422(http_admin):
    r = http_admin.post(
        "/governance/events",
        json={
            "event_kind": "completion",
            "payload": {"workspace_id": "ws-x"},   # missing provider + model
        },
    )
    assert r.status_code == 422


def test_ingest_event_lands_on_sse_bus(fresh_workspace, gateway_url):
    """An ingested event must be observed on /events SSE stream within a
    short window (proves the bus dispatches ingest the same as auto-emit)."""
    wid = fresh_workspace["workspace_id"]
    cid = f"req-bvt-sse-{uuid.uuid4().hex[:8]}"

    seen: list[dict] = []
    stop = threading.Event()

    def consumer():
        try:
            with httpx.Client(timeout=httpx.Timeout(8.0, read=8.0)) as c:
                with c.stream("GET", f"{gateway_url}/events",
                              params={"workspace": wid},
                              headers={"Accept": "text/event-stream"}) as resp:
                    for line in resp.iter_lines():
                        if stop.is_set():
                            return
                        if not line or not line.startswith("data:"):
                            continue
                        try:
                            obj = json.loads(line[5:].strip())
                            seen.append(obj)
                            # SSE flattens the dataclass: the correlation_id
                            # lands as `request_id` (the field name in the
                            # event class). Match either name.
                            if (obj.get("request_id") == cid
                                    or obj.get("correlation_id") == cid):
                                return
                        except Exception:
                            continue
        except Exception:
            pass

    th = threading.Thread(target=consumer, daemon=True)
    th.start()
    time.sleep(0.4)

    r = httpx.post(
        f"{gateway_url}/governance/events",
        headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json"},
        json={
            "event_kind": "completion",
            "correlation_id": cid,
            "payload": {
                "workspace_id": wid,
                "provider": "bedrock",
                "model": "claude-sonnet-4-5",
                "input_tokens": 50, "output_tokens": 20, "cost_usd": 0.001,
            },
        },
        timeout=30,
    )
    assert r.status_code == 202

    th.join(timeout=4)
    stop.set()

    assert seen, "no SSE events received"
    matched = [
        e for e in seen
        if e.get("request_id") == cid or e.get("correlation_id") == cid
    ]
    assert matched, (
        f"ingested event with correlation_id={cid} not seen on SSE within 4s; "
        f"received {len(seen)} other events"
    )
