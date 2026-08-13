"""Routing: resolve a request to an ordered list of provider targets.

Three modes (A default):
  A) alias only          model="claude-sonnet-4-5"           -> workspace alias lookup
  B) provider-prefixed   model="bedrock:us.anthropic...."    -> explicit provider+id
  C) header/registry     model="default-chat" + X-Workspace  -> workspace default alias
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status

from gateway.config import settings
from gateway.core.auth import WorkspaceContext


def resolve_timeout_s(config: dict | None) -> int:
    """Effective per-request timeout (seconds) for a provider target.

    Precedence: admin override (`request_timeout_seconds` /
    `default_request_timeout_in_seconds` in the provider config) wins; otherwise
    the gateway default (`settings.bifrost_default_timeout_s`). Clamped to
    1..`settings.max_request_timeout_s` so a long-running use case can configure
    up to the 2h ceiling, but a typo can't hang a connection forever. This single
    resolver is the source of truth shared by the gateway deadline (fallback.py)
    and the value pushed to Bifrost's per-key network_config (bifrost/sync.py),
    so the two never disagree.
    """
    cfg = config or {}
    raw = cfg.get("request_timeout_seconds") or cfg.get("default_request_timeout_in_seconds")
    try:
        v = int(raw) if raw not in (None, "") else settings.bifrost_default_timeout_s
    except (TypeError, ValueError):
        v = settings.bifrost_default_timeout_s
    return max(1, min(v, settings.max_request_timeout_s))


@dataclass
class ResolvedTarget:
    provider: str
    model_id: str
    context_window: int = 100_000
    weight: float = 1.0
    credentials: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    bifrost_key_name: str | None = None
    # workspace this target belongs to - lets a STATEFUL engine (LiteLLM) select the
    # workspace's synced model (ws-{ws}--{provider}--{model}), mirroring how Bifrost
    # selects a managed key. Set by the resolver; not an engine-specific surface.
    workspace_id: str | None = None
    # promoted connection details (also mirrored in config for the engine)
    region: str | None = None
    base_url: str | None = None
    api_version: str | None = None

    def hydrate_from_config(self) -> None:
        """Promote region/base_url/api_version out of the raw config/credentials."""
        cfg = self.config or {}
        creds = self.credentials or {}
        self.region = creds.get("region") or cfg.get("region") or self.region
        self.base_url = cfg.get("base_url") or self.base_url
        self.api_version = cfg.get("api_version") or self.api_version


def _alias_targets(ws: WorkspaceContext, alias: str) -> list[dict]:
    spec = ws.chat_models.get(alias)
    if not spec:
        return []
    return spec if isinstance(spec, list) else [spec]


def resolve_chat_targets(ws: WorkspaceContext, body: dict, headers) -> tuple[str, list[ResolvedTarget]]:
    """Return (model_alias, ordered targets[primary, *fallbacks])."""
    model = body.get("model")
    if not model:
        # Mode C: no model in body - use workspace default
        model = "default-chat"

    # Mode B: provider-prefixed
    if isinstance(model, str) and ":" in model and "/" not in model:
        provider, _, model_id = model.partition(":")
        alias = model
        targets = [ResolvedTarget(provider=provider, model_id=model_id, workspace_id=ws.workspace_id)]
        return alias, targets

    # Mode C: default sentinels → workspace default alias. Components that don't
    # care which model runs can pass model="default" (or omit it / leave blank);
    # the gateway resolves it to the workspace's default_chat_alias. We DON'T
    # silently remap arbitrary unknown models - an unregistered alias still 404s.
    if model in ("default-chat", "default") or (isinstance(model, str) and not model.strip()):
        # The admin must have set a default. Auto-set on save covers the normal
        # path; if we still arrive here without one, it's a real misconfiguration
        # and we surface it loudly instead of silently picking an arbitrary alias
        # (which used to mask the problem and route requests through the "wrong"
        # model). The error message points the admin at exactly where to fix it.
        chosen = ws.default_chat_alias
        if not chosen:
            aliases = list(ws.chat_models or {})
            if aliases:
                raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                    detail={"error": {
                                        "message": (f"Workspace '{ws.workspace_id}' has no default_chat_alias set, "
                                                    f"so a request with model='default' cannot be resolved. "
                                                    f"Set a default in Workspaces > Routing (available aliases: "
                                                    f"{aliases}), or pass an explicit model in the request."),
                                        "type": "invalid_request_error",
                                        "code": "default_chat_alias_missing",
                                        "param": "default_chat_alias"}})
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail={"error": {"message": "This workspace has no chat models registered. "
                                                             "Add a model alias (routing) for this workspace, "
                                                             "or pass an explicit model in the request.",
                                                  "type": "invalid_request_error"}})
        model = chosen

    # Mode A: alias lookup (with fallback chain + optional weighted primary)
    specs = _alias_targets(ws, model)
    if not specs:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail={"error": {"message": f"Model '{model}' not registered for workspace "
                                                         f"'{ws.workspace_id}'. Available: {list(ws.chat_models)}",
                                              "type": "invalid_request_error"}})
    targets = [ResolvedTarget(provider=s["provider"], model_id=s["model_id"],
                              context_window=s.get("context_window", 100_000),
                              weight=float(s.get("weight", 1) or 1)) for s in specs]
    # Weighted load-balancing: if targets carry weights, pick the primary by weight
    # (the rest remain the ordered fallback chain). Distributes traffic across keys.
    weights = [t.weight for t in targets]
    if len(targets) > 1 and any(w != 1 for w in weights):
        import random
        idx = random.choices(range(len(targets)), weights=weights, k=1)[0]
        if idx != 0:
            targets.insert(0, targets.pop(idx))
    for t in targets:
        t.workspace_id = ws.workspace_id
    return model, targets


def resolve_embedding_target(ws: WorkspaceContext, alias: str) -> ResolvedTarget:
    # Mode B: provider-prefixed explicit embedding model (e.g. "bedrock:amazon.titan-embed-text-v2:0").
    # Mirror chat routing so components (and the sanity catalog) can request an
    # explicit provider embedding model, not only a workspace alias.
    if isinstance(alias, str) and ":" in alias and "/" not in alias:
        provider, _, model_id = alias.partition(":")
        if provider and model_id:
            return ResolvedTarget(provider=provider, model_id=model_id, workspace_id=ws.workspace_id)
    spec = ws.embedding_models.get(alias)
    if not spec:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail={"error": {"message": f"Embedding model '{alias}' not registered. "
                                                         f"Available: {list(ws.embedding_models)}",
                                              "type": "invalid_request_error"}})
    s0 = spec[0] if isinstance(spec, list) else spec
    return ResolvedTarget(provider=s0["provider"], model_id=s0["model_id"],
                          context_window=s0.get("context_window", 8192), workspace_id=ws.workspace_id)
