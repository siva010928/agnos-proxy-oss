"""BVT/guardrails - input block + redact + audit + per-request override.

Each test creates a workspace-scoped guardrail rule, fires a chat that should
match it, asserts the gateway response (422 vs 200+header vs 200+event), and
cleans up.
"""
from __future__ import annotations

import uuid

import httpx

from .conftest import chat_request


def _create_rule(http_admin, *, action: str, profile_name: str, regex: str,
                  workspace_id: str | None = None):
    """Create a stored profile + rule scoped global by default. Returns (rid, pid)."""
    r = http_admin.post(
        "/admin/guardrails/profiles",
        json={
            "name": profile_name,
            "detector_type": "regex",
            "enabled": True,
            "config": {"patterns": {"bvt": regex}},
            "scope": "workspace" if workspace_id else "global",
            "workspace_id": workspace_id,
        },
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    rule_name = f"bvt-rule-{uuid.uuid4().hex[:6]}"
    r = http_admin.post(
        "/admin/guardrails/rules",
        json={
            "name": rule_name,
            "enabled": True,
            "cel_expression": "true",
            "apply_to": "input", "action": action,
            "profile_ids": [pid],
            "scope": "workspace" if workspace_id else "global",
            "client_id": "novatech",
            "workspace_id": workspace_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["id"], pid


def _delete_rule(http_admin, rid: int, pid: int):
    try:
        http_admin.delete(f"/admin/guardrails/rules/{rid}")
        http_admin.delete(f"/admin/guardrails/profiles/{pid}")
    except Exception:
        pass


def test_block_action_returns_422_with_guardrail_violation(fresh_workspace, http_admin, gateway_url):
    pname = f"bvt-block-{uuid.uuid4().hex[:6]}"
    rid, pid = _create_rule(
        http_admin,
        action="block", profile_name=pname,
        regex=r"BVT_BLOCK_KEYWORD",
        workspace_id=fresh_workspace["workspace_id"],
    )
    try:
        r = httpx.post(
            f"{gateway_url}/v1/chat/completions",
            headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}",
                     "Content-Type": "application/json"},
            json=chat_request(content="please trigger BVT_BLOCK_KEYWORD now"),
            timeout=30,
        )
        assert r.status_code == 422, r.text
        body = r.json()
        # Backend returns OpenAI-clean error shape, not extra_fields
        assert "error" in body or "detail" in body
        # No leak keys
        assert "extra_fields" not in body
    finally:
        _delete_rule(http_admin, rid, pid)


def test_redact_action_returns_200_with_header(fresh_workspace, http_admin, gateway_url):
    pname = f"bvt-redact-{uuid.uuid4().hex[:6]}"
    rid, pid = _create_rule(
        http_admin,
        action="redact", profile_name=pname,
        regex=r"BVT_REDACT_KEYWORD",
        workspace_id=fresh_workspace["workspace_id"],
    )
    try:
        r = httpx.post(
            f"{gateway_url}/v1/chat/completions",
            headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}",
                     "Content-Type": "application/json"},
            json=chat_request(content="redact BVT_REDACT_KEYWORD please"),
            timeout=30,
        )
        # Redact succeeds and surfaces a header
        assert r.status_code == 200, r.text
        # Case-insensitive header lookup
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        assert hdrs.get("x-gateway-guardrail") == "redacted", f"missing redact header: {hdrs}"
    finally:
        _delete_rule(http_admin, rid, pid)


def test_audit_action_returns_200_unmodified(fresh_workspace, http_admin, gateway_url):
    pname = f"bvt-audit-{uuid.uuid4().hex[:6]}"
    rid, pid = _create_rule(
        http_admin,
        action="audit", profile_name=pname,
        regex=r"BVT_AUDIT_KEYWORD",
        workspace_id=fresh_workspace["workspace_id"],
    )
    try:
        r = httpx.post(
            f"{gateway_url}/v1/chat/completions",
            headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}",
                     "Content-Type": "application/json"},
            json=chat_request(content="audit-only BVT_AUDIT_KEYWORD please"),
            timeout=30,
        )
        assert r.status_code == 200
        # Audit doesn't add the redact header (that's redact-specific)
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        assert hdrs.get("x-gateway-guardrail") != "redacted"
    finally:
        _delete_rule(http_admin, rid, pid)


def test_per_request_guardrail_mode_override(fresh_workspace, http_admin, gateway_url):
    """The per-request X-Gateway-Guardrail-Mode header overrides INLINE rules.

    Stored DB rules carry their `action` baked in; inline rules built from a
    workspace's guardrails dict take the per-request mode. We patch the
    workspace to add an inline keyword-block, then verify the override flips
    block→audit.
    """
    wid = fresh_workspace["workspace_id"]
    # Inline workspace-level guardrail: block if message contains the keyword
    http_admin.patch(
        f"/admin/workspaces/{wid}",
        json={
            "guardrails": {
                "mode": "block",
                "keywords": ["BVT_OVERRIDE_KEYWORD"],
            }
        },
    )

    # Without override → blocks
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}",
                 "Content-Type": "application/json"},
        json=chat_request(content="trigger BVT_OVERRIDE_KEYWORD"),
        timeout=30,
    )
    assert r.status_code == 422

    # With override → 200 (mode flipped from block → audit)
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={
            "X-Gateway-Component": "document-processing", "Authorization": f"Bearer {fresh_workspace['key']}",
            "Content-Type": "application/json",
            "X-Gateway-Guardrail-Mode": "audit",
        },
        json=chat_request(content="trigger BVT_OVERRIDE_KEYWORD again"),
        timeout=30,
    )
    assert r.status_code == 200, r.text
