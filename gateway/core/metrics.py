"""Prometheus metrics - scraped at GET /metrics.

WAVE 19 TRACK D2: every per-request metric carries the full tenancy +
provider/model dimensions so Grafana dashboards can roll up by Client /
Workspace / User / Component / Provider / Model in any combination.

Cardinality note: ``user`` is the JWT ``sub`` so a noisy IdP could blow up
the label cardinality. The Prometheus observer truncates ``user`` to its
first 32 chars and falls back to ``"-"`` when absent; that keeps the
dimension useful for rollups without runaway series count. We can promote
it to a recording-rule projection later if it becomes a problem.
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# Full tenancy + routing labels. "-" means "not set" (e.g. anonymous user,
# or a workspace not yet attached to a Client during legacy migration windows).
_FULL_LABELS = ("client", "workspace", "user", "component", "provider", "model", "status")
_TENANCY_LABELS = ("client", "workspace", "user", "component")
_TOKEN_LABELS = ("client", "workspace", "user", "component", "provider", "model", "direction")

REQUESTS = Counter("gateway_requests_total", "Total chat requests",
                   _FULL_LABELS)
TOKENS = Counter("gateway_tokens_total", "Tokens processed",
                 _TOKEN_LABELS)
COST = Counter("gateway_cost_usd_total", "Estimated cost USD",
               ("client", "workspace", "user", "component", "provider", "model"))

# Per-stage gateway overhead (excludes upstream provider time). stage label:
#   auth | routing | policy | total  (total = end-to-end minus provider latency)
# Buckets span 50µs → 2s so we can resolve a Bifrost-class plumbing path
# (tens of µs) AND a governance-heavy path (PII/guardrails, tens of ms) on the
# same histogram. The old top bucket was 0.25s, which silently clamped every
# percentile to 250ms once real overhead exceeded it.
_OVERHEAD_BUCKETS = (
    .00005, .0001, .00025, .0005, .001, .0025, .005, .0075,
    .01, .015, .025, .05, .075, .1, .15, .2, .25, .35, .5, .75, 1.0, 2.0,
)
OVERHEAD = Histogram("gateway_overhead_seconds", "Gateway processing overhead by stage (excl. provider)",
                     ["stage"],
                     buckets=_OVERHEAD_BUCKETS)
LATENCY = Histogram("gateway_request_seconds", "Total request latency",
                    buckets=(.05, .1, .25, .5, 1, 2, 5, 10, 30))

# Upstream provider call latency (wall-clock around the BackendEngine call).
PROVIDER_LATENCY = Histogram("gateway_provider_latency_seconds", "Upstream provider call latency",
                             ["provider", "model"],
                             buckets=(.05, .1, .25, .5, 1, 2, 5, 10, 30))
GUARDRAIL = Counter("gateway_guardrail_events_total", "Guardrail decisions",
                    ("client", "workspace", "component", "action"))

# Provider fallbacks (primary -> next target in the ordered chain).
FALLBACKS = Counter("gateway_fallbacks_total", "Provider fallbacks (primary->next)",
                    ("client", "workspace", "from_provider", "to_provider", "reason"))
CACHE_HITS = Counter("gateway_cache_hits_total", "Exact-match response cache hits",
                     ("client", "workspace", "component"))
# WAVE 19 TRACK C2 \u2014 multi-scope rate limits expose the breached scope.
RATE_LIMITED = Counter("gateway_rate_limited_total",
                       "Rate-limit decisions (multi-scope)",
                       ("client", "workspace", "scope", "limit_type"))
BUDGET_ALERTS = Counter("gateway_budget_alerts_total", "Budget threshold alerts",
                        ("client", "workspace", "scope", "threshold"))
# WAVE 19 TRACK C1 \u2014 budget_exceeded responses (402); separate from alerts.
BUDGET_EXCEEDED = Counter("gateway_budget_exceeded_total",
                          "Requests rejected with HTTP 402 budget_exceeded",
                          ("client", "workspace", "scope"))
KAFKA_DLQ = Counter("gateway_kafka_dlq_total", "Kafka publish failures buffered to dead-letter")
GOV_DROPPED = Counter("gateway_governance_events_dropped_total", "Dropped governance events")


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


def _safe(s: str | int | None, *, max_len: int = 32) -> str:
    """Trim+sanitise a label value. None/empty \u2192 '-' so series don't break.

    Accepts int too (e.g. the ``threshold`` label is an int); the body coerces
    with ``str()`` regardless, so widening the annotation is behaviour-preserving.
    """
    if not s:
        return "-"
    s = str(s)
    return s[:max_len] if len(s) > max_len else s


def labels(*, client_id: str | None = None, workspace_id: str | None = None,
           user_id: str | None = None, component: str | None = None,
           provider: str | None = None, model: str | None = None,
           status: str | None = None, direction: str | None = None,
           scope: str | None = None, action: str | None = None,
           limit_type: str | None = None, threshold: int | str | None = None,
           from_provider: str | None = None, to_provider: str | None = None,
           reason: str | None = None) -> dict[str, str]:
    """Helper that returns a dict of safely-trimmed label values. The caller
    passes the keys their metric uses (e.g. for REQUESTS pass client_id +
    workspace_id + user_id + component + provider + model + status). Missing
    values map to ``"-"`` so series stay stable across requests.

    Pass-through behaviour: arguments left at their default ``None`` are
    DROPPED from the returned dict (so callers can pass only the keys their
    metric needs); arguments passed explicitly as None or "" are mapped to
    ``"-"`` and INCLUDED. The convention: pass everything your metric needs
    on every call, even if the value is None \u2014 Prometheus will reject the
    .labels() call if any required key is absent.
    """
    # Use a sentinel to distinguish "not passed" from "passed but empty".
    # Since Python doesn't expose that on a kw-only signature, we instead
    # rely on the metric's documented label set: callers MUST pass every
    # label their metric expects, and "-" is the canonical missing value.
    raw = {
        "client": client_id, "workspace": workspace_id, "user": user_id,
        "component": component, "provider": provider, "model": model,
        "status": status, "direction": direction, "scope": scope,
        "action": action, "limit_type": limit_type, "threshold": threshold,
        "from_provider": from_provider, "to_provider": to_provider,
        "reason": reason,
    }
    out: dict[str, str] = {}
    for k, v in raw.items():
        if v is None:
            continue
        if k == "model":
            out[k] = _safe(v, max_len=64)
        elif k == "threshold":
            out[k] = str(v)
        else:
            out[k] = _safe(v)
    return out


def request_labels(*, client_id, workspace_id, user_id, component,
                    provider, model, status) -> dict[str, str]:
    """Strict helper for the REQUESTS counter \u2014 every label key emitted, with
    `_safe()` defaulting None/empty to '-'. Use this on the chat hot path so
    we never blow up Prometheus with mismatched label sets.
    """
    return {
        "client":    _safe(client_id),
        "workspace": _safe(workspace_id),
        "user":      _safe(user_id),
        "component": _safe(component),
        "provider":  _safe(provider),
        "model":     _safe(model, max_len=64),
        "status":    _safe(status, max_len=16),
    }


def cost_labels(*, client_id, workspace_id, user_id, component,
                 provider, model) -> dict[str, str]:
    return {
        "client":    _safe(client_id),
        "workspace": _safe(workspace_id),
        "user":      _safe(user_id),
        "component": _safe(component),
        "provider":  _safe(provider),
        "model":     _safe(model, max_len=64),
    }


def token_labels(*, client_id, workspace_id, user_id, component,
                  provider, model, direction) -> dict[str, str]:
    return {
        "client":    _safe(client_id),
        "workspace": _safe(workspace_id),
        "user":      _safe(user_id),
        "component": _safe(component),
        "provider":  _safe(provider),
        "model":     _safe(model, max_len=64),
        "direction": _safe(direction, max_len=8),
    }


def guardrail_labels(*, client_id, workspace_id, component, action) -> dict[str, str]:
    return {
        "client":    _safe(client_id),
        "workspace": _safe(workspace_id),
        "component": _safe(component),
        "action":    _safe(action, max_len=16),
    }


def fallback_labels(*, client_id, workspace_id, from_provider, to_provider, reason) -> dict[str, str]:
    return {
        "client":        _safe(client_id),
        "workspace":     _safe(workspace_id),
        "from_provider": _safe(from_provider),
        "to_provider":   _safe(to_provider),
        "reason":        _safe(reason, max_len=32),
    }


def cache_labels(*, client_id, workspace_id, component) -> dict[str, str]:
    return {
        "client":    _safe(client_id),
        "workspace": _safe(workspace_id),
        "component": _safe(component),
    }


def rate_limit_labels(*, client_id, workspace_id, scope, limit_type) -> dict[str, str]:
    return {
        "client":     _safe(client_id),
        "workspace":  _safe(workspace_id),
        "scope":      _safe(scope, max_len=16),
        "limit_type": _safe(limit_type, max_len=16),
    }


def budget_labels(*, client_id, workspace_id, scope) -> dict[str, str]:
    return {
        "client":    _safe(client_id),
        "workspace": _safe(workspace_id),
        "scope":     _safe(scope, max_len=16),
    }


def budget_alert_labels(*, client_id, workspace_id, scope, threshold) -> dict[str, str]:
    return {
        "client":    _safe(client_id),
        "workspace": _safe(workspace_id),
        "scope":     _safe(scope, max_len=16),
        "threshold": str(threshold),
    }
