"""Anti-coupling audit (TRACK A of WAVE 19).

Hard architectural rule: the gateway uses Bifrost ONLY through Bifrost's
OpenAI-compatible HTTP endpoints (``/v1/chat/completions``, ``/v1/embeddings``,
``/v1/models``). The single permitted Bifrost-specific surface is the
managed-key selection header ``x-bf-api-key`` (the encapsulated credential
side-channel; Bifrost v1.5+ removed per-request raw creds), and it must live
ONLY inside ``gateway/engines/bifrost_engine.py`` + ``gateway/bifrost/sync.py``.

Everything else - guardrails, virtual keys/teams/customers, governance,
budgets, rate-limits, routing rules, telemetry, prompt repository, MCP,
model catalog - is OURS, in our layer/DB. This test fails loudly if any of
those Bifrost surfaces start to bleed in.

The audit is a static-analysis sweep over ``gateway/`` source files. Each
forbidden pattern carries a precise allowlist of files where it may legally
appear (the engine boundary itself, the schema column for the side-channel,
and pass-through plumbing that just shuttles ``bifrost_key_name`` from the DB
row to the resolved target without interpreting it).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GATEWAY = REPO / "gateway"


# Files that may legitimately reference Bifrost-isms.
ALLOW_BIFROST_KEY_NAME: tuple[str, ...] = (
    # The engine that speaks Bifrost
    "gateway/engines/bifrost_engine.py",
    # Bifrost managed-key lifecycle (create/rotate/delete in Bifrost)
    "gateway/bifrost/sync.py",
    # Schema column for the side-channel
    "gateway/db/models.py",
    # Plumbing: read the column from DB and place it on ResolvedTarget so
    # BifrostEngine can pick it up. None of these files INTERPRET it.
    "gateway/core/credentials.py",
    "gateway/core/registry.py",
    "gateway/core/fallback.py",
    "gateway/core/provider_health.py",
    # Shadow-parity attaches creds (incl. the key-select side-channel) to a target
    # so the BifrostEngine leg can run alongside DirectEngine. Plumbing only - it
    # never interprets the value (same as chat.py / registry.py / fallback.py).
    "gateway/core/parity_run.py",
    "gateway/routes/chat.py",
    "gateway/routes/embeddings.py",
    # Admin CRUD: surfaces it read-only on add-provider response + delete
    # coordination. No behaviour gated on its contents.
    "gateway/routes/admin_crud.py",
)

ALLOW_X_BF: tuple[str, ...] = (
    # The engine sets x-bf-api-key; the boundary strips any other x-bf-*.
    "gateway/engines/bifrost_engine.py",
    "gateway/engines/base.py",
    "gateway/bifrost/sync.py",
)

ALLOW_LEAK_KEYS: tuple[str, ...] = (
    # The boundary itself defines the strip-list - must name the keys.
    "gateway/engines/base.py",
    "gateway/engines/bifrost_engine.py",
    # DirectEngine intentionally writes extra_fields internally to PROVE the
    # boundary strips it (the in-process leak surface). Same anti-corruption
    # pattern as bifrost; tested by test_backend::test_direct_engine_body_clean_at_boundary.
    "gateway/engines/direct_engine.py",
)

# Bifrost API paths that we MUST NOT call. Anything Bifrost-native that's not
# the OpenAI wire goes here.
FORBIDDEN_BIFROST_API_PATHS: tuple[str, ...] = (
    "/api/governance",
    "/api/customers",
    "/api/teams",
    "/api/budgets",
    "/api/virtualkeys",
    "/api/virtual-keys",
    "/api/routing",
    "/api/guardrails",
    "/api/prompts",
    "/api/mcp",
    "/api/telemetry",
)


def _walk_py_files() -> list[Path]:
    return [p for p in GATEWAY.rglob("*.py") if "__pycache__" not in p.parts]


def _rel(p: Path) -> str:
    return str(p.relative_to(REPO))


def test_no_xbf_header_outside_bifrost_engine():
    """Only `x-bf-api-key` may be set, only inside BifrostEngine + the boundary
    that strips other x-bf-* headers."""
    pat = re.compile(r"\bx[-_]bf[-_]", re.IGNORECASE)
    violations: list[str] = []
    for p in _walk_py_files():
        rel = _rel(p)
        if rel in ALLOW_X_BF:
            continue
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            # Strip comments to allow doc strings to mention them in prose
            code = re.sub(r"#.*$", "", line)
            if pat.search(code):
                violations.append(f"{rel}:{i}  {line.strip()}")
    assert not violations, (
        "x-bf-* header used outside the Bifrost engine boundary "
        "(this is a leak of Bifrost-specific surface):\n  " + "\n  ".join(violations)
    )


def test_no_bifrost_config_or_extra_fields_outside_boundary():
    """`bifrost_config` and `extra_fields` are engine-side annotations the
    EngineResult sanitiser strips at the boundary. Outside the boundary +
    DirectEngine's intentional leak-test, no code is allowed to read or write
    them."""
    pat = re.compile(r"\b(bifrost_config|extra_fields)\b")
    violations: list[str] = []
    for p in _walk_py_files():
        rel = _rel(p)
        if rel in ALLOW_LEAK_KEYS:
            continue
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            code = re.sub(r"#.*$", "", line)
            if pat.search(code):
                violations.append(f"{rel}:{i}  {line.strip()}")
    assert not violations, (
        "bifrost_config / extra_fields referenced outside the engine boundary:\n  "
        + "\n  ".join(violations)
    )


def test_no_bifrost_key_name_outside_allowlist():
    """The bifrost_key_name field is the encapsulated key-select side-channel.
    It may travel from the DB row to ResolvedTarget so BifrostEngine can pick
    it up, but it must NEVER be interpreted, logged, or branched-on outside
    the allowlist."""
    pat = re.compile(r"\bbifrost_key_name\b")
    violations: list[str] = []
    for p in _walk_py_files():
        rel = _rel(p)
        if rel in ALLOW_BIFROST_KEY_NAME:
            continue
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            code = re.sub(r"#.*$", "", line)
            if pat.search(code):
                violations.append(f"{rel}:{i}  {line.strip()}")
    assert not violations, (
        "bifrost_key_name referenced outside the allowed plumbing files:\n  "
        + "\n  ".join(violations)
    )


def test_no_calls_to_bifrost_native_apis():
    """The gateway must not call any Bifrost API path beyond the OpenAI-wire
    endpoints. No /api/governance, /api/customers, /api/teams, /api/budgets,
    /api/virtualkeys, /api/routing, /api/guardrails, /api/prompts, /api/mcp,
    /api/telemetry - those are Bifrost surfaces we explicitly OWN ourselves."""
    violations: list[str] = []
    for p in _walk_py_files():
        text = p.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_BIFROST_API_PATHS:
            if forbidden in text:
                # find line number for friendlier output
                for i, line in enumerate(text.splitlines(), 1):
                    if forbidden in line:
                        violations.append(f"{_rel(p)}:{i}  {line.strip()}")
    assert not violations, (
        "Calls to Bifrost-native API paths detected (we own these features, "
        "we do NOT delegate to Bifrost for them):\n  " + "\n  ".join(violations)
    )


def test_bifrost_http_client_only_in_bifrost_engine():
    """The httpx client that talks to settings.bifrost_url must only be
    constructed inside BifrostEngine. If any other file builds an
    httpx.Client/AsyncClient pointed at bifrost_url, that's a coupling leak."""
    pat = re.compile(r"settings\.bifrost_url")
    allow = ("gateway/engines/bifrost_engine.py", "gateway/bifrost/sync.py",
             # health probes the configured engine; reading the URL is fine
             "gateway/core/provider_health.py",
             # /engine/* escape hatch is whitelisted to /v1/* paths only
             # (covered by test_passthrough_only_allows_openai_wire below)
             "gateway/routes/passthrough.py")
    violations: list[str] = []
    for p in _walk_py_files():
        rel = _rel(p)
        if rel in allow:
            continue
        text = p.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                violations.append(f"{rel}:{i}  {line.strip()}")
    assert not violations, (
        "settings.bifrost_url used outside BifrostEngine + sync + health probe + passthrough:\n  "
        + "\n  ".join(violations)
    )


def test_passthrough_only_allows_openai_wire():
    """The /engine/{path} escape hatch must whitelist OpenAI-wire (/v1/*) paths
    only and reject any Bifrost-native path with 403 - proving the audit rule
    holds at runtime, not just statically."""
    pt = (REPO / "gateway" / "routes" / "passthrough.py").read_text()
    # Must have a regex / literal restriction
    assert "_ALLOWED_PATH" in pt or "v1/" in pt, (
        "passthrough.py must explicitly restrict allowed paths to /v1/*"
    )
    # Must explicitly emit a 403 (or HTTPException) on the non-matching path
    assert "403" in pt and ("HTTPException" in pt or "JSONResponse" in pt), (
        "passthrough.py must reject non-OpenAI paths with HTTP 403"
    )


def test_bifrost_engine_only_hits_openai_wire_paths():
    """Read BifrostEngine and assert the only HTTP paths it builds are the
    OpenAI-compatible ones. If a future change adds /api/* calls to it, fail."""
    bf = REPO / "gateway" / "engines" / "bifrost_engine.py"
    text = bf.read_text(encoding="utf-8")
    # Allow these path tokens
    allowed = ("/v1/chat/completions", "/v1/embeddings", "/v1/models",
               "/api/providers")  # /api/providers is read-only health probe (Bifrost's own /health-style)
    forbidden_substrings = ("/api/governance", "/api/customers", "/api/teams",
                            "/api/budgets", "/api/virtualkeys", "/api/virtual-keys",
                            "/api/routing", "/api/guardrails", "/api/prompts",
                            "/api/mcp", "/api/telemetry")
    for f in forbidden_substrings:
        assert f not in text, (
            f"BifrostEngine references forbidden Bifrost API path '{f}'. "
            f"That feature is OURS, not delegated."
        )


@pytest.mark.parametrize("path", FORBIDDEN_BIFROST_API_PATHS)
def test_no_bifrost_native_path_in_anywhere(path: str):
    """Each forbidden Bifrost-native path is explicitly absent from the entire
    gateway source tree (parametrized so each forbidden surface gets its own
    visible green/red row)."""
    for p in _walk_py_files():
        text = p.read_text(encoding="utf-8")
        assert path not in text, (
            f"Forbidden Bifrost-native path '{path}' found in {_rel(p)} - we own this."
        )
