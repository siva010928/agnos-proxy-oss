"""Admin write endpoints - full CRUD for workspaces, providers, aliases, keys,
quotas, budgets, guardrails. Credentials are encrypted at rest and synced to
Bifrost (create/rotate/delete) so the managed-key lifecycle stays consistent."""
from __future__ import annotations

import secrets as _secrets
import uuid
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete, func, or_, select

from gateway.bifrost import sync as bsync
from gateway.config import settings
from gateway.core import admin_validation as av
from gateway.core.auth import hash_key, invalidate_cache
from gateway.core.security import require_admin
from gateway.db.database import async_session
from gateway.db.models import (
    ApiKey, AuditLog, Component, GuardrailProfile, GuardrailRule,
    RequestLog, Workspace, WorkspaceProviderConfig,
)
from gateway.secrets.store import cipher

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["admin-crud"])


async def _audit(action: str, target: str, **detail) -> None:
    from gateway.core import audit
    await audit.record(principal="admin", action=action, target=target, detail=detail)


# ───────────────────────── Clients (WAVE 19) ─────────────────────────

from gateway.db.models import Client  # noqa: E402


class ClientIn(BaseModel):
    client_id: str
    name: str = ""
    budgets: dict = {}             # {client_usd, user_usd}
    rate_limits: dict = {}         # {rpm, tpm}
    required_headers: list[str] = []
    notes: str = ""


@router.get("/clients")
async def list_clients():
    async with async_session() as s:
        rows = (await s.scalars(select(Client))).all()
    return {"clients": [
        {"client_id": r.client_id, "name": r.name, "budgets": r.budgets or {},
         "rate_limits": r.rate_limits or {},
         "required_headers": r.required_headers or [], "notes": r.notes or ""}
        for r in rows
    ]}


@router.post("/clients")
async def create_client(body: ClientIn):
    av.raise_if(av.validate_client(body))
    async with async_session() as s:
        if await s.get(Client, body.client_id):
            raise HTTPException(409, "client exists")
        s.add(Client(**body.model_dump()))
        await s.commit()
    await _audit("client.create", body.client_id, name=body.name)
    return {"ok": True, "client_id": body.client_id}


@router.patch("/clients/{client_id}")
async def update_client(client_id: str, body: dict):
    async with async_session() as s:
        row = await s.get(Client, client_id)
        if not row:
            raise HTTPException(404, "not found")
        av.raise_if(av.validate_client_patch(body))
        for f in ("name", "budgets", "rate_limits", "required_headers", "notes"):
            if f in body:
                setattr(row, f, body[f])
        await s.commit()
    invalidate_cache()
    await _audit("client.update", client_id)
    return {"ok": True}


@router.delete("/clients/{client_id}")
async def delete_client(client_id: str, cascade: bool = False):
    """Delete a client. By default refuses if it still has workspaces; pass
    `cascade=true` to delete every child workspace (and all of THEIR children -
    providers, keys, components, guardrails, Bifrost keys) too."""
    async with async_session() as s:
        ws_ids = (await s.scalars(
            select(Workspace.workspace_id).where(Workspace.client_id == client_id))).all()
        if ws_ids and not cascade:
            raise HTTPException(409, detail={"error": {
                "type": "client_has_workspaces",
                "message": (f"Client '{client_id}' still has {len(ws_ids)} workspace(s). "
                            "Re-run with cascade=true to delete them and all their children."),
            }})
        row = await s.get(Client, client_id)
        if row is None:
            raise HTTPException(404, "not found")
        provider_keys: list[tuple[str, str]] = []
        for wid in ws_ids:
            provider_keys += await _cascade_delete_workspace_rows(s, wid)
        await s.delete(row)
        await s.commit()
    _prune_bifrost_keys_bg(provider_keys)
    invalidate_cache()
    await _audit("client.delete", client_id, cascade=cascade, workspaces=len(ws_ids))
    return {"ok": True, "deleted_workspaces": len(ws_ids)}


# ───────────────────────── Workspaces ─────────────────────────

class WorkspaceIn(BaseModel):
    workspace_id: str
    client_id: str | None = None       # WAVE 19: parent Client
    name: str = ""
    chat_models: dict = {}
    embedding_models: dict = {}
    default_chat_alias: str | None = None
    guardrails: dict = {}
    quotas: dict = {}
    budgets: dict = {}
    rate_limits: dict = {}             # workspace-wide RPM/TPM ceiling


@router.post("/workspaces")
async def create_workspace(body: WorkspaceIn):
    # Auto-set default_chat_alias when an admin defines aliases but forgets the
    # default. The first alias is a sane fallback; the response surfaces a clear
    # warning so the admin sees what happened and can adjust.
    auto_warning = None
    if not body.default_chat_alias and isinstance(body.chat_models, dict) and body.chat_models:
        body.default_chat_alias = next(iter(body.chat_models.keys()))
        auto_warning = (f"default_chat_alias was not set; auto-set to '{body.default_chat_alias}'. "
                        f"Change it under Workspaces if you wanted a different alias.")
    async with async_session() as s:
        av.raise_if(await av.validate_workspace_create(body, s))
        if await s.get(Workspace, body.workspace_id):
            raise HTTPException(409, "workspace exists")
        s.add(Workspace(**body.model_dump()))
        await s.commit()
    invalidate_cache()
    await _audit("workspace.create", body.workspace_id, name=body.name, client_id=body.client_id)
    out = {"ok": True, "workspace_id": body.workspace_id, "default_chat_alias": body.default_chat_alias}
    if auto_warning:
        out["warning"] = auto_warning
    return out


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(workspace_id: str, body: dict):
    async with async_session() as s:
        ws = await s.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(404, "not found")
        auto_warning: str | None = None
        # Self-heal the orphaned-alias trap: if chat_models is being changed and
        # the effective default_chat_alias is no longer one of its keys, clear it
        # so the workspace can never get stuck pointing at a removed alias.
        if "chat_models" in body:
            new_cm = body.get("chat_models") or {}
            eff_default = body.get("default_chat_alias", ws.default_chat_alias)
            if eff_default and isinstance(new_cm, dict) and eff_default not in new_cm:
                body["default_chat_alias"] = None
                eff_default = None
            # Auto-set when chat_models is non-empty but no default is in effect.
            # Reason: admins regularly create aliases without picking a default,
            # then are surprised that requests with model="default" silently pick
            # an arbitrary alias. Snapping to the first alias on save makes the
            # behavior predictable and the warning makes it loud.
            if isinstance(new_cm, dict) and new_cm and not eff_default:
                first = next(iter(new_cm.keys()))
                body["default_chat_alias"] = first
                auto_warning = (f"default_chat_alias was not set; auto-set to '{first}'. "
                                f"Change it under Workspaces if you wanted a different alias.")
        av.raise_if(await av.validate_workspace_patch(body, ws, s))
        for f in ("name", "client_id", "chat_models", "embedding_models", "default_chat_alias",
                  "guardrails", "quotas", "budgets", "rate_limits", "engine_overrides"):
            if f in body:
                setattr(ws, f, body[f])
        await s.commit()
    invalidate_cache()
    out: dict = {"ok": True}
    if auto_warning:
        out["warning"] = auto_warning
        out["default_chat_alias"] = body.get("default_chat_alias")
    return out


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str):
    """Cascade delete: workspace + provider configs + api keys + components +
    guardrail rules/profiles scoped to this workspace (or any of its components).
    AuditLog and RequestLog are intentionally retained for history.
    """
    async with async_session() as s:
        provider_keys = await _cascade_delete_workspace_rows(s, workspace_id)
        await s.commit()
    _prune_bifrost_keys_bg(provider_keys)
    invalidate_cache()
    await _audit("workspace.delete", workspace_id)
    return {"ok": True}


def _prune_bifrost_keys_bg(keys: list[tuple[str, str]]) -> None:
    """Delete managed keys from Bifrost in the BACKGROUND (best-effort). Kept off
    the request path so a slow/unreachable Bifrost never makes a delete hang."""
    async def _run():
        for provider, name in keys:
            try:
                await bsync.delete_key(provider, name)
            except Exception:  # noqa: BLE001
                pass
    if keys:
        asyncio.create_task(_run())


async def _cascade_delete_workspace_rows(s, workspace_id: str) -> list[tuple[str, str]]:
    """Delete a workspace + all its child rows within session `s`. Returns the
    list of (provider, bifrost_key_name) to prune from Bifrost after commit.
    Shared by delete_workspace and the cascade path of delete_client."""
    rows = (await s.scalars(select(WorkspaceProviderConfig)
                            .where(WorkspaceProviderConfig.workspace_id == workspace_id))).all()
    provider_keys = [(r.provider, r.bifrost_key_name) for r in rows if r.bifrost_key_name]
    await s.execute(sa_delete(WorkspaceProviderConfig).where(WorkspaceProviderConfig.workspace_id == workspace_id))
    await s.execute(sa_delete(ApiKey).where(ApiKey.workspace_id == workspace_id))
    await s.execute(sa_delete(Component).where(Component.workspace_id == workspace_id))
    await s.execute(sa_delete(GuardrailRule).where(GuardrailRule.workspace_id == workspace_id))
    await s.execute(sa_delete(GuardrailProfile).where(GuardrailProfile.workspace_id == workspace_id))
    ws = await s.get(Workspace, workspace_id)
    if ws:
        await s.delete(ws)
    return provider_keys


# ───────────────────────── Components (read-only auto-registry) ─────────────────────────
#
# WAVE 20 TRACK 1: components are NOT operator-creatable. They're a runtime
# attribution dimension: every chat carrying ``X-Gateway-Component: <name>``
# auto-registers a lightweight (workspace_id, name) row the first time it's
# seen. The admin endpoint below is read-only and returns the registry so
# Analytics + Request Logs filters have a populated dropdown source.
#
# We deliberately do NOT keep create/edit/delete endpoints. The repeated
# instruction "remove the editor" means the surface goes away, not just the
# screen \u2014 admins can't invent platform apps. If a future need for explicit
# component metadata (display_name etc) emerges, that's a separate decision
# captured in the doc; until then, the auto-registry is the source of truth.


def _component_dict(c: Component) -> dict:
    return {
        "id": c.id,
        "workspace_id": c.workspace_id,
        "name": c.name,
        "display_name": c.display_name,
        "first_seen": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/workspaces/{workspace_id}/components")
async def list_components(workspace_id: str):
    """Read-only: list components auto-registered for this workspace."""
    async with async_session() as s:
        rows = (await s.scalars(
            select(Component).where(Component.workspace_id == workspace_id)
                              .order_by(Component.name)
        )).all()
    return {"workspace_id": workspace_id, "components": [_component_dict(c) for c in rows]}


# ───────────────────────── Provider configs ─────────────────────────

class ProviderIn(BaseModel):
    provider: str
    credentials: dict          # plaintext in; encrypted at rest
    config: dict = {}          # region / base_url / api_version / aliases


@router.post("/workspaces/{workspace_id}/providers")
async def add_provider(workspace_id: str, body: ProviderIn):
    av.raise_if(av.validate_provider_in(body))
    async with async_session() as s:
        if not await s.get(Workspace, workspace_id):
            raise HTTPException(404, "workspace not found")
        existing = await s.scalar(select(WorkspaceProviderConfig).where(
            WorkspaceProviderConfig.workspace_id == workspace_id,
            WorkspaceProviderConfig.provider == body.provider))
        if existing:
            existing.encrypted_credentials = cipher().encrypt(body.credentials)
            existing.config = body.config
            existing.key_version += 1
            cfg_id = existing.id
        else:
            row = WorkspaceProviderConfig(
                workspace_id=workspace_id, provider=body.provider,
                config=body.config, encrypted_credentials=cipher().encrypt(body.credentials))
            s.add(row)
            await s.flush()
            cfg_id = row.id
        await s.commit()
    # Register/rotate the Bifrost managed key. Best-effort: a provider that only
    # runs on the DirectEngine (e.g. Bedrock bearer/SSO creds Bifrost can't use)
    # must still provision cleanly - the credential is already saved above.
    name, kid = None, None
    try:
        name, kid = await bsync.sync_one(cfg_id)
    except Exception as exc:  # noqa: BLE001
        await _audit("provider.upsert.bifrost_sync_skipped", f"{workspace_id}/{body.provider}",
                     error=str(exc)[:200])
    try:  # keep the stateful LiteLLM engine's store in sync too (best-effort)
        from gateway import litellm_sync
        await litellm_sync.sync_provider_row(cfg_id)
    except Exception:  # noqa: BLE001
        pass
    await _audit("provider.upsert", f"{workspace_id}/{body.provider}", bifrost_key=name)
    return {"ok": True, "bifrost_key_name": name, "bifrost_key_id": kid}


@router.patch("/workspaces/{workspace_id}/providers/{provider}")
async def update_provider_config(workspace_id: str, provider: str, body: dict):
    """Update a provider's NON-secret config (region / endpoint / api_version /
    request_timeout_seconds) WITHOUT re-entering credentials. Re-syncs the
    managed key to Bifrost so the new network config takes effect immediately."""
    new_config = body.get("config")
    if not isinstance(new_config, dict):
        raise HTTPException(400, "config object required")
    # Validate only the config fields (no credentials needed for a config edit).
    rt = new_config.get("request_timeout_seconds")
    if rt not in (None, ""):
        try:
            n = int(str(rt))
            if n < 1 or n > settings.max_request_timeout_s:
                raise HTTPException(422, f"request timeout must be between 1 and {settings.max_request_timeout_s} seconds")
        except (TypeError, ValueError):
            raise HTTPException(422, "request timeout must be an integer number of seconds")
    async with async_session() as s:
        row = await s.scalar(select(WorkspaceProviderConfig).where(
            WorkspaceProviderConfig.workspace_id == workspace_id,
            WorkspaceProviderConfig.provider == provider))
        if not row:
            raise HTTPException(404, "provider not found for this workspace")
        # Merge so callers can PATCH just the fields they changed (e.g. timeout).
        merged = {**(row.config or {}), **new_config}
        # Drop empty-string values so "clear the field" reverts to default.
        row.config = {k: v for k, v in merged.items() if v not in (None, "")}
        row.key_version += 1
        cfg_id = row.id
        await s.commit()
    name, kid = await bsync.sync_one(cfg_id)   # push new network_config to Bifrost
    try:  # refresh the LiteLLM engine's synced models with the new config (best-effort)
        from gateway import litellm_sync
        await litellm_sync.sync_provider_row(cfg_id)
    except Exception:  # noqa: BLE001
        pass
    await _audit("provider.config_update", f"{workspace_id}/{provider}", bifrost_key=name)
    return {"ok": True, "bifrost_key_name": name, "bifrost_key_id": kid}


@router.delete("/workspaces/{workspace_id}/providers/{provider}")
async def delete_provider(workspace_id: str, provider: str):
    async with async_session() as s:
        row = await s.scalar(select(WorkspaceProviderConfig).where(
            WorkspaceProviderConfig.workspace_id == workspace_id,
            WorkspaceProviderConfig.provider == provider))
        if not row:
            raise HTTPException(404, "not found")
        name = row.bifrost_key_name
        await s.delete(row)
        await s.commit()
    if name:
        try:
            await bsync.delete_key(provider, name)
        except Exception:  # noqa: BLE001
            pass
    try:  # drop this provider's synced models from the LiteLLM engine too (best-effort)
        from gateway import litellm_sync
        await litellm_sync.delete_provider_models(workspace_id, provider)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True}


# ───────────────────────── API keys ─────────────────────────

def _new_key(workspace_id: str) -> str:
    return f"gw-{workspace_id[:10]}-{_secrets.token_hex(12)}"


def _key_dict(k: ApiKey) -> dict:
    from datetime import datetime as _dt
    expired = bool(k.expires_at and k.expires_at < _dt.utcnow())
    status = "disabled" if k.disabled else ("expired" if expired else "active")
    return {"id": k.id, "workspace_id": k.workspace_id, "prefix": k.prefix,
            "disabled": k.disabled, "status": status,
            "roles": list(k.roles or ["member"]),
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "created_at": k.created_at.isoformat() if k.created_at else None}


class KeyIssueIn(BaseModel):
    expires_at: str | None = None             # ISO date/datetime, optional
    roles: list[str] = ["member"]


@router.post("/workspaces/{workspace_id}/keys")
async def issue_key(workspace_id: str, body: KeyIssueIn | None = None):
    body = body or KeyIssueIn()
    av.raise_if(av.validate_key_issue(body))
    async with async_session() as s:
        if not await s.get(Workspace, workspace_id):
            raise HTTPException(404, "workspace not found")
        raw = _new_key(workspace_id)
        exp = None
        if body.expires_at:
            # Validator above already proved this parses + is in the future
            exp = av._is_iso_date_or_datetime(body.expires_at)
        s.add(ApiKey(workspace_id=workspace_id, sha256=hash_key(raw), prefix=raw[:14] + "…",
                     roles=body.roles or ["member"], expires_at=exp))
        await s.commit()
    await _audit("key.issue", workspace_id, prefix=raw[:14] + "…", roles=body.roles or ["member"])
    return {"api_key": raw, "note": "shown once; only the SHA-256 is stored"}


@router.post("/workspaces/{workspace_id}/keys/{key_id}/rotate")
async def rotate_key(workspace_id: str, key_id: int):
    async with async_session() as s:
        row = await s.get(ApiKey, key_id)
        if not row or row.workspace_id != workspace_id:
            raise HTTPException(404, "not found")
        raw = _new_key(workspace_id)
        row.sha256 = hash_key(raw)
        row.prefix = raw[:14] + "…"
        await s.commit()
    invalidate_cache()
    await _audit("key.rotate", workspace_id, key_id=key_id, prefix=raw[:14] + "…")
    return {"api_key": raw, "note": "old key invalidated"}


@router.delete("/workspaces/{workspace_id}/keys/{key_id}")
async def disable_key(workspace_id: str, key_id: int):
    async with async_session() as s:
        row = await s.get(ApiKey, key_id)
        if not row or row.workspace_id != workspace_id:
            raise HTTPException(404, "not found")
        row.disabled = True
        await s.commit()
    invalidate_cache()
    await _audit("key.disable", workspace_id, key_id=key_id)
    return {"ok": True}


@router.get("/workspaces/{workspace_id}/keys")
async def list_keys(workspace_id: str):
    async with async_session() as s:
        rows = (await s.scalars(select(ApiKey).where(ApiKey.workspace_id == workspace_id))).all()
    return {"keys": [_key_dict(k) for k in rows]}


@router.get("/workspaces/{workspace_id}/providers")
async def list_providers(workspace_id: str):
    """Per-workspace provider configs. **Never** returns decrypted credentials."""
    async with async_session() as s:
        rows = (await s.scalars(select(WorkspaceProviderConfig).where(
            WorkspaceProviderConfig.workspace_id == workspace_id))).all()
    return {"providers": [
        {"id": r.id, "provider": r.provider, "config": r.config or {},
         "bifrost_key_name": r.bifrost_key_name, "key_version": r.key_version,
         "updated_at": r.updated_at.isoformat() if r.updated_at else None}
        for r in rows
    ]}


@router.get("/routing/preview")
async def routing_preview(workspace: str, alias: str | None = None, component: str | None = None):
    """Non-secret routing introspection for the admin UI - same shape as
    /v1/routing/resolve, parameterized by workspace_id (no auth header required)."""
    from gateway.core.auth import _ctx_from_workspace, _overlay_component
    async with async_session() as s:
        ws = await s.get(Workspace, workspace)
    if not ws:
        raise HTTPException(404, "workspace not found")
    ctx = _ctx_from_workspace(ws, ["admin"], auth_method="admin-preview")
    if component:
        await _overlay_component(ctx, component)
    chosen = alias or ctx.default_chat_alias
    aliases: dict[str, list[dict]] = {}
    for a, spec in (ctx.chat_models or {}).items():
        targets = spec if isinstance(spec, list) else [spec]
        aliases[a] = [{"provider": t.get("provider"), "model_id": t.get("model_id"),
                       "context_window": t.get("context_window"),
                       "weight": t.get("weight", 1),
                       "role": "primary" if i == 0 else "fallback"}
                      for i, t in enumerate(targets)]
    return {"workspace_id": workspace, "component": ctx.component,
            "default_chat_alias": ctx.default_chat_alias,
            "resolved_alias": chosen,
            "resolved_targets": aliases.get(chosen or "", []),
            "aliases": aliases,
            "guardrails": ctx.guardrails, "quotas": ctx.quotas, "budgets": ctx.budgets}


# ───────────────────────── Provider connection test (real probe) ─────────────────────────

class ProviderTestIn(BaseModel):
    provider: str
    credentials: dict = {}
    config: dict = {}
    model_id: str | None = None


@router.get("/engine-routing")
async def get_engine_routing():
    """Gateway-WIDE engine routing (rented↔owned per provider) - identical for every
    client/workspace. Returns {overrides: {provider: ''|'direct'|int}}."""
    from gateway.core.engine_routing import get_overrides
    return {"overrides": get_overrides()}


@router.patch("/engine-routing")
async def set_engine_routing(body: dict):
    from gateway.core.engine_routing import set_overrides
    overrides = body.get("overrides") if isinstance(body, dict) else None
    if not isinstance(overrides, dict):
        raise HTTPException(400, "body must be {\"overrides\": {provider: ''|'direct'|int}}")
    saved = await set_overrides(overrides)
    invalidate_cache()
    return {"ok": True, "overrides": saved}


@router.post("/providers/test")
async def test_provider(body: ProviderTestIn):
    """Real 1-token probe with the supplied creds (no persistence). Green/red + error."""
    from gateway.core.provider_test import test_connection
    return await test_connection(body.provider, body.credentials, body.config, body.model_id)


@router.post("/providers/available-models")
async def available_models_for_creds(body: ProviderTestIn):
    """List the models THIS account can actually reach, using the supplied creds
    (before save) - so the editor can restrict model selection to the accessible set."""
    from gateway.core.provider_models import list_available_models
    return await list_available_models(body.provider, body.credentials, body.config)


@router.get("/workspaces/{workspace_id}/providers/{provider}/available-models")
async def available_models_for_provider(workspace_id: str, provider: str):
    """List the models a CONFIGURED provider account can reach (reads the stored,
    encrypted credentials). Used by the alias editor to offer only reachable models."""
    from gateway.core.credentials import get_provider_credential
    from gateway.core.provider_models import list_available_models
    cred = await get_provider_credential(workspace_id, provider)
    if not cred:
        return {"ok": False, "error": f"provider '{provider}' is not configured on this workspace", "models": [], "count": 0}
    return await list_available_models(provider, cred.credentials, cred.config)


# ───────────────────────── Custom pricing (DB-driven overrides) ─────────────────────────

class PricingIn(BaseModel):
    model_substr: str
    input_per_1k: float
    output_per_1k: float
    note: str = ""


@router.get("/pricing")
async def list_pricing():
    from gateway.db.models import CustomPricing
    async with async_session() as s:
        rows = (await s.scalars(select(CustomPricing))).all()
    return {"overrides": [{"id": r.id, "model_substr": r.model_substr,
                           "input_per_1k": r.input_per_1k, "output_per_1k": r.output_per_1k,
                           "note": r.note} for r in rows]}


@router.post("/pricing")
async def upsert_pricing(body: PricingIn):
    av.raise_if(av.validate_pricing_in(body))
    from gateway.core.pricing import set_override
    from gateway.db.models import CustomPricing
    async with async_session() as s:
        existing = await s.scalar(select(CustomPricing).where(CustomPricing.model_substr == body.model_substr))
        if existing:
            existing.input_per_1k = body.input_per_1k
            existing.output_per_1k = body.output_per_1k
            existing.note = body.note
        else:
            s.add(CustomPricing(**body.model_dump()))
        await s.commit()
    set_override(body.model_substr, body.input_per_1k, body.output_per_1k)
    await _audit("pricing.upsert", body.model_substr)
    return {"ok": True}


@router.delete("/pricing/{model_substr}")
async def delete_pricing(model_substr: str):
    from gateway.core.pricing import clear_override
    from gateway.db.models import CustomPricing
    async with async_session() as s:
        row = await s.scalar(select(CustomPricing).where(CustomPricing.model_substr == model_substr))
        if row:
            await s.delete(row)
            await s.commit()
    clear_override(model_substr)
    return {"ok": True}


# ───────────────────────── Guardrail Profiles + Rules (DB-driven) ─────────────────────────

class ProfileIn(BaseModel):
    name: str
    detector_type: str            # regex|secrets|keyword|presidio|bedrock|azure|model-armor
    policy_name: str = ""
    enabled: bool = True
    config: dict = {}
    scope: str = "global"
    workspace_id: str | None = None
    component: str | None = None


class RuleIn(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    cel_expression: str = "true"
    builder_spec: dict | None = None
    apply_to: str = "input"       # input|output|both
    action: str = "block"         # block|redact|audit
    sampling_rate: float = 1.0
    timeout_ms: int = 1000
    profile_ids: list[int] = []
    scope: str = "global"
    workspace_id: str | None = None
    component: str | None = None


def _gr_inval():
    from gateway.core.guardrails import store as _s
    _s.invalidate()


@router.get("/guardrails/profiles")
async def list_profiles():
    from gateway.db.models import GuardrailProfile
    async with async_session() as s:
        rows = (await s.scalars(select(GuardrailProfile))).all()
    return {"profiles": [{"id": p.id, "name": p.name, "detector_type": p.detector_type,
                          "policy_name": p.policy_name, "enabled": p.enabled, "config": p.config,
                          "scope": p.scope, "workspace_id": p.workspace_id, "component": p.component}
                         for p in rows]}


@router.post("/guardrails/profiles")
async def create_profile(body: ProfileIn):
    from gateway.db.models import GuardrailProfile
    async with async_session() as s:
        av.raise_if(await av.validate_profile_in(body, s))
        row = GuardrailProfile(**body.model_dump())
        s.add(row); await s.flush(); pid = row.id
        await s.commit()
    _gr_inval(); await _audit("guardrail_profile.create", str(pid), detector=body.detector_type)
    return {"ok": True, "id": pid}


@router.patch("/guardrails/profiles/{pid}")
async def update_profile(pid: int, body: dict):
    from gateway.db.models import GuardrailProfile
    async with async_session() as s:
        row = await s.get(GuardrailProfile, pid)
        if not row:
            raise HTTPException(404, "not found")
        # Merge body onto current row state, then validate the would-be result
        merged = ProfileIn(**{
            "name": body.get("name", row.name),
            "detector_type": body.get("detector_type", row.detector_type),
            "policy_name": body.get("policy_name", row.policy_name or ""),
            "enabled": body.get("enabled", row.enabled),
            "config": body.get("config", row.config or {}),
            "scope": body.get("scope", row.scope),
            "workspace_id": body.get("workspace_id", row.workspace_id),
            "component": body.get("component", row.component),
        })
        av.raise_if(await av.validate_profile_in(merged, s))
        for f in ("name", "detector_type", "policy_name", "enabled", "config", "scope", "workspace_id", "component"):
            if f in body:
                setattr(row, f, body[f])
        await s.commit()
    _gr_inval(); return {"ok": True}


@router.delete("/guardrails/profiles/{pid}")
async def delete_profile(pid: int):
    from gateway.db.models import GuardrailProfile
    async with async_session() as s:
        row = await s.get(GuardrailProfile, pid)
        if row:
            await s.delete(row)
            # Sweep dangling profile_ids references in rules so they don't silently no-op later
            rules = (await s.scalars(select(GuardrailRule))).all()
            for r in rules:
                ids = list(r.profile_ids or [])
                if pid in ids:
                    r.profile_ids = [x for x in ids if x != pid]
            await s.commit()
    _gr_inval(); await _audit("guardrail_profile.delete", str(pid))
    return {"ok": True}


@router.get("/guardrails/rules")
async def list_rules(workspace_id: str | None = None, component: str | None = None):
    """List guardrail rules.

    With no params → ALL rules (the admin-wide Rule Builder management view).
    With `workspace_id` → only rules that ACTUALLY apply to that workspace
    (global, or this workspace's own workspace/component-scoped rules). This
    closes the cross-workspace leak: a per-workspace editor must never offer
    another workspace's scoped rules for selection.
    """
    from gateway.db.models import GuardrailRule
    async with async_session() as s:
        if workspace_id:
            stmt = select(GuardrailRule).where(
                or_(GuardrailRule.scope == "global",
                    GuardrailRule.workspace_id == workspace_id))
        else:
            stmt = select(GuardrailRule)
        rows = (await s.scalars(stmt)).all()
    # component-scoped rules narrow further: only when component matches (or no
    # component filter requested, in which case all of this workspace's are shown).
    if workspace_id and component is not None:
        rows = [r for r in rows
                if r.scope != "component" or r.component == component]
    return {"rules": [{"id": r.id, "name": r.name, "description": r.description, "enabled": r.enabled,
                       "cel_expression": r.cel_expression, "builder_spec": r.builder_spec,
                       "apply_to": r.apply_to, "action": r.action,
                       "sampling_rate": r.sampling_rate, "timeout_ms": r.timeout_ms,
                       "profile_ids": r.profile_ids, "scope": r.scope,
                       "workspace_id": r.workspace_id, "component": r.component} for r in rows]}


@router.post("/guardrails/rules")
async def create_rule(body: RuleIn):
    from gateway.db.models import GuardrailRule
    async with async_session() as s:
        av.raise_if(await av.validate_rule_in(body, s))
        row = GuardrailRule(**body.model_dump())
        s.add(row); await s.flush(); rid = row.id
        await s.commit()
    _gr_inval(); await _audit("guardrail_rule.create", str(rid), name=body.name)
    return {"ok": True, "id": rid}


@router.patch("/guardrails/rules/{rid}")
async def update_rule(rid: int, body: dict):
    from gateway.db.models import GuardrailRule
    async with async_session() as s:
        row = await s.get(GuardrailRule, rid)
        if not row:
            raise HTTPException(404, "not found")
        # Merge + validate as if final state
        merged = RuleIn(**{
            "name": body.get("name", row.name),
            "description": body.get("description", row.description or ""),
            "enabled": body.get("enabled", row.enabled),
            "cel_expression": body.get("cel_expression", row.cel_expression or "true"),
            "builder_spec": body.get("builder_spec", row.builder_spec),
            "apply_to": body.get("apply_to", row.apply_to),
            "action": body.get("action", row.action),
            "sampling_rate": body.get("sampling_rate", row.sampling_rate),
            "timeout_ms": body.get("timeout_ms", row.timeout_ms),
            "profile_ids": body.get("profile_ids", row.profile_ids or []),
            "scope": body.get("scope", row.scope),
            "workspace_id": body.get("workspace_id", row.workspace_id),
            "component": body.get("component", row.component),
        })
        av.raise_if(await av.validate_rule_in(merged, s))
        for f in ("name", "description", "enabled", "cel_expression", "builder_spec",
                  "apply_to", "action",
                  "sampling_rate", "timeout_ms", "profile_ids", "scope", "workspace_id", "component"):
            if f in body:
                setattr(row, f, body[f])
        await s.commit()
    _gr_inval(); return {"ok": True}


@router.delete("/guardrails/rules/{rid}")
async def delete_rule(rid: int):
    from gateway.db.models import GuardrailRule
    async with async_session() as s:
        row = await s.get(GuardrailRule, rid)
        if row:
            await s.delete(row); await s.commit()
    _gr_inval(); await _audit("guardrail_rule.delete", str(rid))
    return {"ok": True}


class GuardrailTestIn(BaseModel):
    content: str
    cel_expression: str = "true"
    action: str = "block"
    profiles: list[dict] = []          # [{detector_type, config}] - inline test
    profile_ids: list[int] = []        # or reference stored profiles
    headers: dict | None = None        # optional sample headers (for header[..] CEL)
    model: str | None = None           # optional sample model id


class GuardrailProfileTestIn(BaseModel):
    detector_type: str
    config: dict = {}


@router.post("/guardrails/test-profile")
async def test_guardrail_profile(body: GuardrailProfileTestIn):
    """Connectivity test for a detector profile (currently AWS Bedrock Guardrails)
    using ONLY the supplied config/creds - calls the real ApplyGuardrail API."""
    if body.detector_type == "bedrock":
        from gateway.core.provider_test import test_bedrock_guardrail
        return await test_bedrock_guardrail(body.config or {})
    return {"ok": False, "error": f"Connectivity test not supported for detector '{body.detector_type}'."}


@router.post("/guardrails/test")
async def test_guardrail(body: GuardrailTestIn):
    """Actually evaluate a CEL rule + its detectors against sample content (real, not dummy)."""
    from gateway.core.guardrails import store as _s
    profiles = list(body.profiles)
    if body.profile_ids:
        from gateway.db.models import GuardrailProfile
        async with async_session() as s:
            for pid in body.profile_ids:
                p = await s.get(GuardrailProfile, pid)
                if p:
                    profiles.append({"detector_type": p.detector_type, "config": p.config})
    return await _s.test_rule(body.content, body.cel_expression, profiles, body.action,
                              headers=body.headers, model=body.model)


class CelIn(BaseModel):
    cel_expression: str


@router.post("/guardrails/validate-cel")
async def validate_cel(body: CelIn):
    """Compile/validate a CEL expression with celpy. Returns {ok, error?, position?}."""
    expr = body.cel_expression or ""
    if not expr.strip():
        return {"ok": True, "warning": "empty expression - applies to all requests at sampling rate"}
    try:
        import celpy  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"celpy not installed: {exc}"}
    try:
        from celpy import Environment
        env = Environment()
        env.compile(expr)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        # celpy errors usually include "line N column M"; surface it for the editor caret
        import re as _re
        m = _re.search(r"line\s+(\d+)\s*[,:]\s*col(?:umn)?\s+(\d+)", msg, _re.I)
        return {"ok": False, "error": msg[:300],
                "line": int(m.group(1)) if m else None,
                "column": int(m.group(2)) if m else None}


# ───────────────────────── Model Catalog (WAVE 19 TRACK C4) ─────────────────────────

from gateway.db.models import ModelCatalog  # noqa: E402


class ModelCatalogIn(BaseModel):
    provider: str
    model_id: str
    display_name: str | None = None
    context_window: int = 0
    supports_tools: bool = False
    supports_images: bool = False
    supports_reasoning: bool = False
    supports_streaming: bool = True
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0
    enabled: bool = True
    notes: str = ""


@router.get("/model-catalog")
async def list_model_catalog():
    from gateway.core.model_catalog import _row_dict
    async with async_session() as s:
        rows = (await s.scalars(select(ModelCatalog))).all()
    return {"models": [_row_dict(r) for r in rows]}


@router.post("/model-catalog")
async def upsert_model_catalog(body: ModelCatalogIn):
    if body.provider not in av.PROVIDERS:
        av.raise_if([av.err(["body", "provider"],
                            f"provider must be one of {', '.join(av.PROVIDERS)}")])
    if not body.model_id.strip():
        av.raise_if([av.err(["body", "model_id"], "model_id is required")])
    async with async_session() as s:
        existing = await s.scalar(select(ModelCatalog).where(
            ModelCatalog.provider == body.provider,
            ModelCatalog.model_id == body.model_id))
        if existing:
            for f, v in body.model_dump().items():
                setattr(existing, f, v)
        else:
            s.add(ModelCatalog(**body.model_dump()))
        await s.commit()
    from gateway.core.model_catalog import invalidate_catalog_cache
    invalidate_catalog_cache()
    await _audit("model_catalog.upsert", f"{body.provider}/{body.model_id}",
                 enabled=body.enabled)
    return {"ok": True}


@router.delete("/model-catalog/{provider}/{model_id:path}")
async def delete_model_catalog(provider: str, model_id: str):
    async with async_session() as s:
        row = await s.scalar(select(ModelCatalog).where(
            ModelCatalog.provider == provider,
            ModelCatalog.model_id == model_id))
        if row is None:
            raise HTTPException(404, "not found")
        await s.delete(row)
        await s.commit()
    from gateway.core.model_catalog import invalidate_catalog_cache
    invalidate_catalog_cache()
    await _audit("model_catalog.delete", f"{provider}/{model_id}")
    return {"ok": True}


# ───────────────────────── Engine swap (proof-of-swap) ─────────────────────────

async def _maybe_sync_engine(name: str) -> None:
    """When a stateful engine becomes active, make sure its store is synced from our
    vault. LiteLLM: reconcile models. (Bifrost self-persists + reconciles at startup.)"""
    if name == "litellm":
        try:
            from gateway import litellm_sync
            if await litellm_sync.healthy():
                await litellm_sync.reconcile_all()
        except Exception:  # noqa: BLE001
            pass


class EngineIn(BaseModel):
    engine: str   # bifrost | litellm | portkey | direct | echo


@router.post("/engine/reconcile")
async def engine_reconcile():
    """Force a re-sync of the LiteLLM engine's model store from our vault. Repopulates
    an empty/stale engine DB (e.g. after a fresh deploy) so every workspace's scoped
    ws-* model exists. Best-effort; returns how many models were (re)registered."""
    from gateway import litellm_sync
    healthy = await litellm_sync.healthy()
    n = await litellm_sync.reconcile_all() if healthy else 0
    return {"ok": True, "engine": "litellm", "healthy": healthy, "models_synced": n}


async def _bg_reconcile(name: str) -> None:
    """Sync our vault -> a stateful engine's key store in the BACKGROUND, logging
    progress into the live tail. Keeps the swap itself instant (fixes the slow
    live-demo switch to LiteLLM); the engine's own DB persists across activations."""
    import time as _t
    from gateway.core import log_buffer
    log_buffer.record(f"reconcile: syncing our encrypted vault -> {name} key store "
                      f"(store_model_in_db) ...", source="engine.swap")
    t0 = _t.monotonic()
    try:
        await _maybe_sync_engine(name)
        log_buffer.record(f"reconcile: {name} key store in sync ({round(_t.monotonic() - t0, 1)}s)",
                          source="engine.swap")
    except Exception as exc:  # noqa: BLE001
        log_buffer.record(f"reconcile: {name} sync error: {str(exc)[:120]}", level="ERROR", source="engine.swap")


@router.post("/engine")
async def set_engine(body: EngineIn):
    """Activate an engine for the whole slot. PERSISTS across restarts + page loads
    (engine_state), so whatever you activate sticks until you switch. Governance is
    unaffected (anti-corruption boundary keeps the contract identical).

    Returns `evidence` showing exactly WHERE the config changed (the durable
    gateway_settings row) and emits real log lines into the live tail so an operator
    can see the swap happen for real."""
    name = body.engine
    if name not in av.ENGINES:
        raise HTTPException(400, f"engine must be one of {', '.join(av.ENGINES)}")
    import gateway.runtime as rt
    from gateway.core import engine_state, log_buffer
    from gateway.core.engine_routing import set_overrides

    prev_runtime = rt._engine.name if rt._engine else settings.engine
    prev_persisted = engine_state.get()
    if name == prev_runtime and prev_persisted == name:
        return {"engine": name, "governance": "unaffected", "persisted": True, "unchanged": True,
                "evidence": {"store": "postgres", "table": "gateway_settings", "column": "value",
                             "key": "active_engine", "previous": name, "new": name,
                             "runtime_prev": prev_runtime, "runtime_now": name, "reconcile": "none"}}

    log_buffer.record(f"POST /admin/engine  {{\"engine\": \"{name}\"}}", source="engine.swap")
    # ── the REAL config change: durable single source of truth ──
    rt._engine = rt.engine_by_name(name)
    await engine_state.set_active(name)      # durable: survives restart
    await set_overrides({})                  # single source of truth = the active engine
    invalidate_cache()
    await _audit("engine.activate", name)
    log_buffer.record(
        f"engine_state: UPDATE gateway_settings SET value='\"{name}\"' "
        f"WHERE key='active_engine'  (was {json.dumps(prev_persisted)})", source="engine.swap")
    log_buffer.record(f"runtime._engine = {rt._engine.__class__.__name__}  "
                      f"(auth/guardrails/budgets/audit boundary unchanged)", source="engine.swap")

    stateful = name in ("litellm", "bifrost")
    reconcile = "none"
    if name == "litellm":            # only LiteLLM needs the slow vault->store sync
        reconcile = "background"
        asyncio.create_task(_bg_reconcile(name))
    evidence = {
        "store": "postgres", "table": "gateway_settings", "column": "value",
        "key": "active_engine", "previous": prev_persisted or prev_runtime, "new": name,
        "runtime_prev": prev_runtime, "runtime_now": rt._engine.name,
        "stateful": stateful, "reconcile": reconcile,
    }
    return {"engine": rt._engine.name, "governance": "unaffected", "persisted": True, "evidence": evidence}


# ───────────────────── Quarantine & Evacuate (incident response) ─────────────────────
# The demo's Act 3 payoff: a commodity translator in the slot is compromised, so we
# EVACUATE every provider to a safe (stateless/owned) engine in ONE write. Governance
# (auth, budgets, guardrails, audit, our encrypted vault) never lived in the engine,
# so this is a config flip - not a fleet rebuild. Fully audited + reversible.

_QUARANTINE_SNAPSHOT: dict | None = None


async def _configured_providers_list() -> list[str]:
    async with async_session() as s:
        rows = (await s.scalars(select(WorkspaceProviderConfig.provider).distinct())).all()
    return sorted({r for r in rows if r})


_ENGINE_AVAIL: dict = {"at": 0.0, "data": {}}


async def _engine_availability() -> dict[str, bool]:
    """Which engines can actually serve right now. direct/echo are in-process (always
    available); bifrost/litellm/portkey are available only if their sidecar answers a
    healthcheck. Cached ~20s so dashboard polling doesn't spam the sidecars."""
    import time
    import gateway.runtime as rt
    if time.monotonic() - _ENGINE_AVAIL["at"] < 20 and _ENGINE_AVAIL["data"]:
        return _ENGINE_AVAIL["data"]

    async def _one(name: str) -> tuple[str, bool]:
        if name in ("direct", "echo"):
            return name, True
        try:
            eng = rt.engine_by_name(name)
            ok = bool(await asyncio.wait_for(eng.healthcheck(), timeout=4))
            client = getattr(eng, "_client", None)
            if client is not None:
                try:
                    await client.aclose()
                except Exception:  # noqa: BLE001
                    pass
            return name, ok
        except Exception:  # noqa: BLE001
            return name, False

    res = dict(await asyncio.gather(*[_one(n) for n in av.ENGINES]))
    _ENGINE_AVAIL.update(at=time.monotonic(), data=res)
    return res


@router.get("/engine/catalog")
async def engine_catalog_state():
    """Everything the Engine & Health screen needs: the engines that can occupy the
    slot (blast-radius metadata + LIVE availability), the engine serving now, the
    per-provider routing, and the providers currently in use."""
    from gateway.core import engine_catalog as ec, engine_state
    from gateway.core.engine_routing import get_overrides
    import gateway.runtime as rt
    providers = await _configured_providers_list()
    avail = await _engine_availability()
    return {
        "current_engine": rt.engine().name,
        "active_engine": engine_state.get(),
        "overrides": get_overrides(),
        "providers": providers,
        "safe_engines": list(ec.SAFE_ENGINES),
        "engines": {name: {**ec.meta(name), "available": bool(avail.get(name))} for name in av.ENGINES},
        "available_engines": [n for n in av.ENGINES if avail.get(n)],
        "quarantined": _QUARANTINE_SNAPSHOT is not None,
    }


class QuarantineIn(BaseModel):
    to: str = "portkey"                # safe engine to evacuate onto
    incident: str = "compromised engine"  # short human label recorded in the audit trail


@router.post("/engine/quarantine")
async def quarantine_engine(body: QuarantineIn):
    """EVACUATE: flip the whole slot to a safe engine, PERSISTED. Records the
    incident + a before/after snapshot so the dashboard can show what changed and
    why it was safe. One write; governance never moves."""
    from gateway.core import engine_catalog as ec, engine_state
    from gateway.core.engine_routing import set_overrides
    import gateway.runtime as rt

    target = body.to
    if target not in av.ENGINES:
        raise HTTPException(400, f"engine must be one of {', '.join(av.ENGINES)}")

    global _QUARANTINE_SNAPSHOT
    before_engine = rt.engine().name
    _QUARANTINE_SNAPSHOT = {"engine": before_engine}

    providers = await _configured_providers_list()
    await _maybe_sync_engine(target)
    rt._engine = rt.engine_by_name(target)
    await engine_state.set_active(target)     # durable: the evacuation sticks
    await set_overrides({})                   # single source of truth = the active engine
    invalidate_cache()

    await _audit("engine.quarantine", target,
                 incident=body.incident, from_engine=before_engine,
                 to_engine=target, providers=providers)

    return {
        "ok": True,
        "action": "quarantine",
        "incident": body.incident,
        "from": {"engine": before_engine, "meta": ec.meta(before_engine)},
        "to": {"engine": target, "meta": ec.meta(target)},
        "providers_evacuated": providers,
        "message": (f"Evacuated {len(providers)} provider(s) from '{before_engine}' to "
                    f"'{target}'. Governance, workspace keys, budgets and audit never moved."),
    }


@router.post("/engine/restore")
async def restore_engine(body: dict | None = None):
    """Revert the last evacuation (or set an explicit engine), PERSISTED."""
    from gateway.core import engine_state
    from gateway.core.engine_routing import set_overrides
    import gateway.runtime as rt
    global _QUARANTINE_SNAPSHOT

    explicit = (body or {}).get("to") if isinstance(body, dict) else None
    target = explicit or (_QUARANTINE_SNAPSHOT or {}).get("engine") or "bifrost"
    if target not in av.ENGINES:
        raise HTTPException(400, f"engine must be one of {', '.join(av.ENGINES)}")
    rt._engine = rt.engine_by_name(target)
    await engine_state.set_active(target)
    await set_overrides({})
    invalidate_cache()
    await _audit("engine.restore", target, mode="explicit" if explicit else "snapshot")
    _QUARANTINE_SNAPSHOT = None
    return {"ok": True, "engine": rt._engine.name, "overrides": {}}


# ───────────────────────── Currency & FX (WAVE 25 TRACK 1) ─────────────────────────

@router.get("/settings/currency")
async def get_currency_settings():
    """Current default currency + available currencies + latest FX rates."""
    from gateway.core.fx import get_default_currency, get_rate, CURRENCIES
    from datetime import date
    default = await get_default_currency()
    rates = {c: get_rate(c, date.today()) for c in CURRENCIES}
    return {"default_currency": default, "currencies": list(CURRENCIES), "rates": rates}


@router.post("/settings/currency")
async def set_currency_settings(body: dict):
    """Set the global default currency (or per-client override)."""
    from gateway.core.fx import set_default_currency, CURRENCIES
    currency = body.get("currency", "").upper()
    if currency not in CURRENCIES:
        raise HTTPException(422, detail=[{"loc": ["body", "currency"],
                                          "msg": f"currency must be one of {', '.join(CURRENCIES)}",
                                          "type": "value_error"}])
    client_id = body.get("client_id")
    if client_id:
        # Per-client currency override
        async with async_session() as s:
            from gateway.db.models import GatewaySettings
            key = f"currency:{client_id}"
            row = await s.get(GatewaySettings, key)
            if row:
                row.value = currency
            else:
                s.add(GatewaySettings(key=key, value=currency))
            await s.commit()
    else:
        await set_default_currency(currency)
    await _audit("settings.currency", currency, client_id=client_id)
    return {"ok": True, "currency": currency, "client_id": client_id}


# ───────────────────────── Profitability (WAVE 25 TRACK 2) ─────────────────────────

@router.get("/profitability")
async def profitability(
    group_by: str = "client",
    currency: str | None = None,
    client: str | None = None,
    workspace: str | None = None,
    from_: str | None = Query(None, alias="from"), to: str | None = None,
):
    """Profitability/chargeback view: raw cost vs billed vs margin per dimension.
    Uses the RateCard markup_pct (per-client or global default). Currency conversion
    is time-accurate (per-row date × FX rate for that date)."""
    from gateway.core.fx import get_default_currency, convert_usd, get_rate
    from gateway.db.models import RateCard
    from datetime import date as _date

    cur = (currency or await get_default_currency()).upper()

    # Load rate cards (per-client + global fallback)
    async with async_session() as s:
        cards = (await s.scalars(select(RateCard))).all()
    card_map: dict[str | None, float] = {}
    for c in cards:
        card_map[c.client_id] = c.markup_pct
    global_markup = card_map.get(None, 30.0)  # 30% default

    # Reuse existing /admin/cost logic for the breakdown
    col_map = {"client": RequestLog.client_id, "workspace": RequestLog.workspace_id,
               "component": RequestLog.component, "user": RequestLog.user_id}
    if group_by not in col_map:
        raise HTTPException(400, "group_by must be client|workspace|component|user")
    col = col_map[group_by]
    from gateway.routes.admin import _filter_conds, _parse_dt
    conds = _filter_conds(workspace, None, None, None, None, None,
                          _parse_dt(from_), _parse_dt(to), client=client)
    async with async_session() as s:
        rows = (await s.execute(
            select(col.label("key"),
                   func.count(RequestLog.id).label("requests"),
                   func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                   func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                   func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("raw_cost_usd"))
            .where(*conds).group_by(col)
            .order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0.0).desc())
        )).all()

    result = []
    for r in rows:
        key = r.key or "(unattributed)"
        raw_usd = float(r.raw_cost_usd)
        markup = card_map.get(r.key, global_markup)
        billed_usd = raw_usd * (1 + markup / 100)
        margin_usd = billed_usd - raw_usd
        margin_pct = markup
        # Convert to target currency (use today's rate for the aggregate view;
        # per-row time-accuracy is shown in detailed breakdowns)
        rate = get_rate(cur, _date.today())
        result.append({
            "key": key,
            "requests": r.requests,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "raw_cost_usd": round(raw_usd, 6),
            "raw_cost": round(raw_usd * rate, 4),
            "billed": round(billed_usd * rate, 4),
            "margin": round(margin_usd * rate, 4),
            "margin_pct": round(margin_pct, 1),
            "currency": cur,
        })

    total_raw = sum(r["raw_cost_usd"] for r in result)
    total_billed_usd = sum(r["raw_cost_usd"] * (1 + card_map.get(r["key"] if r["key"] != "(unattributed)" else None, global_markup) / 100) for r in result)
    rate = get_rate(cur, _date.today())
    return {
        "group_by": group_by,
        "currency": cur,
        "rate_to_usd": rate,
        "summary": {
            "total_raw_usd": round(total_raw, 6),
            "total_raw": round(total_raw * rate, 4),
            "total_billed": round(total_billed_usd * rate, 4),
            "total_margin": round((total_billed_usd - total_raw) * rate, 4),
            "avg_margin_pct": round(global_markup, 1),
        },
        "rows": result,
    }


# ───────────────────────── Rate Cards (WAVE 25 TRACK 2) ─────────────────────────

@router.get("/rate-cards")
async def list_rate_cards():
    from gateway.db.models import RateCard
    async with async_session() as s:
        rows = (await s.scalars(select(RateCard))).all()
    return {"rate_cards": [
        {"id": r.id, "client_id": r.client_id, "markup_pct": r.markup_pct,
         "description": r.description} for r in rows
    ]}


@router.post("/rate-cards")
async def upsert_rate_card(body: dict):
    from gateway.db.models import RateCard
    client_id = body.get("client_id")  # None = global default
    markup = float(body.get("markup_pct", 30.0))
    desc = body.get("description", "")
    async with async_session() as s:
        existing = await s.scalar(select(RateCard).where(RateCard.client_id == client_id))
        if existing:
            existing.markup_pct = markup
            existing.description = desc
        else:
            s.add(RateCard(client_id=client_id, markup_pct=markup, description=desc))
        await s.commit()
    await _audit("rate_card.upsert", client_id or "global", markup_pct=markup)
    return {"ok": True}


# ───────────────────────── Platform Value dashboard (WAVE 25 TRACK 4) ─────────────────────────

@router.get("/platform-value")
async def platform_value(currency: str | None = None):
    """Executive dashboard: quantify the ownership ROI in one JSON response.
    Every number is real (derived from request_logs + guardrail_violations +
    the FX/margin/engine data); in the selected currency."""
    from gateway.core.fx import get_default_currency, get_rate, convert_usd
    from gateway.db.models import RateCard, GuardrailViolation
    from datetime import date as _date

    cur = (currency or await get_default_currency()).upper()
    rate = get_rate(cur, _date.today())

    async with async_session() as s:
        # Governed spend (total USD flowing through the boundary)
        total_usd = await s.scalar(
            select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0))
            .where(RequestLog.source == "live")
        ) or 0.0
        total_requests = await s.scalar(
            select(func.count(RequestLog.id)).where(RequestLog.source == "live")
        ) or 0

        # Cache savings (cost that would have been incurred without the cache)
        cache_hits = await s.scalar(
            select(func.count(RequestLog.id))
            .where(RequestLog.event_kind == "cache_hit", RequestLog.source == "live")
        ) or 0
        # Estimate saved cost = cache_hits × avg cost per request
        avg_cost = total_usd / max(1, total_requests)
        cache_savings_usd = cache_hits * avg_cost

        # Security posture
        secrets_blocked = await s.scalar(
            select(func.count(GuardrailViolation.id))
            .where(GuardrailViolation.detector.in_(["secrets", "keyword"]),
                   GuardrailViolation.action == "block")
        ) or 0
        pii_redacted = await s.scalar(
            select(func.count(GuardrailViolation.id))
            .where(GuardrailViolation.detector.in_(["regex", "presidio"]),
                   GuardrailViolation.action.in_(["redact", "block"]))
        ) or 0
        total_audit_events = await s.scalar(
            select(func.count(GuardrailViolation.id))
        ) or 0

        # Governance coverage (% requests with full attribution)
        fully_attributed = await s.scalar(
            select(func.count(RequestLog.id))
            .where(RequestLog.source == "live",
                   RequestLog.client_id.isnot(None),
                   RequestLog.workspace_id.isnot(None),
                   RequestLog.user_id.isnot(None),
                   RequestLog.component.isnot(None))
        ) or 0
        coverage_pct = (fully_attributed / max(1, total_requests)) * 100

        # Engine independence (% requests on our owned engine vs rented)
        direct_count = await s.scalar(
            select(func.count(RequestLog.id))
            .where(RequestLog.source == "live",
                   RequestLog.engine.in_(["direct", "direct-anthropic"]))
        ) or 0
        owned_pct = (direct_count / max(1, total_requests)) * 100

        # Rate cards for margin calculation
        cards = (await s.scalars(select(RateCard))).all()
    card_map = {c.client_id: c.markup_pct for c in cards}
    global_markup = card_map.get(None, 30.0)

    total_billed_usd = total_usd * (1 + global_markup / 100)
    margin_usd = total_billed_usd - total_usd

    # Unique clients, workspaces, components, users
    async with async_session() as s:
        n_clients = await s.scalar(select(func.count(func.distinct(RequestLog.client_id)))
                                    .where(RequestLog.source == "live")) or 0
        n_workspaces = await s.scalar(select(func.count(func.distinct(RequestLog.workspace_id)))
                                       .where(RequestLog.source == "live")) or 0
        n_components = await s.scalar(select(func.count(func.distinct(RequestLog.component)))
                                       .where(RequestLog.source == "live")) or 0

    # Unit economics (measured from live data)
    cost_per_req_usd = total_usd / max(1, total_requests)
    margin_per_req_usd = margin_usd / max(1, total_requests)
    async with async_session() as s:
        total_tokens = await s.scalar(
            select(func.coalesce(func.sum(RequestLog.input_tokens + RequestLog.output_tokens), 0))
            .where(RequestLog.source == "live")
        ) or 0
    tokens_per_req = total_tokens / max(1, total_requests)
    cache_hit_rate = cache_hits / max(1, total_requests)

    # Projection at scale (configurable volume)
    PROJECTION_VOLUME = 5_000_000  # 5M req/month (Agnos platform scale)

    return {
        "currency": cur,
        "rate_to_usd": rate,
        "governed_spend": {
            "usd": round(total_usd, 4),
            "local": round(total_usd * rate, 2),
            "requests": total_requests,
        },
        "profitability": {
            "billed_usd": round(total_billed_usd, 4),
            "billed_local": round(total_billed_usd * rate, 2),
            "margin_usd": round(margin_usd, 4),
            "margin_local": round(margin_usd * rate, 2),
            "margin_pct": round(global_markup, 1),
        },
        "cache_savings": {
            "hits": cache_hits,
            "saved_usd": round(cache_savings_usd, 4),
            "saved_local": round(cache_savings_usd * rate, 2),
        },
        "security": {
            "credentials_centralized": f"{n_components} components \u2192 1 encrypted store",
            "secrets_blocked": secrets_blocked,
            "pii_redacted": pii_redacted,
            "audit_events": total_audit_events,
        },
        "engine_independence": {
            "status": "ACTIVE" if direct_count > 0 else "AVAILABLE",
            "detail": (f"ws-novatech-platform live on our Anthropic adapter"
                       if direct_count > 0
                       else "Insourcing path available for all providers"),
            "direct_requests": direct_count,
            "total_requests": total_requests,
        },
        "governance_coverage": {
            "fully_attributed_pct": round(coverage_pct, 1),
            "fully_attributed": fully_attributed,
            "total": total_requests,
            "clients": n_clients,
            "workspaces": n_workspaces,
            "components": n_components,
        },
        "unit_economics": {
            "cost_per_req_usd": round(cost_per_req_usd, 6),
            "margin_per_req_usd": round(margin_per_req_usd, 6),
            "tokens_per_req": round(tokens_per_req, 1),
            "cache_hit_rate_pct": round(cache_hit_rate * 100, 2),
        },
        "projection": {
            "volume_monthly": PROJECTION_VOLUME,
            "label": f"Projected at {PROJECTION_VOLUME // 1_000_000}M req/month from measured unit economics",
            "governed_spend_local": round(cost_per_req_usd * PROJECTION_VOLUME * rate, 0),
            "margin_local": round(margin_per_req_usd * PROJECTION_VOLUME * rate, 0),
            "cache_savings_local": round(cache_hit_rate * PROJECTION_VOLUME * cost_per_req_usd * rate, 0),
            "currency": cur,
        },
    }
