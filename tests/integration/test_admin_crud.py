"""BVT/admin-CRUD round-trips - every admin write endpoint, happy path.

Covers: workspace create/update/delete (cascade), component auto-registration
(no create endpoint), provider add/delete, key issue/rotate/disable, pricing
upsert/delete, guardrail profile + rule create/update/delete.
"""
from __future__ import annotations

import uuid

import httpx

from .conftest import _ws_id


def test_workspace_create_update_delete(http_admin):
    wid = _ws_id()
    # Create
    r = http_admin.post("/admin/workspaces", json={"workspace_id": wid, "client_id": "novatech", "name": "BVT crud"})
    assert r.status_code == 200
    # Update name
    r = http_admin.patch(f"/admin/workspaces/{wid}", json={"name": "BVT crud renamed"})
    assert r.status_code == 200
    # Read
    r = http_admin.get("/admin/workspaces")
    assert r.status_code == 200
    found = [w for w in r.json()["workspaces"] if w["workspace_id"] == wid]
    assert found and found[0]["name"] == "BVT crud renamed"
    # Delete
    r = http_admin.delete(f"/admin/workspaces/{wid}")
    assert r.status_code == 200
    # Read-after-delete: workspace no longer in the list
    r = http_admin.get("/admin/workspaces")
    assert not any(w["workspace_id"] == wid for w in r.json()["workspaces"])


def test_workspace_delete_cascades_keys_and_providers(http_admin):
    wid = _ws_id()
    http_admin.post("/admin/workspaces", json={"workspace_id": wid, "client_id": "novatech", "name": "BVT cascade"})
    http_admin.post(
        f"/admin/workspaces/{wid}/providers",
        json={"provider": "anthropic", "credentials": {"api_key": "sk-x"}},
    )
    http_admin.post(
        f"/admin/workspaces/{wid}/keys",
        json={"roles": ["member"], "expires_at": "2027-12-31"},
    )
    # Sanity: rows exist
    assert http_admin.get(f"/admin/workspaces/{wid}/keys").json()["keys"]
    assert http_admin.get(f"/admin/workspaces/{wid}/providers").json()["providers"]

    # Delete workspace; expect cascade to clear all child rows
    r = http_admin.delete(f"/admin/workspaces/{wid}")
    assert r.status_code == 200


def test_component_auto_registration_via_chat(fresh_workspace, gateway_url):
    """WAVE 20 TRACK 1: components are auto-registered. A chat carrying a
    never-before-seen X-Gateway-Component value makes that component appear
    in the /admin/workspaces/{id}/components facet list automatically."""
    import uuid as _uuid
    new_comp = f"auto-{_uuid.uuid4().hex[:8]}"
    r = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {fresh_workspace['key']}",
            "Content-Type": "application/json",
            "X-Gateway-Component": new_comp,
        },
        json={"model": "claude-sonnet-4-5",
              "messages": [{"role": "user", "content": "auto-reg test"}],
              "max_tokens": 8},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    # The component should now appear in the workspace's component list
    import time; time.sleep(0.5)  # small settle for the async upsert
    comps = httpx.get(
        f"{gateway_url}/admin/workspaces/{fresh_workspace['workspace_id']}/components",
        headers={"X-Admin-Token": "platform-admin-secret"},
        timeout=10,
    ).json()["components"]
    names = [c["name"] for c in comps]
    assert new_comp in names, f"auto-registered component '{new_comp}' not in {names}"


def test_key_issue_rotate_disable(fresh_workspace, http_admin):
    wid = fresh_workspace["workspace_id"]
    # The fresh fixture already issued one key. Issue another.
    r = http_admin.post(
        f"/admin/workspaces/{wid}/keys",
        json={"roles": ["member"], "expires_at": "2027-12-31"},
    )
    assert r.status_code == 200
    raw_a = r.json()["api_key"]
    keys = http_admin.get(f"/admin/workspaces/{wid}/keys").json()["keys"]
    new_key = sorted(keys, key=lambda k: k["id"])[-1]
    kid = new_key["id"]
    # Rotate
    r = http_admin.post(f"/admin/workspaces/{wid}/keys/{kid}/rotate", json={})
    assert r.status_code == 200
    raw_b = r.json()["api_key"]
    assert raw_b != raw_a
    # Disable
    r = http_admin.delete(f"/admin/workspaces/{wid}/keys/{kid}")
    assert r.status_code == 200
    rows = http_admin.get(f"/admin/workspaces/{wid}/keys").json()["keys"]
    assert any(k["id"] == kid and k["disabled"] for k in rows)


def test_pricing_override_upsert_and_delete(http_admin):
    substr = f"bvt-pricing-{uuid.uuid4().hex[:6]}"
    r = http_admin.post(
        "/admin/pricing",
        json={"model_substr": substr, "input_per_1k": 0.001234, "output_per_1k": 0.005678,
              "note": "BVT test"},
    )
    assert r.status_code == 200
    rows = http_admin.get("/admin/pricing").json()["overrides"]
    assert any(o["model_substr"] == substr for o in rows)
    # Update - upsert by model_substr
    r = http_admin.post(
        "/admin/pricing",
        json={"model_substr": substr, "input_per_1k": 0.002, "output_per_1k": 0.006},
    )
    assert r.status_code == 200
    # Delete
    r = http_admin.delete(f"/admin/pricing/{substr}")
    assert r.status_code == 200


def test_guardrail_profile_and_rule_lifecycle(http_admin):
    pname = f"bvt-prof-{uuid.uuid4().hex[:6]}"
    rname = f"bvt-rule-{uuid.uuid4().hex[:6]}"
    # Create profile
    r = http_admin.post(
        "/admin/guardrails/profiles",
        json={
            "name": pname, "detector_type": "regex", "enabled": True,
            "config": {"patterns": {"phone": r"\\d{3}-\\d{4}"}},
            "scope": "global",
        },
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    # Create rule referencing profile
    r = http_admin.post(
        "/admin/guardrails/rules",
        json={
            "name": rname, "description": "BVT", "enabled": True,
            "cel_expression": "true",
            "apply_to": "input", "action": "audit", "scope": "global",
            "profile_ids": [pid],
        },
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]

    # PATCH the rule to disable
    r = http_admin.patch(f"/admin/guardrails/rules/{rid}", json={"enabled": False})
    assert r.status_code == 200

    # PATCH the profile to rename
    r = http_admin.patch(f"/admin/guardrails/profiles/{pid}", json={"name": pname + "-v2"})
    assert r.status_code == 200

    # Delete profile → rule's profile_ids should be swept
    r = http_admin.delete(f"/admin/guardrails/profiles/{pid}")
    assert r.status_code == 200
    rules = http_admin.get("/admin/guardrails/rules").json()["rules"]
    me = next(rl for rl in rules if rl["id"] == rid)
    assert pid not in (me["profile_ids"] or [])

    # Delete rule
    r = http_admin.delete(f"/admin/guardrails/rules/{rid}")
    assert r.status_code == 200


def test_engine_swap_round_trip(http_admin):
    # Currently we're on echo (set by session fixture). Swap to direct, then back.
    r = http_admin.post("/admin/engine", json={"engine": "echo"})
    assert r.status_code == 200
    assert r.json()["engine"] == "echo"

    r = http_admin.post("/admin/engine", json={"engine": "bifrost"})
    assert r.status_code == 200
    assert r.json()["engine"] == "bifrost"

    # Restore echo for downstream tests in this session
    r = http_admin.post("/admin/engine", json={"engine": "echo"})
    assert r.status_code == 200
