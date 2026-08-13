"""Environment + credential loading for the sanity suite.

Reads the SAME provider env vars the application uses. Source precedence:
  1. gateway `.env` (repo root) - the base.
  2. an override file: `--env-file PATH`, or `$SANITY_ENV_FILE` if set.
The override (when given) wins per-key. No path is hardcoded - point `--env-file`
/ `$SANITY_ENV_FILE` at any provider env file you want to exercise. Read at
runtime only; nothing is committed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import dotenv_values

_GATEWAY = os.path.join(os.path.dirname(__file__), "..", "..", ".env")


def _clean(v: str | None) -> str:
    return (v or "").strip().strip('"').strip("'")


def load_env(env_file: str | None = None) -> dict[str, str]:
    """Merged env: gateway .env (base) <- override file (--env-file / $SANITY_ENV_FILE) on top.

    Hedge: prefer an AI-Studio-format Gemini key ("AIza…") from EITHER source, since
    a non-standard GEMINI_API_KEY (e.g. an OAuth token) won't work on the AI-Studio
    REST path the DirectEngine uses.
    """
    override = env_file or os.environ.get("SANITY_ENV_FILE") or None
    gemini_candidates: list[str] = []
    merged: dict[str, str] = {}
    for path in (_GATEWAY, override):
        if not path:
            continue
        try:
            for k, v in dotenv_values(path).items():
                cv = _clean(v)
                if not cv:
                    continue
                if k == "GEMINI_API_KEY":
                    gemini_candidates.append(cv)
                merged[k] = cv
        except Exception:
            pass
    aistudio = next((g for g in gemini_candidates if g.startswith("AIza")), None)
    if aistudio:
        merged["GEMINI_API_KEY"] = aistudio
    return merged


# region prefix → default AWS region (mirrors the Bedrock inference-profile rule)
_PREFIX_REGION = {"us": "us-east-1", "eu": "eu-west-1", "ap": "ap-northeast-1",
                  "au": "ap-southeast-2", "global": "us-east-1"}


def aws_region(env: dict, model_id: str = "") -> str:
    r = _clean(env.get("AWS_REGION_NAME"))
    if r:
        return r
    prefix = (model_id or "").split(".", 1)[0]
    return _PREFIX_REGION.get(prefix, "us-east-1")


@dataclass
class ProviderSpec:
    key: str                 # target key, e.g. "bedrock-static"
    provider: str            # engine provider name: anthropic|bedrock|gemini|openai
    auth: str                # static|bearer|sso|api-key
    credentials: dict        # plaintext creds for the admin provider POST
    chat_model: str | None
    embed_model: str | None
    available: bool
    engines: tuple[str, ...] = ("bifrost", "direct")   # which engines can serve it
    config: dict = field(default_factory=dict)          # extra provider config (e.g. vertex_project)
    note: str = ""


def provider_specs(env: dict) -> list[ProviderSpec]:
    """Build the provider/auth/model matrix from whatever creds are present."""
    specs: list[ProviderSpec] = []

    # ── Anthropic (chat only; no embeddings API) ──
    ak = _clean(env.get("ANTHROPIC_API_KEY"))
    specs.append(ProviderSpec(
        key="anthropic", provider="anthropic", auth="api-key",
        credentials={"api_key": ak},
        chat_model=_clean(env.get("ANTHROPIC_MODEL")) or "claude-haiku-4-5-20251001",
        embed_model=None, available=bool(ak)))

    # ── Bedrock - three auth modes ──
    chat_b = _clean(env.get("AWS_MODEL_NAME")) or "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    embed_b = _clean(env.get("AWS_EMBEDDINGS_MODEL_NAME")) or "amazon.titan-embed-text-v2:0"
    region = aws_region(env, chat_b)
    ak_id, sk = _clean(env.get("AWS_ACCESS_KEY_ID")), _clean(env.get("AWS_SECRET_ACCESS_KEY"))
    specs.append(ProviderSpec(
        key="bedrock-static", provider="bedrock", auth="static",
        credentials={"access_key": ak_id, "secret_key": sk, "region": region,
                     **({"session_token": _clean(env.get("AWS_SESSION_TOKEN"))}
                        if _clean(env.get("AWS_SESSION_TOKEN")) else {})},
        chat_model=chat_b, embed_model=embed_b, available=bool(ak_id and sk),
        note="static access key/secret"))
    bearer = _clean(env.get("AWS_BEDROCK_API_KEY"))
    specs.append(ProviderSpec(
        key="bedrock-bearer", provider="bedrock", auth="bearer",
        credentials={"bedrock_api_key": bearer, "region": region},
        chat_model=chat_b, embed_model=embed_b, available=bool(bearer),
        engines=("direct",), note="Bedrock bearer API key (AWS_BEARER_TOKEN_BEDROCK)"))
    profile = _clean(env.get("AWS_PROFILE_NAME"))
    specs.append(ProviderSpec(
        key="bedrock-sso", provider="bedrock", auth="sso",
        credentials={"auth_type": "sso", "profile_name": profile, "region": region},
        chat_model=chat_b, embed_model=embed_b, available=bool(profile),
        engines=("direct",), note="SSO profile (aws sso login first)"))

    # ── Gemini (AI Studio) ──
    gk = _clean(env.get("GEMINI_API_KEY"))
    specs.append(ProviderSpec(
        key="gemini", provider="gemini", auth="api-key",
        credentials={"api_key": gk},
        chat_model=_clean(env.get("GOOGLE_GENAI_MODEL")) or "gemini-2.5-flash",
        embed_model=_clean(env.get("GOOGLE_GENAI_EMBEDDING_MODEL")) or "gemini-embedding-001",
        available=bool(gk)))

    # ── OpenAI (now served by BOTH bifrost + our DirectEngine OpenAI-compat adapter) ──
    ok = _clean(env.get("OPENAI_API_KEY"))
    specs.append(ProviderSpec(
        key="openai", provider="openai", auth="api-key",
        credentials={"api_key": ok},
        chat_model=_clean(env.get("OPENAI_MODEL")) or "gpt-4o-mini",
        embed_model=_clean(env.get("OPENAI_EMBEDDINGS_MODEL")) or "text-embedding-3-small",
        available=bool(ok), engines=("bifrost", "direct"),
        note="bifrost + DirectEngine (OpenAI-compatible adapter)"))

    # (gemini above IS Google AI Studio / google_genai - one provider, not two.)

    # ── Vertex AI (service-account JSON, admin-provided; no ambient ADC) ──
    sa = _clean(env.get("GOOGLE_APPLICATION_CREDENTIALS"))
    vproj = _clean(env.get("VERTEXAI_PROJECT"))
    vloc = _clean(env.get("VERTEXAI_LOCATION")) or "us-central1"
    specs.append(ProviderSpec(
        key="vertex-ai", provider="vertex_ai", auth="service-account",
        credentials={"api_key": sa},
        chat_model=_clean(env.get("VERTEX_MODEL")) or "gemini-2.5-flash",
        # Vertex's OpenAI-compat endpoint only exposes select OpenMaaS embedding
        # models (classic text-embedding-005 is not one) - chat-only here.
        embed_model=None,
        available=bool(vproj and sa),   # requires an admin-provided SA JSON
        engines=("direct",),
        config={"vertex_project": vproj, "vertex_location": vloc},
        note="Vertex AI via admin-provided service account"))

    return specs


def available(env: dict) -> list[ProviderSpec]:
    return [s for s in provider_specs(env) if s.available]
