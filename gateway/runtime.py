"""Runtime singletons: backend engine + governance bus + SSE observer."""
from __future__ import annotations

import random

from gateway.config import settings
from gateway.engines.base import BackendEngine
from gateway.engines.bifrost_engine import BifrostEngine
from gateway.governance.bus import GovernanceBus
from gateway.governance.logger_observer import LoggerObserver
from gateway.governance.postgres_observer import PostgresObserver
from gateway.governance.sse_observer import SseObserver

# SSE observer is referenced directly by the /events route
sse_observer = SseObserver()

_engine: BackendEngine | None = None
_bus: GovernanceBus | None = None


def engine_by_name(name: str) -> BackendEngine:
    """Construct a BackendEngine by its slot name. This is the ONE place that maps
    an engine name → instance, shared by the global default, the live /admin/engine
    swap, and per-provider routing. Adding a commodity translator to the slot is a
    single line here - nothing in the request path or governance changes."""
    if name == "direct":
        from gateway.engines.direct_engine import DirectEngine  # lazy (optional dep)
        return DirectEngine()
    if name == "echo":
        from gateway.engines.echo_engine import EchoEngine  # deterministic in-process upstream
        return EchoEngine()
    if name == "litellm":
        from gateway.engines.litellm_engine import LiteLLMEngine  # stateless commodity translator (holds no keys)
        return LiteLLMEngine()
    if name == "portkey":
        from gateway.engines.portkey_engine import PortkeyEngine  # STATELESS commodity translator
        return PortkeyEngine()
    return BifrostEngine()


def build_engine() -> BackendEngine:
    return engine_by_name(settings.engine)


def engine() -> BackendEngine:
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


# Providers only our DirectEngine serves (Bifrost has no adapter for them), so
# they always route to DirectEngine no matter the workspace/global engine setting.
_DIRECT_ONLY_PROVIDERS = frozenset({"litellm_proxy", "ollama", "hosted_vllm", "lm-studio",
                                    "google_genai", "vertex_ai"})


def select_engine(overrides: dict | None, provider: str) -> BackendEngine:
    """Per-provider engine selection honoring a gateway-wide override. The override
    value for a provider can be:

        None / ""            → the global default engine (engine())
        "bifrost"            → the Bifrost commodity translator (stateless; holds no keys)
        "litellm"            → the LiteLLM commodity translator (stateless; holds no keys)
        "portkey"            → the Portkey commodity translator (STATELESS)
        "direct"             → our owned DirectEngine (in-process, holds nothing)
        int/float N          → CANARY: route ~N% of this provider's calls to our
                               owned DirectEngine, the rest to the default engine
        {"direct_pct": N}    → same as the numeric form

    This is how a provider is migrated across the slot gradually and safely, and how
    "Quarantine & Evacuate" flips every provider to a safe engine in one write. The
    engine that actually served is recorded per request (RequestLog.engine), so the
    split is visible in Analytics / Request Logs. No engine identity is hardcoded in
    the request path."""
    from gateway.engines.direct_engine import DirectEngine
    # Providers the rented Bifrost sidecar does not serve: our OWN DirectEngine is
    # the only path, so force it regardless of the global default / override.
    if provider in _DIRECT_ONLY_PROVIDERS:
        return DirectEngine()
    ov = (overrides or {}).get(provider)
    if ov in (None, ""):
        return engine()
    if isinstance(ov, str):
        return engine_by_name(ov)
    pct = 0.0
    if isinstance(ov, (int, float)):
        pct = float(ov)
    elif isinstance(ov, dict):
        try:
            pct = float(ov.get("direct_pct", 0) or 0)
        except (TypeError, ValueError):
            pct = 0.0
    if pct > 0 and random.random() * 100.0 < pct:
        return DirectEngine()
    return engine()


def bus() -> GovernanceBus:
    global _bus
    if _bus is None:
        if settings.governance_mode == "noop":
            _bus = GovernanceBus([])   # A/B baseline: governance disabled
            return _bus
        from gateway.governance.prometheus_observer import PrometheusObserver
        observers = [sse_observer, LoggerObserver(), PostgresObserver(), PrometheusObserver()]
        # Kafka observer added when KAFKA_BROKERS is set
        if settings.kafka_brokers:
            from gateway.governance.kafka_observer import KafkaObserver
            observers.append(KafkaObserver())
        _bus = GovernanceBus(observers)
    return _bus
