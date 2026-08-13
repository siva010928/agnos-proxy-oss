"""Seed the NovaTech client + its client-team workspaces (WAVE 19 model).

Tenancy:  **Client \u2192 Workspace(client team) \u2192 Component(attribution) + User(JWT sub)**.

  * `Client` = an enterprise tenant (the cross-workspace budget cap).
  * `Workspace` = a CLIENT TEAM (Payments, Platform, Knowledge), the isolation +
    config unit.  Provider creds, model aliases, guardrails, RPM/TPM, budget,
    api key/JWT live here.
  * `Component` = one of the platform's fixed apps (`document-processing`,
    `code-generation`, `search-index`, `data-pipeline`, `report-engine`,
    `guardrails`).  Set per request via `X-Gateway-Component`; ATTRIBUTION ONLY.
  * `User` = `sub` from the workspace-scoped JWT; ATTRIBUTION + optional
    per-user budget.  Never CRUD'd in the gateway.

Seed contents (fresh DB or wiped on `reseed_to_wave19_model()`):

    Client `novatech` ("NovaTech", $5000/mo cross-workspace cap)
      \u251c\u2500 Workspace `ws-novatech-payments`    ("NovaTech \u2014 Payments")
      \u251c\u2500 Workspace `ws-novatech-platform`    ("NovaTech \u2014 Platform Engineering")
      \u251c\u2500 Workspace `ws-novatech-knowledge`   ("NovaTech \u2014 Knowledge")
      \u251c\u2500 Workspace `ws-novatech-guardrails`  ("NovaTech \u2014 Guardrails QA")       [QA]
      \u2514\u2500 Workspace `ws-novatech-presidio`    ("NovaTech \u2014 Presidio PII")         [QA]

Components are NOT seeded as DB rows; they're a fixed runtime enum.

Idempotent.  `reseed_to_wave19_model()` is the explicit migration path: wipes
all old workspaces (including the legacy `ws-legacy` etc. naming) and
seeds the NovaTech tenancy in place.  Cold-start `seed()` only seeds when the DB
is empty.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import delete, select

from gateway.config import settings
from gateway.db.database import async_session
from gateway.db.models import (
    ApiKey, Client, Component, GuardrailProfile, GuardrailRule,
    Workspace, WorkspaceProviderConfig,
)
from gateway.secrets.store import cipher

# ── real provider model ids we actually route to ──
BEDROCK_CLAUDE = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
ANTHROPIC_CLAUDE = "claude-sonnet-4-5-20250929"
GEMINI_FLASH = "gemini-2.5-flash"
TITAN_EMBED = "amazon.titan-embed-text-v2:0"
GEMINI_EMBED = "text-embedding-004"

# The fixed component enum (NOT DB-managed; sent via X-Gateway-Component).
KNOWN_COMPONENTS: tuple[str, ...] = (
    "document-processing",
    "code-generation",
    "search-index",
    "data-pipeline",
    "report-engine",
    "guardrails",
)

ACME_CLIENT_ID = "novatech"
ACME_CLIENT_NAME = "NovaTech"

# ── client-team workspaces under NovaTech ──
# Each carries: display name, chat alias chain, embedding default, guardrails,
# rate-limits (workspace ceiling), budget. Components reach these workspaces
# via the workspace key + X-Gateway-Component header.
WORKSPACES: dict[str, dict] = {
    "ws-novatech-payments": {
        "display_name": "NovaTech \u2014 Payments",
        "alias": "claude-sonnet-4-5",
        "chat": [("bedrock", BEDROCK_CLAUDE), ("anthropic", ANTHROPIC_CLAUDE)],
        "embedding": ("bedrock", TITAN_EMBED),
        "guardrails": {"pii_detection": True, "secrets_detection": True, "mode": "block"},
        "quotas": {"rpm": 600, "tpm": 800_000},
        "budgets": {"workspace_usd": 2000.0, "user_usd": 500.0},
        "rate_limits": {"rpm": 600, "tpm": 800_000},
    },
    "ws-novatech-platform": {
        "display_name": "NovaTech \u2014 Platform Engineering",
        "alias": "claude-sonnet-4-5",
        "chat": [("anthropic", ANTHROPIC_CLAUDE), ("bedrock", BEDROCK_CLAUDE)],
        "embedding": ("bedrock", TITAN_EMBED),
        # Demo-friendly: both detectors on so the playground guardrail scenarios
        # (phone/email/ssn/aws_secret) all visibly trigger.
        "guardrails": {"secrets_detection": True, "pii_detection": True, "mode": "block"},
        "quotas": {"rpm": 400, "tpm": 600_000},
        "budgets": {"workspace_usd": 1500.0, "user_usd": 400.0},
        "rate_limits": {"rpm": 400, "tpm": 600_000},
    },
    "ws-novatech-knowledge": {
        "display_name": "NovaTech \u2014 Knowledge",
        "alias": "gemini-flash",
        "chat": [("gemini", GEMINI_FLASH), ("bedrock", BEDROCK_CLAUDE)],
        "embedding": ("gemini", GEMINI_EMBED),
        "guardrails": {"pii_detection": True, "mode": "redact"},
        "quotas": {"rpm": 800, "tpm": 1_200_000},
        "budgets": {"workspace_usd": 800.0, "user_usd": 200.0},
        "rate_limits": {"rpm": 800, "tpm": 1_200_000},
    },
    "ws-novatech-guardrails": {
        "display_name": "NovaTech \u2014 Guardrails QA",
        "alias": "claude-sonnet-4-5",
        "chat": [("bedrock", BEDROCK_CLAUDE)],
        "embedding": ("bedrock", TITAN_EMBED),
        "guardrails": {"secrets_detection": True, "keywords": ["projectphoenix", "confidential"],
                        "mode": "block"},
        "quotas": {"rpm": 100_000, "tpm": 100_000_000},
        "budgets": {},
        "rate_limits": {"rpm": 100_000, "tpm": 100_000_000},
    },
    "ws-novatech-presidio": {
        "display_name": "NovaTech \u2014 Presidio PII",
        "alias": "claude-sonnet-4-5",
        "chat": [("bedrock", BEDROCK_CLAUDE)],
        "embedding": ("bedrock", TITAN_EMBED),
        "guardrails": {"presidio": True, "mode": "redact"},
        "quotas": {"rpm": 100_000, "tpm": 100_000_000},
        "budgets": {},
        "rate_limits": {"rpm": 100_000, "tpm": 100_000_000},
    },
}

# Demo plaintext keys mapped to workspace ids.
WS_KEYS: dict[str, str] = {
    "ws-novatech-payments":    settings.ws_key_secondary,
    "ws-novatech-platform":    settings.ws_key_primary,
    "ws-novatech-knowledge":   settings.ws_key_gemini,
    "ws-novatech-guardrails":  "gw-key-guarddepth-001",
    "ws-novatech-presidio":    "gw-key-presidio-001",
}


def hash_key(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def _chat_models(spec: dict) -> dict:
    targets = [{"provider": p, "model_id": m,
                "context_window": 1_000_000 if p == "gemini" else 200_000}
               for (p, m) in spec["chat"]]
    return {spec["alias"]: targets}


def _embedding_models(spec: dict) -> dict:
    p, m = spec["embedding"]
    return {"text-embedding-default": [{"provider": p, "model_id": m}]}


def _provider_creds(provider: str) -> dict:
    if provider == "bedrock":
        return {"access_key": settings.aws_access_key_id or "",
                "secret_key": settings.aws_secret_access_key or "",
                "region": settings.aws_region_name}
    if provider == "anthropic":
        return {"api_key": settings.anthropic_api_key or ""}
    if provider == "gemini":
        return {"api_key": settings.gemini_api_key or ""}
    if provider == "openai":
        return {"api_key": settings.openai_api_key or ""}
    return {}


def _provider_config(provider: str, spec: dict) -> dict:
    aliases: dict[str, str] = {}
    for (p, m) in spec["chat"]:
        if p == provider:
            aliases[spec["alias"]] = m
    ep, em = spec["embedding"]
    if ep == provider:
        aliases["text-embedding-default"] = em
    cfg: dict = {"aliases": aliases}
    if provider == "bedrock":
        cfg["region"] = settings.aws_region_name
    return cfg


def _needed_providers(spec: dict) -> set[str]:
    provs = {p for (p, _) in spec["chat"]}
    provs.add(spec["embedding"][0])
    return provs


# ─────────────────────────── public API ───────────────────────────


async def seed() -> None:
    """Cold-start seed. Runs only when the DB has no workspaces AND no clients.

    WAVE 21: after a deliberate wipe-to-zero, the operator provisions tenancy
    manually via the admin API (Part B). The seed must NOT re-insert demo data
    on top of a deliberately empty DB \u2014 it only fires when both tables are empty
    (fresh install / first boot).
    """
    async with async_session() as s:
        has_ws = await s.scalar(select(Workspace).limit(1))
        has_client = await s.scalar(select(Client).limit(1))
        if has_ws is not None or has_client is not None:
            return  # already provisioned; reconcile_components() handles updates
        await _seed_tenant(s)
        await s.commit()


async def reseed_to_wave19_model() -> dict:
    """Idempotent migration: ensure the DB matches the WAVE 19 Client \u2192 Workspace
    model. Wipes legacy workspaces (anything not in WORKSPACES.keys()) along
    with their child rows, then upserts the NovaTech tenancy.  Returns a summary."""
    summary = {"client": ACME_CLIENT_ID, "wiped_workspaces": 0,
               "seeded_workspaces": 0, "kept_workspaces": 0}
    c = cipher()
    async with async_session() as s:
        # Wipe any workspace that's NOT one of the WAVE 19 NovaTech workspaces.
        legacy = (await s.scalars(select(Workspace.workspace_id).where(
            Workspace.workspace_id.notin_(list(WORKSPACES.keys()))))).all()
        for ws_id in legacy:
            await s.execute(delete(WorkspaceProviderConfig).where(
                WorkspaceProviderConfig.workspace_id == ws_id))
            await s.execute(delete(ApiKey).where(ApiKey.workspace_id == ws_id))
            await s.execute(delete(Component).where(Component.workspace_id == ws_id))
            await s.execute(delete(GuardrailRule).where(GuardrailRule.workspace_id == ws_id))
            await s.execute(delete(GuardrailProfile).where(GuardrailProfile.workspace_id == ws_id))
            ws = await s.get(Workspace, ws_id)
            if ws:
                await s.delete(ws)
            summary["wiped_workspaces"] += 1
        await _seed_tenant(s, summary=summary)
        await s.commit()
    return summary


async def _seed_tenant(s, summary: dict | None = None) -> None:
    c = cipher()
    # Upsert Client
    client = await s.get(Client, ACME_CLIENT_ID)
    if client is None:
        s.add(Client(
            client_id=ACME_CLIENT_ID, name=ACME_CLIENT_NAME,
            budgets={"client_usd": 5000.0, "user_usd": 1000.0},
            rate_limits={"rpm": 5000, "tpm": 5_000_000},
            required_headers=["X-Gateway-Component"],
            notes="Demo enterprise tenant. Cross-workspace cap enforced by governance flow.",
        ))
    else:
        client.name = ACME_CLIENT_NAME
        client.required_headers = ["X-Gateway-Component"]

    for ws_id, spec in WORKSPACES.items():
        ws = await s.get(Workspace, ws_id)
        if ws is None:
            s.add(Workspace(
                workspace_id=ws_id, client_id=ACME_CLIENT_ID,
                name=spec["display_name"], display_name=spec["display_name"],
                chat_models=_chat_models(spec), embedding_models=_embedding_models(spec),
                default_chat_alias=spec["alias"], guardrails=spec["guardrails"],
                quotas={spec["alias"]: spec["quotas"]}, budgets=spec["budgets"],
                rate_limits=spec["rate_limits"],
            ))
            if summary is not None:
                summary["seeded_workspaces"] += 1
        else:
            ws.client_id = ACME_CLIENT_ID
            ws.name = spec["display_name"]
            ws.display_name = spec["display_name"]
            ws.chat_models = _chat_models(spec)
            ws.embedding_models = _embedding_models(spec)
            ws.default_chat_alias = spec["alias"]
            ws.guardrails = spec["guardrails"]
            ws.quotas = {spec["alias"]: spec["quotas"]}
            ws.budgets = spec["budgets"]
            ws.rate_limits = spec["rate_limits"]
            if summary is not None:
                summary["kept_workspaces"] += 1
        for provider in _needed_providers(spec):
            existing = await s.scalar(select(WorkspaceProviderConfig).where(
                WorkspaceProviderConfig.workspace_id == ws_id,
                WorkspaceProviderConfig.provider == provider))
            if existing is None:
                s.add(WorkspaceProviderConfig(
                    workspace_id=ws_id, provider=provider,
                    config=_provider_config(provider, spec),
                    encrypted_credentials=c.encrypt(_provider_creds(provider))))
            else:
                existing.config = _provider_config(provider, spec)
        # Ensure the demo plaintext key exists (idempotent)
        plain = WS_KEYS.get(ws_id)
        if plain:
            sha = hash_key(plain)
            existing_key = await s.scalar(select(ApiKey).where(ApiKey.sha256 == sha))
            if existing_key is None:
                s.add(ApiKey(workspace_id=ws_id, sha256=sha,
                             prefix=plain[:12] + "\u2026", roles=["admin", "member"]))


async def reconcile_components() -> int:
    """Keeps the seeded NovaTech workspaces in sync (idempotent).

    WAVE 21: does NOT wipe non-NovaTech workspaces (the operator may have
    provisioned Globex/Initech/etc via Part B). Only upserts the WORKSPACES
    defined in seed.py IF the NovaTech client exists AND those workspaces exist.
    If neither is true, it's a manually-provisioned DB and we leave it alone.
    """
    async with async_session() as s:
        primary_client = await s.get(Client, ACME_CLIENT_ID)
        if primary_client is None:
            return 0  # NovaTech client not present \u2014 operator-provisioned DB, skip
        # Just keep existing NovaTech workspaces in sync (don't create new ones)
        count = 0
        for ws_id in WORKSPACES:
            ws = await s.get(Workspace, ws_id)
            if ws is None:
                continue
            spec = WORKSPACES[ws_id]
            ws.display_name = spec["display_name"]
            ws.chat_models = _chat_models(spec)
            ws.embedding_models = _embedding_models(spec)
            ws.default_chat_alias = spec["alias"]
            ws.guardrails = spec["guardrails"]
            ws.rate_limits = spec["rate_limits"]
            count += 1
        await s.commit()
    return count
