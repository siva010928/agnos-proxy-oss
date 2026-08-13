"""Shared fixtures for BVT integration tests.

Strategy: reuse the running stack (Postgres + Redis + Kafka + Bifrost) and the
running gateway, swap the BackendEngine to `echo` for the duration of the
session, then swap back. Tests use real HTTP, real auth, real DB persistence,
real governance bus - only the LLM upstream is mocked.

Test workspaces are prefixed `wsbvt-` and a session-scoped teardown deletes
every workspace whose ID starts with that prefix (relies on the cascade-
delete added in WAVE 16-UX-2).

If the gateway is unreachable, every test in this module is auto-skipped with
a clear message - never silently "passes".
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest

GW = os.getenv("GATEWAY_URL", "http://localhost:8090")
ADMIN_TOKEN = os.getenv("PLATFORM_ADMIN_TOKEN", "platform-admin-secret")

# Use a unique prefix per pytest session so parallel runs don't collide
TEST_PREFIX = f"wsbvt-{uuid.uuid4().hex[:8]}"


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": ADMIN_TOKEN, "Content-Type": "application/json"}


# Mark every BVT test as integration so `-m integration` selects them. Skip
# files that are already marked `live` (the capped real-provider smoke) so
# `-m integration` doesn't accidentally hit real providers.
def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    for item in items:
        path = str(item.path)
        if "tests/integration" not in path:
            continue
        if "test_live_smoke" in path:
            continue
        item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def gateway_url() -> str:
    return GW


@pytest.fixture(scope="session", autouse=True)
def _require_gateway_and_swap_engine():
    """Verify gateway is healthy, swap to echo engine for the session, then restore."""
    try:
        r = httpx.get(f"{GW}/health", timeout=3)
        if r.status_code != 200:
            pytest.skip(f"gateway unhealthy: {r.status_code}")
    except Exception as exc:
        pytest.skip(f"gateway unreachable at {GW}: {exc}")

    # Swap to echo for the session
    swap = httpx.post(
        f"{GW}/admin/engine",
        headers=_admin_headers(),
        json={"engine": "echo"},
        timeout=30,
    )
    if swap.status_code != 200 or swap.json().get("engine") != "echo":
        pytest.skip(f"could not swap to echo engine: {swap.status_code} {swap.text[:200]}")

    yield

    # Restore bifrost (ignore failures on teardown)
    try:
        httpx.post(
            f"{GW}/admin/engine",
            headers=_admin_headers(),
            json={"engine": "bifrost"},
            timeout=30,
        )
    except Exception:
        pass

    # Cleanup: delete every workspace created by this session (cascades to
    # components/keys/providers/guardrails per WAVE 16-UX-2).
    try:
        r = httpx.get(f"{GW}/admin/workspaces", headers=_admin_headers(), timeout=5)
        for w in r.json().get("workspaces", []):
            wid = w.get("workspace_id", "")
            if wid.startswith(TEST_PREFIX):
                try:
                    httpx.delete(
                        f"{GW}/admin/workspaces/{wid}",
                        headers=_admin_headers(),
                        timeout=30,
                    )
                except Exception:
                    pass
    except Exception:
        pass


@pytest.fixture()
def http_admin() -> httpx.Client:
    """A fresh httpx.Client carrying the admin token. Use as a context."""
    with httpx.Client(base_url=GW, headers=_admin_headers(), timeout=30) as c:
        yield c


@pytest.fixture()
def http_anonymous() -> httpx.Client:
    """A fresh httpx.Client with no auth (so we can test 401)."""
    with httpx.Client(base_url=GW, timeout=30) as c:
        yield c


def _ws_id() -> str:
    """Mint a unique test-workspace id."""
    return f"{TEST_PREFIX}-{uuid.uuid4().hex[:6]}"


@pytest.fixture()
def fresh_workspace(http_admin: httpx.Client):
    """
    Create a fresh workspace + bedrock provider + chat alias + first key.
    Yields a dict with: workspace_id, key (plaintext), alias, model_id.
    The session teardown handles cleanup, but tests can also explicitly DELETE.
    """
    wid = _ws_id()
    alias = "claude-sonnet-4-5"
    model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

    # 1) Create workspace skeleton (attached to the seeded NovaTech client; WAVE 19
    # tenancy requires every workspace to belong to a Client). This client is
    # set up by the cold-start seed (`novatech`).
    r = http_admin.post(
        "/admin/workspaces",
        json={
            "workspace_id": wid,
            "client_id": "novatech",
            "name": f"BVT {wid}",
            "chat_models": {alias: [{"provider": "bedrock", "model_id": model_id, "weight": 1}]},
            "default_chat_alias": alias,
            "guardrails": {"mode": "block"},
            "quotas": {alias: {"rpm": 600, "tpm": 1_000_000}},
            "budgets": {"workspace_usd": 1000, "user_usd": 100},
        },
    )
    assert r.status_code == 200, f"create workspace failed: {r.text}"

    # 2) Attach bedrock provider (creds are dummy - Test isn't called here, and
    # the EchoEngine doesn't read them; the server only needs a row to satisfy
    # resolvability on later patches).
    r = http_admin.post(
        f"/admin/workspaces/{wid}/providers",
        json={
            "provider": "bedrock",
            "credentials": {"access_key": "AKIATEST", "secret_key": "secret"},
            "config": {"region": "us-east-1"},
        },
    )
    assert r.status_code == 200, f"add provider failed: {r.text}"

    # 3) Issue first key (with future expiry - server now rejects past)
    r = http_admin.post(
        f"/admin/workspaces/{wid}/keys",
        json={"roles": ["member"], "expires_at": "2027-12-31"},
    )
    assert r.status_code == 200, f"issue key failed: {r.text}"
    key = r.json()["api_key"]

    yield {"workspace_id": wid, "key": key, "alias": alias, "model_id": model_id}

    # explicit cleanup attempt (idempotent; session teardown is the safety net)
    try:
        http_admin.delete(f"/admin/workspaces/{wid}")
    except Exception:
        pass


def chat_request(model: str = "claude-sonnet-4-5", content: str = "hello", **extra) -> dict:
    """Build a minimal valid OpenAI chat-completions body."""
    body = {"model": model, "messages": [{"role": "user", "content": content}]}
    body.update(extra)
    return body
