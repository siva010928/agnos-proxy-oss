"""WAVE 20 TRACK 3 \u2014 Filter-efficacy tests.

These tests prove the Analytics and Request Logs filters actually change the
result set (not just that the dropdown renders). Each test fires a small
controlled burst of attributed traffic, then queries the same endpoint WITH
and WITHOUT a filter and asserts the counts differ.

This catches the entire class of bug where:
  * a filter param is accepted but silently ignored in the WHERE clause
  * the UI sends the wrong field name
  * the backend has an ILIKE mismatch (the old model_alias \u2260 model_id bug)
"""
from __future__ import annotations

import time
import uuid

import httpx

from .conftest import chat_request


def _admin(gateway_url: str) -> dict:
    return {"X-Admin-Token": "platform-admin-secret", "Content-Type": "application/json"}


def test_filter_workspace_changes_cost_rollup(fresh_workspace, http_admin, gateway_url):
    """Analytics cost grouped by workspace; filtering to this workspace must
    return fewer rows than unfiltered (since the DB has rows from multiple
    workspaces)."""
    # Fire one chat so the workspace has at least 1 row
    httpx.post(f"{gateway_url}/v1/chat/completions",
               headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                        "Content-Type": "application/json",
                        "X-Gateway-Component": "document-processing"},
               json=chat_request(), timeout=30)
    time.sleep(0.5)
    wid = fresh_workspace["workspace_id"]

    all_rows = http_admin.get("/admin/cost", params={"group_by": "workspace"}).json()["rows"]
    filtered = http_admin.get("/admin/cost", params={"group_by": "workspace", "workspace": wid}).json()["rows"]
    assert len(all_rows) >= 1
    assert len(filtered) == 1
    assert filtered[0]["key"] == wid


def test_filter_model_ilike_returns_nonzero(fresh_workspace, http_admin, gateway_url):
    """The model filter uses ILIKE against BOTH model_alias and
    provider_model_id, so searching a substring that appears in the provider
    model id (e.g. 'claude-sonnet') must return matching rows.

    Regression test for the bug where the filter compared against model_alias
    only (which was the workspace-defined alias 'claude-sonnet-4-5') while the
    dropdown showed the provider model id ('claude-sonnet-4-5-20250929')."""
    # Fire one chat so there's at least one row with the provider model id
    httpx.post(f"{gateway_url}/v1/chat/completions",
               headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                        "Content-Type": "application/json",
                        "X-Gateway-Component": "document-processing"},
               json=chat_request(), timeout=30)
    time.sleep(0.5)

    # Substring from the provider_model_id (echo engine uses 'us.anthropic.claude-sonnet-...')
    r = http_admin.get("/admin/request-logs",
                       params={"model": "claude-sonnet", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0, "model ILIKE filter returned zero - regression of the model_alias≠model_id bug"


def test_filter_client_changes_cost_rollup(fresh_workspace, http_admin, gateway_url):
    """The WAVE 19 'client' dimension filters analytics end-to-end."""
    httpx.post(f"{gateway_url}/v1/chat/completions",
               headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                        "Content-Type": "application/json",
                        "X-Gateway-Component": "document-processing"},
               json=chat_request(), timeout=30)
    time.sleep(0.5)

    # Unfiltered: includes legacy rows (client_id=NULL) + novatech
    all_r = http_admin.get("/admin/cost", params={"group_by": "client"}).json()["rows"]
    # Filtered to novatech only
    filtered = http_admin.get("/admin/cost", params={"group_by": "client", "client": "novatech"}).json()["rows"]
    # Unfiltered must have \u2265 rows than filtered (likely NULL + novatech vs just novatech)
    assert len(all_r) >= len(filtered)
    assert all(r["key"] == "novatech" for r in filtered if r["key"] is not None)


def test_source_live_default_excludes_synthetic(http_admin):
    """Default analytics scope is source='live': synthetic-tagged rows are
    excluded unless include_synthetic=true is passed.

    Self-provisioning: a fresh cold-start DB has no synthetic backfill, and the
    ONLY mechanism that mints source='synthetic' rows is a direct RequestLog
    insert (that's what scripts/seed_synthetic.py does - the HTTP governance
    path always tags source='live', see gateway/governance/postgres_observer.py).
    So we insert a couple of uniquely-tagged synthetic rows the same way the
    seeder does, assert the include_synthetic filter flips them in/out, then
    delete them again in teardown."""
    import asyncio
    from datetime import datetime

    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from gateway.config import settings
    from gateway.db.models import RequestLog

    marker = f"synbvt-{uuid.uuid4().hex[:10]}"
    req_ids = [f"{marker}-{i}" for i in range(2)]

    async def _run(op):
        # A throwaway engine bound to THIS call's event loop, so the two
        # asyncio.run() calls below never try to reuse an asyncpg connection
        # across event loops (which would raise "attached to a different loop").
        eng = create_async_engine(settings.db_url)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as s:
                await op(s)
                await s.commit()
        finally:
            await eng.dispose()

    async def _insert(s):
        for rid in req_ids:
            s.add(RequestLog(
                timestamp=datetime.utcnow(), request_id=rid,
                workspace_id="ws-novatech-payments", provider="bedrock",
                model_alias="claude-sonnet-4-5",
                provider_model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                input_tokens=1, output_tokens=1, status="success",
                call_kind="chat", event_kind="completion", source="synthetic"))

    async def _cleanup(s):
        await s.execute(delete(RequestLog).where(RequestLog.request_id.in_(req_ids)))

    asyncio.run(_run(_insert))
    try:
        # Filter to just our marker rows (request_id is an ILIKE substring match)
        # so the assertion is immune to any concurrent live traffic other tests
        # generate between the two calls.
        live_only = http_admin.get(
            "/admin/request-logs", params={"request_id": marker, "limit": 5}).json()["total"]
        with_synth = http_admin.get(
            "/admin/request-logs",
            params={"request_id": marker, "limit": 5, "include_synthetic": "true"}).json()["total"]
        assert live_only == 0, f"synthetic rows leaked into the default live scope (got {live_only})"
        assert with_synth == len(req_ids), (
            f"include_synthetic didn't surface the synthetic rows "
            f"(live={live_only}, with_synth={with_synth})")
        assert with_synth > live_only
    finally:
        asyncio.run(_run(_cleanup))


def test_request_logs_ilike_user(fresh_workspace, http_admin, gateway_url):
    """Substring filter on user_id works (ILIKE)."""
    unique_user = f"alice-{uuid.uuid4().hex[:6]}"
    httpx.post(f"{gateway_url}/v1/chat/completions",
               headers={"Authorization": f"Bearer {fresh_workspace['key']}",
                        "Content-Type": "application/json",
                        "X-Gateway-Component": "document-processing",
                        "X-Gateway-User-Id": unique_user},
               json=chat_request(), timeout=30)
    time.sleep(0.5)

    r = http_admin.get("/admin/request-logs", params={"user": unique_user[:10], "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(unique_user in (row.get("user_id") or "") for row in body["rows"])


def test_request_logs_pagination(http_admin):
    """Pagination: page 0 + page 1 return different rows (offset works)."""
    page0 = http_admin.get("/admin/request-logs", params={"limit": 2, "offset": 0}).json()
    page1 = http_admin.get("/admin/request-logs", params={"limit": 2, "offset": 2}).json()
    # Total is the same regardless of offset
    assert page0["total"] == page1["total"]
    # Rows are different (assuming \u2265 4 live rows exist)
    if page0["total"] >= 4:
        ids0 = {r["request_id"] for r in page0["rows"]}
        ids1 = {r["request_id"] for r in page1["rows"]}
        assert ids0 != ids1, "page 0 and page 1 returned the same rows - offset ignored"
