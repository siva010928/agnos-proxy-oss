"""BVT/admin-CRUD negatives - every validator from gateway/core/admin_validation.py
exercised through the live HTTP path. Each test asserts:
  * HTTP 422
  * `detail[]` is a list with at least one {loc, msg} object
  * The msg is human-readable (toast-ready)
"""
from __future__ import annotations

import uuid

from .conftest import _ws_id


def _detail(r) -> list[dict]:
    body = r.json()
    detail = body.get("detail")
    assert isinstance(detail, list) and detail, f"expected list detail, got {body}"
    for d in detail:
        assert "loc" in d and "msg" in d, f"malformed detail: {d}"
    return detail


# ─────────────────────────── workspace ───────────────────────────


def test_workspace_id_must_be_slug(http_admin):
    r = http_admin.post(
        "/admin/workspaces",
        json={"client_id": "novatech", "workspace_id": "Has Spaces", "name": "x"},
    )
    assert r.status_code == 422
    detail = _detail(r)
    assert any("workspace_id" in d["loc"] for d in detail)


def test_workspace_chat_models_rejects_unknown_provider(http_admin):
    wid = _ws_id()
    r = http_admin.post(
        "/admin/workspaces",
        json={
            "client_id": "novatech",
            "workspace_id": wid,
            "chat_models": {"alias": [{"provider": "aws_lol", "model_id": "x"}]},
        },
    )
    assert r.status_code == 422
    msgs = " ".join(d["msg"] for d in _detail(r))
    assert "provider must be one of" in msgs


def test_workspace_default_alias_must_be_in_chat_models(http_admin):
    wid = _ws_id()
    r = http_admin.post(
        "/admin/workspaces",
        json={
            "client_id": "novatech",
            "workspace_id": wid,
            "chat_models": {"alpha": [{"provider": "bedrock", "model_id": "us.x"}]},
            "default_chat_alias": "beta",   # not in chat_models
        },
    )
    assert r.status_code == 422
    assert any("default_chat_alias" in d["loc"] for d in _detail(r))


def test_workspace_patch_rejects_unconfigured_provider(http_admin):
    wid = _ws_id()
    http_admin.post("/admin/workspaces", json={
        "workspace_id": wid, "client_id": "novatech", "name": "x"})
    # PATCH a chat_models alias pointing at openai BUT the workspace has no openai provider
    r = http_admin.patch(
        f"/admin/workspaces/{wid}",
        json={"chat_models": {"alpha": [{"provider": "openai", "model_id": "gpt-4o"}]}},
    )
    assert r.status_code == 422, r.text
    msgs = " ".join(d["msg"] for d in _detail(r))
    assert "not configured for this workspace" in msgs

    http_admin.delete(f"/admin/workspaces/{wid}")


# ─────────────────────────── component ───────────────────────────


def test_component_create_endpoint_removed(http_admin, fresh_workspace):
    """WAVE 20 TRACK 1: POST to /admin/workspaces/{id}/components must 404 or
    405 - the create endpoint was removed. Components are auto-registered."""
    wid = fresh_workspace["workspace_id"]
    r = http_admin.post(
        f"/admin/workspaces/{wid}/components",
        json={"name": "should-not-create"},
    )
    # FastAPI returns 405 Method Not Allowed when only GET remains on the path
    assert r.status_code in (404, 405, 422), r.text


# ─────────────────────────── provider ───────────────────────────


def test_provider_rejects_unknown_provider(http_admin, fresh_workspace):
    r = http_admin.post(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}/providers",
        json={"provider": "lol_cloud", "credentials": {"api_key": "x"}},
    )
    assert r.status_code == 422
    msgs = " ".join(d["msg"] for d in _detail(r))
    assert "provider must be one of" in msgs


def test_provider_anthropic_requires_api_key(http_admin, fresh_workspace):
    r = http_admin.post(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}/providers",
        json={"provider": "anthropic", "credentials": {}},
    )
    assert r.status_code == 422
    assert any("api_key" in d["loc"] for d in _detail(r))


def test_provider_bedrock_validates_region_format(http_admin, fresh_workspace):
    r = http_admin.post(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}/providers",
        json={"provider": "bedrock",
              "credentials": {"access_key": "AKIA", "secret_key": "secret"},
              "config": {"region": "definitely-not-a-region"}},
    )
    assert r.status_code == 422
    assert any("region" in d["loc"] for d in _detail(r))


def test_provider_azure_validates_endpoint_url(http_admin, fresh_workspace):
    r = http_admin.post(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}/providers",
        json={"provider": "azure",
              "credentials": {"api_key": "azure-key"},
              "config": {"endpoint": "not a url at all"}},
    )
    assert r.status_code == 422
    assert any("endpoint" in d["loc"] for d in _detail(r))


# ─────────────────────────── api keys ───────────────────────────


def test_key_expires_at_unparseable_rejected(http_admin, fresh_workspace):
    r = http_admin.post(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}/keys",
        json={"roles": ["member"], "expires_at": "never"},
    )
    assert r.status_code == 422, r.text
    msgs = " ".join(d["msg"] for d in _detail(r))
    assert "ISO-8601" in msgs


def test_key_expires_at_in_past_rejected(http_admin, fresh_workspace):
    r = http_admin.post(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}/keys",
        json={"roles": ["member"], "expires_at": "2020-01-01"},
    )
    assert r.status_code == 422
    assert any("expires_at" in d["loc"] for d in _detail(r))


def test_key_empty_roles_rejected(http_admin, fresh_workspace):
    r = http_admin.post(
        f"/admin/workspaces/{fresh_workspace['workspace_id']}/keys",
        json={"roles": []},
    )
    assert r.status_code == 422
    assert any("roles" in d["loc"] for d in _detail(r))


# ─────────────────────────── pricing ───────────────────────────


def test_pricing_substr_empty_rejected(http_admin):
    r = http_admin.post(
        "/admin/pricing",
        json={"model_substr": "", "input_per_1k": 1, "output_per_1k": 1},
    )
    assert r.status_code == 422
    assert any("model_substr" in d["loc"] for d in _detail(r))


def test_pricing_substr_too_short_rejected(http_admin):
    r = http_admin.post(
        "/admin/pricing",
        json={"model_substr": "ab", "input_per_1k": 1, "output_per_1k": 1},
    )
    assert r.status_code == 422
    msgs = " ".join(d["msg"] for d in _detail(r))
    assert "at least 3 characters" in msgs


def test_pricing_zero_zero_rejected(http_admin):
    r = http_admin.post(
        "/admin/pricing",
        json={"model_substr": "bvt-zero-test", "input_per_1k": 0, "output_per_1k": 0},
    )
    assert r.status_code == 422
    msgs = " ".join(d["msg"] for d in _detail(r))
    assert "no-op" in msgs


# ─────────────────────────── guardrails ───────────────────────────


def test_rule_invalid_cel_rejected(http_admin):
    r = http_admin.post(
        "/admin/guardrails/rules",
        json={"name": f"bvt-bad-cel-{uuid.uuid4().hex[:6]}",
              "cel_expression": "((not valid"},
    )
    assert r.status_code == 422
    msgs = " ".join(d["msg"] for d in _detail(r))
    assert "CEL syntax error" in msgs


def test_rule_invalid_apply_to_rejected(http_admin):
    r = http_admin.post(
        "/admin/guardrails/rules",
        json={"name": f"bvt-bad-apply-{uuid.uuid4().hex[:6]}", "apply_to": "what"},
    )
    assert r.status_code == 422
    assert any("apply_to" in d["loc"] for d in _detail(r))


def test_rule_invalid_action_rejected(http_admin):
    r = http_admin.post(
        "/admin/guardrails/rules",
        json={"name": f"bvt-bad-act-{uuid.uuid4().hex[:6]}", "action": "ignore"},
    )
    assert r.status_code == 422
    assert any("action" in d["loc"] for d in _detail(r))


def test_rule_sampling_rate_out_of_range(http_admin):
    r = http_admin.post(
        "/admin/guardrails/rules",
        json={"name": f"bvt-bad-sr-{uuid.uuid4().hex[:6]}", "sampling_rate": 5.0},
    )
    assert r.status_code == 422
    assert any("sampling_rate" in d["loc"] for d in _detail(r))


def test_profile_invalid_detector_type_rejected(http_admin):
    r = http_admin.post(
        "/admin/guardrails/profiles",
        json={"name": "bvt-bad-det", "detector_type": "regxe"},
    )
    assert r.status_code == 422
    assert any("detector_type" in d["loc"] for d in _detail(r))


def test_profile_keyword_requires_keywords(http_admin):
    r = http_admin.post(
        "/admin/guardrails/profiles",
        json={"name": "bvt-bad-kw", "detector_type": "keyword", "config": {}},
    )
    assert r.status_code == 422
    msgs = " ".join(d["msg"] for d in _detail(r))
    assert "keyword profile requires" in msgs


# ─────────────────────────── engine ───────────────────────────


def test_engine_invalid_value_rejected(http_admin):
    r = http_admin.post("/admin/engine", json={"engine": "fake"})
    # admin_crud raises 400 for the engine swap; admin_validation has the same
    # check. Either is acceptable as long as the response is informative.
    assert r.status_code in (400, 422), r.text
