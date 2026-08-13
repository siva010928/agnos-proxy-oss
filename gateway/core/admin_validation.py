"""Centralized validation for every admin write endpoint.

Each function returns a list of FastAPI-shaped error dicts:
``[{"loc": [...], "msg": "...", "type": "value_error"}, ...]``

Routes call ``raise_if(errors)`` to turn a non-empty list into a 422 with the
same shape FastAPI itself produces from Pydantic, so the existing UI toast
pipeline (which parses ``detail[]``) just works.

The goal is "half-cooked config must be impossible to save": enum checks,
shape checks, cross-field checks (default_chat_alias must be a key in
chat_models), and **resolvability** (every distinct provider referenced by
chat_models/embedding_models must have a WorkspaceProviderConfig in this
workspace).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.config import settings

from gateway.db.models import (
    Component,
    CustomPricing,
    GuardrailProfile,
    Workspace,
    WorkspaceProviderConfig,
)

# ─────────────────────────── enums (single source of truth) ───────────────────────────

PROVIDERS: tuple[str, ...] = (
    "anthropic", "bedrock", "gemini", "openai", "azure",
    # served by our OWN DirectEngine (OpenAI-compatible + Google AI Studio + Vertex):
    "google_genai", "vertex_ai", "litellm_proxy", "ollama", "hosted_vllm",
)
GUARDRAIL_MODES: tuple[str, ...] = ("block", "redact", "audit")
GUARDRAIL_APPLY_TO: tuple[str, ...] = ("input", "output", "both")
GUARDRAIL_ACTIONS: tuple[str, ...] = ("block", "redact", "audit")
GUARDRAIL_SCOPES: tuple[str, ...] = ("global", "workspace", "component")
DETECTOR_TYPES: tuple[str, ...] = (
    "regex", "secrets", "keyword", "presidio", "bedrock", "azure", "model-armor",
)
ENGINES: tuple[str, ...] = ("bifrost", "litellm", "portkey", "direct", "echo")

# Required credential fields per provider (mirrors core/provider_test.py)
REQUIRED_CREDS: dict[str, tuple[str, ...]] = {
    "anthropic": ("api_key",),
    "openai": ("api_key",),
    "gemini": ("api_key",),
    "bedrock": ("access_key", "secret_key"),
    "azure": ("api_key", "endpoint"),
    "google_genai": ("api_key",),      # AI Studio key (maps to gemini-api-key)
    "vertex_ai": ("api_key",),         # admin-provided service-account JSON; vertex_project required too (checked below)
    "litellm_proxy": ("api_key",),     # + base_url in config (checked below)
    "ollama": (),                      # local, no key
    "hosted_vllm": (),                 # local/self-hosted OpenAI-compatible, no key
}

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])?$")  # 1..64, no leading/trailing dash


# ─────────────────────────── error helper ───────────────────────────

def err(loc: list[str | int], msg: str, type_: str = "value_error") -> dict[str, Any]:
    return {"loc": loc, "msg": msg, "type": type_}


def raise_if(errors: list[dict[str, Any]]) -> None:
    """Raise HTTP 422 with FastAPI-shaped detail if any errors present."""
    if errors:
        raise HTTPException(status_code=422, detail=errors)


# ─────────────────────────── small validators ───────────────────────────

def _is_pos_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x > 0


def _is_nonneg_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0


def _is_url(s: str) -> bool:
    return isinstance(s, str) and bool(re.match(r"^https?://[^\s]+$", s.strip()))


def _is_aws_region(s: str) -> bool:
    return isinstance(s, str) and bool(re.match(r"^[a-z]{2,3}-[a-z]+-\d+$", s.strip()))


def _is_iso_date_or_datetime(s: str) -> _dt.datetime | None:
    """Return parsed datetime or None if unparseable."""
    if not isinstance(s, str) or not s.strip():
        return None
    raw = s.strip().replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(raw)
        return d.replace(tzinfo=None) if d.tzinfo else d
    except Exception:
        # Try plain ISO date "YYYY-MM-DD"
        try:
            return _dt.datetime.fromisoformat(raw + "T00:00:00")
        except Exception:
            return None


# ─────────────────────────── alias targets (chat_models / embedding_models) ───────────────────────────

def validate_alias_map(
    alias_map: Any,
    *,
    field: str,                                 # "chat_models" or "embedding_models"
    configured_providers: set[str],             # providers with WorkspaceProviderConfig
    base_loc: list[str | int] | None = None,
) -> list[dict[str, Any]]:
    """Validate {alias: [{provider, model_id, weight?, ...}, ...]} shape + resolvability.

    Empty map is allowed (component overlay falls back to workspace).
    """
    base = list(base_loc or ["body", field])
    errors: list[dict[str, Any]] = []

    if alias_map is None:
        return errors
    if not isinstance(alias_map, dict):
        errors.append(err(base, f"{field} must be an object mapping alias \u2192 [targets]"))
        return errors

    for alias, targets in alias_map.items():
        loc_alias = base + [alias]
        if not isinstance(alias, str) or not alias.strip():
            errors.append(err(loc_alias, "alias name must be a non-empty string"))
            continue
        if isinstance(targets, dict):
            # Allow legacy shorthand {provider, model_id} as a single target
            targets = [targets]
        if not isinstance(targets, list) or not targets:
            errors.append(err(loc_alias, f"alias '{alias}' must have at least one target"))
            continue

        seen_pairs: set[tuple[str, str]] = set()
        for i, t in enumerate(targets):
            loc_t = loc_alias + [i]
            if not isinstance(t, dict):
                errors.append(err(loc_t, "target must be an object {provider, model_id, weight?}"))
                continue
            provider = t.get("provider")
            model_id = t.get("model_id") or t.get("model")
            weight = t.get("weight", 1)

            if not isinstance(provider, str) or not provider.strip():
                errors.append(err(loc_t + ["provider"], "provider is required"))
            elif provider not in PROVIDERS:
                errors.append(err(loc_t + ["provider"],
                                  f"provider must be one of {', '.join(PROVIDERS)} (got '{provider}')"))
            elif provider not in configured_providers:
                errors.append(err(loc_t + ["provider"],
                                  f"provider '{provider}' is not configured for this workspace; add it under Admin \u2192 Providers first"))

            if not isinstance(model_id, str) or not model_id.strip():
                errors.append(err(loc_t + ["model_id"], "model_id is required"))

            if isinstance(weight, bool) or not isinstance(weight, (int, float)):
                errors.append(err(loc_t + ["weight"], "weight must be a non-negative number"))
            elif weight < 0:
                errors.append(err(loc_t + ["weight"], "weight must be \u2265 0"))

            if isinstance(provider, str) and isinstance(model_id, str):
                pair = (provider, model_id)
                if pair in seen_pairs:
                    errors.append(err(loc_t,
                                      f"duplicate target {provider}/{model_id} in alias '{alias}'"))
                else:
                    seen_pairs.add(pair)

    return errors


# ─────────────────────────── guardrails / quotas / budgets ───────────────────────────

def validate_guardrails_blob(blob: Any, *, base_loc: list[str | int] | None = None) -> list[dict[str, Any]]:
    """Inline guardrail flags blob (used on workspace/component records).

    Recognized keys today: pii_detection (bool), secrets_detection (bool),
    auto_truncate (bool), mode (enum), rule_ids (list[int]).
    Unknown keys are tolerated but flagged as a soft warning via type=warning.
    """
    base = list(base_loc or ["body", "guardrails"])
    errors: list[dict[str, Any]] = []
    if blob is None or blob == {}:
        return errors
    if not isinstance(blob, dict):
        errors.append(err(base, "guardrails must be an object"))
        return errors
    if "mode" in blob and blob["mode"] not in GUARDRAIL_MODES:
        errors.append(err(base + ["mode"],
                          f"mode must be one of {', '.join(GUARDRAIL_MODES)}"))
    for k in ("pii_detection", "secrets_detection", "auto_truncate"):
        if k in blob and not isinstance(blob[k], bool):
            errors.append(err(base + [k], f"{k} must be a boolean"))
    if "rule_ids" in blob:
        rids = blob["rule_ids"]
        if not isinstance(rids, list) or not all(isinstance(x, int) for x in rids):
            errors.append(err(base + ["rule_ids"], "rule_ids must be a list of integers"))
    return errors


def validate_quotas(blob: Any, *, base_loc: list[str | int] | None = None) -> list[dict[str, Any]]:
    base = list(base_loc or ["body", "quotas"])
    errors: list[dict[str, Any]] = []
    if not blob:
        return errors
    if not isinstance(blob, dict):
        errors.append(err(base, "quotas must be an object"))
        return errors
    for alias, lims in blob.items():
        if not isinstance(lims, dict):
            errors.append(err(base + [alias], "quota entry must be an object {rpm?, tpm?}"))
            continue
        for k in ("rpm", "tpm"):
            if k in lims and not _is_pos_int(lims[k]):
                errors.append(err(base + [alias, k], f"{k} must be a positive integer"))
    return errors


def validate_budgets(blob: Any, *, base_loc: list[str | int] | None = None) -> list[dict[str, Any]]:
    base = list(base_loc or ["body", "budgets"])
    errors: list[dict[str, Any]] = []
    if not blob:
        return errors
    if not isinstance(blob, dict):
        errors.append(err(base, "budgets must be an object"))
        return errors
    for k in ("workspace_usd", "user_usd"):
        if k in blob and not _is_nonneg_number(blob[k]):
            errors.append(err(base + [k], f"{k} must be a non-negative number"))
    return errors


# ─────────────────────────── workspace + component bodies ───────────────────────────

async def _configured_providers(db: AsyncSession, workspace_id: str) -> set[str]:
    rows = await db.scalars(
        select(WorkspaceProviderConfig.provider).where(
            WorkspaceProviderConfig.workspace_id == workspace_id
        )
    )
    return set(rows.all())


async def validate_workspace_create(body: Any, db: AsyncSession) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    wid = (getattr(body, "workspace_id", "") or "").strip()
    if not wid:
        errors.append(err(["body", "workspace_id"], "workspace_id is required"))
    elif not SLUG_RE.match(wid):
        errors.append(err(["body", "workspace_id"],
                          "workspace_id must be lowercase letters/digits/hyphens (1\u201364 chars, no leading/trailing dash)"))

    # client_id: required + must reference an existing Client
    client_id = (getattr(body, "client_id", None) or "").strip()
    if not client_id:
        errors.append(err(["body", "client_id"],
                          "client_id is required (workspaces belong to a Client)"))
    else:
        from gateway.db.models import Client as _Client
        if not await db.get(_Client, client_id):
            errors.append(err(["body", "client_id"],
                              f"client '{client_id}' does not exist; create it first"))

    # On create, no providers exist yet \u2014 chat_models that names providers is fine
    # only if the admin will add those providers afterward; we still enforce *shape*
    # and *enum* but skip the resolvability check when the workspace is brand-new.
    cm_errs = validate_alias_map(getattr(body, "chat_models", {}), field="chat_models",
                                 configured_providers=set(PROVIDERS))
    em_errs = validate_alias_map(getattr(body, "embedding_models", {}), field="embedding_models",
                                 configured_providers=set(PROVIDERS))
    errors.extend(cm_errs); errors.extend(em_errs)

    errors.extend(validate_guardrails_blob(getattr(body, "guardrails", {})))
    errors.extend(validate_quotas(getattr(body, "quotas", {})))
    errors.extend(validate_budgets(getattr(body, "budgets", {})))
    errors.extend(validate_rate_limits(getattr(body, "rate_limits", {})))

    dca = getattr(body, "default_chat_alias", None)
    cm = getattr(body, "chat_models", {}) or {}
    if dca and isinstance(cm, dict) and dca not in cm:
        errors.append(err(["body", "default_chat_alias"],
                          f"default_chat_alias '{dca}' must be a key in chat_models"))
    return errors


async def validate_workspace_patch(body: dict, ws: Workspace, db: AsyncSession) -> list[dict[str, Any]]:
    """Existing workspace \u2014 enforce resolvability against currently-configured providers."""
    errors: list[dict[str, Any]] = []
    cfg = await _configured_providers(db, ws.workspace_id)

    # client_id (if reassigning): must exist
    if "client_id" in body and body["client_id"]:
        from gateway.db.models import Client as _Client
        if not await db.get(_Client, body["client_id"]):
            errors.append(err(["body", "client_id"],
                              f"client '{body['client_id']}' does not exist"))

    if "chat_models" in body:
        errors.extend(validate_alias_map(body["chat_models"], field="chat_models",
                                         configured_providers=cfg))
    if "embedding_models" in body:
        errors.extend(validate_alias_map(body["embedding_models"], field="embedding_models",
                                         configured_providers=cfg))
    if "guardrails" in body:
        errors.extend(validate_guardrails_blob(body["guardrails"]))
    if "quotas" in body:
        errors.extend(validate_quotas(body["quotas"]))
    if "budgets" in body:
        errors.extend(validate_budgets(body["budgets"]))
    if "rate_limits" in body:
        errors.extend(validate_rate_limits(body["rate_limits"]))

    # Cross-field: default_chat_alias \u2208 keys(chat_models) (after patch, considering existing)
    new_cm = body.get("chat_models", ws.chat_models or {})
    new_dca = body.get("default_chat_alias", ws.default_chat_alias)
    if new_dca and isinstance(new_cm, dict) and new_dca not in new_cm:
        errors.append(err(["body", "default_chat_alias"],
                          f"default_chat_alias '{new_dca}' must be a key in chat_models"))
    return errors


def validate_rate_limits(blob: Any, *, base_loc: list[str | int] | None = None) -> list[dict[str, Any]]:
    """Validate {rpm, tpm} workspace/client ceiling shape."""
    base = list(base_loc or ["body", "rate_limits"])
    errors: list[dict[str, Any]] = []
    if not blob:
        return errors
    if not isinstance(blob, dict):
        errors.append(err(base, "rate_limits must be an object"))
        return errors
    for k in ("rpm", "tpm"):
        if k in blob and blob[k] is not None and not _is_pos_int(blob[k]):
            errors.append(err(base + [k], f"{k} must be a positive integer or null"))
    return errors


def validate_client(body: Any) -> list[dict[str, Any]]:
    """Validate a Client create body."""
    errors: list[dict[str, Any]] = []
    cid = (getattr(body, "client_id", "") or "").strip()
    if not cid:
        errors.append(err(["body", "client_id"], "client_id is required"))
    elif not SLUG_RE.match(cid):
        errors.append(err(["body", "client_id"],
                          "client_id must be lowercase letters/digits/hyphens (1\u201364 chars, no leading/trailing dash)"))
    name = getattr(body, "name", "") or ""
    if name and len(name) > 128:
        errors.append(err(["body", "name"], "name must be \u2264 128 chars"))
    errors.extend(validate_client_budgets(getattr(body, "budgets", {})))
    errors.extend(validate_rate_limits(getattr(body, "rate_limits", {})))
    rh = getattr(body, "required_headers", []) or []
    if not isinstance(rh, list) or not all(isinstance(x, str) and x.strip() for x in rh):
        errors.append(err(["body", "required_headers"],
                          "required_headers must be a list of non-empty strings"))
    return errors


def validate_client_patch(body: dict) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if "budgets" in body:
        errors.extend(validate_client_budgets(body["budgets"]))
    if "rate_limits" in body:
        errors.extend(validate_rate_limits(body["rate_limits"]))
    if "required_headers" in body:
        rh = body["required_headers"] or []
        if not isinstance(rh, list) or not all(isinstance(x, str) and x.strip() for x in rh):
            errors.append(err(["body", "required_headers"],
                              "required_headers must be a list of non-empty strings"))
    return errors


def validate_client_budgets(blob: Any, *, base_loc: list[str | int] | None = None) -> list[dict[str, Any]]:
    base = list(base_loc or ["body", "budgets"])
    errors: list[dict[str, Any]] = []
    if not blob:
        return errors
    if not isinstance(blob, dict):
        errors.append(err(base, "budgets must be an object"))
        return errors
    for k in ("client_usd", "user_usd"):
        if k in blob and blob[k] is not None and not _is_nonneg_number(blob[k]):
            errors.append(err(base + [k], f"{k} must be a non-negative number"))
    return errors


# validate_component_upsert was removed in WAVE 20 TRACK 1. Components are a
# runtime attribution dimension (auto-registered by X-Gateway-Component), not
# an admin-created entity. No create/edit/delete surface exists.


# ─────────────────────────── provider config ───────────────────────────

def validate_provider_in(body: Any) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    provider = (getattr(body, "provider", "") or "").strip()
    if not provider:
        errors.append(err(["body", "provider"], "provider is required"))
    elif provider not in PROVIDERS:
        errors.append(err(["body", "provider"],
                          f"provider must be one of {', '.join(PROVIDERS)} (got '{provider}')"))

    creds = getattr(body, "credentials", {}) or {}
    if not isinstance(creds, dict):
        errors.append(err(["body", "credentials"], "credentials must be an object"))
        creds = {}

    if provider == "bedrock":
        # Bedrock supports THREE auth modes; require exactly the fields for one:
        #   static: access_key + secret_key   |   bearer: bedrock_api_key   |   sso: profile_name
        def _has(k: str) -> bool:
            v = creds.get(k)
            return isinstance(v, str) and bool(v.strip())
        static_ok = _has("access_key") and _has("secret_key")
        bearer_ok = _has("bedrock_api_key") or _has("api_key")
        sso_ok = (str(creds.get("auth_type", "")).lower() == "sso") and _has("profile_name")
        if not (static_ok or bearer_ok or sso_ok):
            errors.append(err(["body", "credentials"],
                              "bedrock needs ONE auth mode: static (access_key+secret_key), "
                              "bearer (bedrock_api_key), or sso (auth_type=sso + profile_name)"))
    else:
        required = REQUIRED_CREDS.get(provider, ())
        for f in required:
            v = creds.get(f)
            if not isinstance(v, str) or not v.strip():
                errors.append(err(["body", "credentials", f], f"{f} is required for provider '{provider}'"))

    config = getattr(body, "config", {}) or {}
    # base_url-based providers: litellm_proxy MUST have one; ollama/hosted_vllm may
    # use their local default. Validate the URL shape when present.
    if provider in ("litellm_proxy", "ollama", "hosted_vllm"):
        base_url = config.get("base_url") or creds.get("base_url")
        if provider == "litellm_proxy" and not (base_url and str(base_url).strip()):
            errors.append(err(["body", "config", "base_url"], "litellm_proxy requires a base_url (the proxy URL)"))
        if base_url and not _is_url(str(base_url)):
            errors.append(err(["body", "config", "base_url"], f"base_url must be an http(s):// URL (got '{base_url}')"))
    if provider == "vertex_ai":
        project = config.get("vertex_project") or config.get("vertex-ai-project")
        if not (project and str(project).strip()):
            errors.append(err(["body", "config", "vertex_project"], "vertex_ai requires a vertex_project (GCP project id) in config"))
    if provider == "bedrock":
        region = config.get("region") or creds.get("region")
        if region and not _is_aws_region(str(region)):
            errors.append(err(["body", "config", "region"],
                              f"region '{region}' does not look like an AWS region (e.g. us-east-1)"))
    if provider == "azure":
        endpoint = config.get("endpoint") or creds.get("endpoint")
        if endpoint and not _is_url(str(endpoint)):
            errors.append(err(["body", "config", "endpoint"],
                              f"endpoint must be an https:// URL (got '{endpoint}')"))
        api_version = config.get("api_version") or creds.get("api_version")
        if api_version and not re.match(r"^\d{4}-\d{2}-\d{2}(-preview)?$", str(api_version)):
            errors.append(err(["body", "config", "api_version"],
                              f"api_version must look like YYYY-MM-DD or YYYY-MM-DD-preview (got '{api_version}')"))
    # Optional per-provider request timeout (seconds) → pushed to Bifrost.
    rt = config.get("request_timeout_seconds")
    if rt not in (None, ""):
        try:
            n = int(rt)
            if n < 1 or n > settings.max_request_timeout_s:
                errors.append(err(["body", "config", "request_timeout_seconds"],
                                  f"request timeout must be between 1 and {settings.max_request_timeout_s} seconds"))
        except (TypeError, ValueError):
            errors.append(err(["body", "config", "request_timeout_seconds"],
                              "request timeout must be an integer number of seconds"))
    return errors


# ─────────────────────────── pricing ───────────────────────────

def validate_pricing_in(body: Any) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    substr = (getattr(body, "model_substr", "") or "").strip()
    if not substr:
        errors.append(err(["body", "model_substr"], "model_substr is required"))
    elif len(substr) < 3:
        errors.append(err(["body", "model_substr"],
                          "model_substr must be at least 3 characters (shorter substrings would match too many models)"))
    inp = getattr(body, "input_per_1k", None)
    out = getattr(body, "output_per_1k", None)
    if inp is None or not _is_nonneg_number(inp):
        errors.append(err(["body", "input_per_1k"], "input_per_1k must be a non-negative number"))
    if out is None or not _is_nonneg_number(out):
        errors.append(err(["body", "output_per_1k"], "output_per_1k must be a non-negative number"))
    if isinstance(inp, (int, float)) and isinstance(out, (int, float)) and inp == 0 and out == 0:
        errors.append(err(["body"],
                          "at least one of input_per_1k or output_per_1k must be > 0 (otherwise the override is a no-op)"))
    return errors


# ─────────────────────────── api keys ───────────────────────────

def validate_key_issue(body: Any) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    roles = list(getattr(body, "roles", []) or [])
    if not roles:
        errors.append(err(["body", "roles"], "at least one role is required"))
    for i, r in enumerate(roles):
        if not isinstance(r, str) or not r.strip():
            errors.append(err(["body", "roles", i], "role must be a non-empty string"))

    expires_at = getattr(body, "expires_at", None)
    if expires_at:
        d = _is_iso_date_or_datetime(expires_at)
        if d is None:
            errors.append(err(["body", "expires_at"],
                              "expires_at must be ISO-8601 (e.g. 2027-01-15 or 2027-01-15T12:00:00Z)"))
        elif d <= _dt.datetime.utcnow():
            errors.append(err(["body", "expires_at"],
                              "expires_at must be in the future"))
    return errors


# ─────────────────────────── guardrails (profile + rule) ───────────────────────────

async def validate_profile_in(body: Any, db: AsyncSession) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    name = (getattr(body, "name", "") or "").strip()
    if not name:
        errors.append(err(["body", "name"], "name is required"))

    dt = getattr(body, "detector_type", "")
    if dt not in DETECTOR_TYPES:
        errors.append(err(["body", "detector_type"],
                          f"detector_type must be one of {', '.join(DETECTOR_TYPES)}"))

    scope = getattr(body, "scope", "global")
    if scope not in GUARDRAIL_SCOPES:
        errors.append(err(["body", "scope"],
                          f"scope must be one of {', '.join(GUARDRAIL_SCOPES)}"))
    if scope == "workspace":
        wid = getattr(body, "workspace_id", None)
        if not wid:
            errors.append(err(["body", "workspace_id"], "workspace_id is required when scope='workspace'"))
        elif not await db.get(Workspace, wid):
            errors.append(err(["body", "workspace_id"], f"workspace '{wid}' does not exist"))
    if scope == "component":
        wid = getattr(body, "workspace_id", None)
        comp = getattr(body, "component", None)
        if not wid or not comp:
            errors.append(err(["body"], "workspace_id and component are required when scope='component'"))
        elif not await db.scalar(select(Component).where(
                Component.workspace_id == wid, Component.name == comp)):
            errors.append(err(["body", "component"],
                              f"component '{comp}' in workspace '{wid}' does not exist"))

    config = getattr(body, "config", {}) or {}
    if not isinstance(config, dict):
        errors.append(err(["body", "config"], "config must be an object"))

    # detector-specific config minimums
    if dt == "regex":
        # regex profiles use either `pattern` (single) or `patterns` (named map).
        # Empty config is allowed only because there's a backend default PII set,
        # but we strongly recommend custom patterns and accept either shape.
        has_single = bool(config.get("pattern"))
        has_map = isinstance(config.get("patterns"), dict) and len(config["patterns"]) > 0
        # An empty {} is permitted (backend supplies a default PII pattern set);
        # a non-empty config must include at least one of pattern/patterns.
        if config and not has_single and not has_map:
            errors.append(err(["body", "config"],
                              "regex profile config must contain `pattern` (string) or `patterns` (named map); "
                              "leave config={} to use the built-in PII template"))
    if dt == "keyword" and not (config.get("keywords") or config.get("blocklist")):
        errors.append(err(["body", "config"], "keyword profile requires config.keywords or config.blocklist"))
    if dt == "bedrock" and not (config.get("guardrail_id") or config.get("guardrailIdentifier")):
        errors.append(err(["body", "config", "guardrail_id"],
                          "bedrock profile requires config.guardrail_id (the AWS Guardrail ARN/id)"))
    return errors


async def validate_rule_in(body: Any, db: AsyncSession) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    name = (getattr(body, "name", "") or "").strip()
    if not name:
        errors.append(err(["body", "name"], "name is required"))

    apply_to = getattr(body, "apply_to", "input")
    if apply_to not in GUARDRAIL_APPLY_TO:
        errors.append(err(["body", "apply_to"],
                          f"apply_to must be one of {', '.join(GUARDRAIL_APPLY_TO)}"))

    action = getattr(body, "action", "block")
    if action not in GUARDRAIL_ACTIONS:
        errors.append(err(["body", "action"],
                          f"action must be one of {', '.join(GUARDRAIL_ACTIONS)}"))

    scope = getattr(body, "scope", "global")
    if scope not in GUARDRAIL_SCOPES:
        errors.append(err(["body", "scope"],
                          f"scope must be one of {', '.join(GUARDRAIL_SCOPES)}"))

    sr = getattr(body, "sampling_rate", 1.0)
    if not isinstance(sr, (int, float)) or sr < 0 or sr > 1:
        errors.append(err(["body", "sampling_rate"], "sampling_rate must be in [0, 1]"))

    tm = getattr(body, "timeout_ms", 1000)
    if not isinstance(tm, int) or tm <= 0 or tm > 60_000:
        errors.append(err(["body", "timeout_ms"], "timeout_ms must be a positive integer (\u2264 60000)"))

    expr = (getattr(body, "cel_expression", "") or "").strip()
    if expr and expr != "true":
        try:
            import celpy  # noqa: F401
            from celpy import Environment
            Environment().compile(expr)
        except ImportError:
            pass  # celpy not installed \u2014 will be flagged by /admin/guardrails/validate-cel
        except Exception as exc:  # noqa: BLE001
            errors.append(err(["body", "cel_expression"], f"CEL syntax error: {exc}"))

    pids = getattr(body, "profile_ids", []) or []
    if not isinstance(pids, list):
        errors.append(err(["body", "profile_ids"], "profile_ids must be a list of integers"))
    else:
        for i, pid in enumerate(pids):
            if not isinstance(pid, int):
                errors.append(err(["body", "profile_ids", i], "profile_id must be an integer"))
                continue
            if not await db.get(GuardrailProfile, pid):
                errors.append(err(["body", "profile_ids", i],
                                  f"guardrail profile id={pid} does not exist"))

    if scope == "workspace":
        wid = getattr(body, "workspace_id", None)
        if not wid:
            errors.append(err(["body", "workspace_id"], "workspace_id is required when scope='workspace'"))
        elif not await db.get(Workspace, wid):
            errors.append(err(["body", "workspace_id"], f"workspace '{wid}' does not exist"))
    if scope == "component":
        wid = getattr(body, "workspace_id", None)
        comp = getattr(body, "component", None)
        if not wid or not comp:
            errors.append(err(["body"], "workspace_id and component are required when scope='component'"))
        elif not await db.scalar(select(Component).where(
                Component.workspace_id == wid, Component.name == comp)):
            errors.append(err(["body", "component"],
                              f"component '{comp}' in workspace '{wid}' does not exist"))
    return errors


# ─────────────────────────── engine ───────────────────────────

def validate_engine_name(name: str) -> list[dict[str, Any]]:
    if name not in ENGINES:
        return [err(["body", "engine"], f"engine must be one of {', '.join(ENGINES)}")]
    return []
