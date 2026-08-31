"""Capability-comparison matrix, served as JSON at /admin/parity.

A generic, vendor-neutral comparison of three approaches to LLM governance:
  - "embedded_sdk": an SDK embedded in every component (per-component integration)
  - "generic_gateway": a generic provider-multiplexing LLM gateway
  - "ours": this gateway (own-the-policy proxy over a swappable engine)

Single source of truth for the dashboard's comparison panel (/admin/parity).
Status reflects OUR implementation: built | built_live_needs_config | not_built.
"""
from __future__ import annotations

# (capability, embedded_sdk, generic_gateway, ours, status)
_ROWS: list[tuple[str, str, str, str, str]] = [
    ("Onboarding", "import + wire SDK into each component", "change base_url",
     "change base_url only (framework's own OpenAI interface)", "built"),
    ("Identity", "app passes ids in code", "API key → flat tenant",
     "workspace JWT or API key → workspace + user + component (server-derived)", "built"),
    ("Per-(workspace,component) credentials", "creds in each component process", "one key set per gateway",
     "encrypted per workspace×component in the vault; injected per request (engines hold none)", "built"),
    ("Credential isolation", "keys live in every component", "keys in gateway",
     "keys only in gateway (encrypted) + isolated engine; never in components", "built"),
    ("Governance emission", "component calls an emit/log function", "basic request logs",
     "automatic from the pipeline - components emit nothing", "built"),
    ("Guardrails", "library per component (drift)", "provider-delegated or none",
     "UI-configurable Rules + Profiles, run in our layer (survives engine swap)", "built"),
    ("Pre-call policy enforcement", "depends on each component", "limited",
     "CEL rules + detectors before the model call, uniformly", "built"),
    ("Routing & fallback", "hardcoded per component", "static routing",
     "weighted ordered targets + fallback chain, DB-driven + UI", "built"),
    ("Cost attribution", "per-app, inconsistent", "per key/model",
     "per workspace/user/component/use-case/model/provider/key", "built"),
    ("Custom pricing", "n/a", "static table",
     "synced public pricing + DB overrides (override wins)", "built"),
    ("Rate limits & budgets", "per app", "per key",
     "RPM/TPM + workspace & user $ budgets (402), Redis multi-replica", "built"),
    ("Observability", "per-app wiring", "metrics only",
     "Prometheus + Grafana + SLO alerts + OTel parent/child spans", "built"),
    ("Event stream", "app-specific topics", "vendor logs",
     "own self-described envelope on agnos-proxy.governance.v1", "built"),
    ("Multi-framework", "one SDK per language/framework", "OpenAI clients",
     "any OpenAI-compatible client/framework, zero bridge code", "built"),
    ("Engine swappability", "n/a", "locked to gateway's providers",
     "swap translation engine (Bifrost ⇄ Direct), identical governance", "built"),
    ("Pass-through escape hatch", "n/a", "limited",
     "/engine/{path} for long-tail provider features", "built"),
    ("Per-provider health + breaker", "n/a", "varies",
     "live probe + circuit-breaker state on /admin/health", "built"),
    ("Anti-corruption boundary", "n/a", "leaks provider-isms",
     "pure-OpenAI engine boundary; engine-isms stripped", "built"),
    ("SSO / OIDC", "platform-specific", "varies",
     "workspace JWT (OIDC/JWKS or dev-trust) + password session", "built_live_needs_config"),
]

GOAL = 0.80


def matrix() -> dict:
    rows = [{"capability": c, "embedded_sdk": e, "generic_gateway": g, "ours": o, "status": st}
            for (c, e, g, o, st) in _ROWS]
    counted = [r for r in rows if r["status"] in ("built", "built_live_needs_config")]
    built = [r for r in rows if r["status"] == "built"]
    total = len(rows)
    # capabilities where our approach is uniquely strong
    leads = ["Governance emission", "Guardrails", "Engine swappability",
             "Per-(workspace,component) credentials", "Multi-framework"]
    return {
        "goal": GOAL,
        "total_capabilities": total,
        "built": len(built),
        "built_or_needs_config": len(counted),
        "coverage": round(len(counted) / total, 3) if total else 0.0,
        "coverage_strict": round(len(built) / total, 3) if total else 0.0,
        "meets_goal": (len(counted) / total) >= GOAL if total else False,
        "we_lead": leads,
        "rows": rows,
    }
