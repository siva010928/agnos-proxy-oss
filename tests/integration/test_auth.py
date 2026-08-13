"""BVT/auth - API key auth against a real gateway.

Covers: valid → 200; missing key → 401; bad key → 401; disabled key → 401;
expired key (past) is rejected at write-time (validator) so we can't even
create one - proven by negative test in test_admin_crud_negatives.py.
"""
from __future__ import annotations

import httpx

from .conftest import chat_request


def test_valid_apikey_returns_200(fresh_workspace, gateway_url):
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={
            "X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}",
            "Content-Type": "application/json",
        },
        json=chat_request(),
        timeout=30,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion"
    # EchoEngine prefixes with "[echo:bedrock]" so we can also assert routing
    assert "[echo:bedrock]" in body["choices"][0]["message"]["content"]
    # Anti-corruption: no leak keys
    assert "extra_fields" not in body
    assert "bifrost_config" not in body


def test_missing_authorization_header_returns_401(http_anonymous, gateway_url):
    r = http_anonymous.post(
        "/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        json=chat_request(),
    )
    assert r.status_code == 401, r.text


def test_bogus_apikey_returns_401(http_anonymous, gateway_url):
    r = http_anonymous.post(
        "/v1/chat/completions",
        headers={
            "X-Gateway-Component": "document-processing", "Authorization": "Bearer gw-not-a-real-key-xxx",
            "Content-Type": "application/json",
        },
        json=chat_request(),
    )
    assert r.status_code == 401, r.text


def test_disabled_key_returns_401(fresh_workspace, http_admin, gateway_url):
    # Find the issued key id, disable it, then try to use it
    keys = http_admin.get(f"/admin/workspaces/{fresh_workspace['workspace_id']}/keys").json()["keys"]
    assert len(keys) == 1
    kid = keys[0]["id"]
    r = http_admin.delete(f"/admin/workspaces/{fresh_workspace['workspace_id']}/keys/{kid}")
    assert r.status_code == 200

    # Now the same plaintext should fail
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={
            "X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}",
            "Content-Type": "application/json",
        },
        json=chat_request(),
        timeout=30,
    )
    assert r.status_code == 401, r.text


def test_admin_token_is_required_for_admin_routes(http_anonymous):
    # Use an admin_crud route (which carries `Depends(require_admin)`); the
    # analytics /admin/workspaces is intentionally public for the dashboard.
    r = http_anonymous.post("/admin/workspaces", json={"client_id": "novatech", "workspace_id": "x", "name": "x"})
    assert r.status_code == 403, r.text


def test_correlation_id_header_echoed(fresh_workspace, gateway_url):
    """Every response must carry X-Gateway-Correlation-Id (governance tracing)."""
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={
            "X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}",
            "Content-Type": "application/json",
        },
        json=chat_request(),
        timeout=30,
    )
    assert r.status_code == 200
    cid = r.headers.get("X-Gateway-Correlation-Id") or r.headers.get("x-gateway-correlation-id")
    assert cid, "correlation id header missing"
    assert cid.startswith("req-") or len(cid) > 5
