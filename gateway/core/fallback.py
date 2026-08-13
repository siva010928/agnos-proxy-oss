"""Own-the-policy reliability: retries+backoff, circuit breaker, and provider
fallback across an ordered target list. Each attempt is observable."""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

from gateway.core.credentials import get_provider_credential
from gateway.core.registry import ResolvedTarget, resolve_timeout_s
from gateway.engines.base import BackendEngine, EngineResult

# Error *types* that are safe to retry on the same target.
RETRYABLE = {"upstream_error", "timeout", "rate_limit_exceeded"}
# Transient upstream *status codes* that should be retried with backoff before
# giving up / falling over to the next target (provider overload, throttling,
# gateway hiccups). 529 = Anthropic "overloaded".
RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}


@dataclass
class _Breaker:
    fails: int = 0
    opened_at: float = 0.0


class CircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown: float = 30.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self._b: dict[str, _Breaker] = {}

    def _key(self, ws: str, provider: str) -> str:
        return f"{ws}:{provider}"

    def is_open(self, ws: str, provider: str) -> bool:
        b = self._b.get(self._key(ws, provider))
        if not b or b.opened_at == 0.0:
            return False
        if time.monotonic() - b.opened_at > self.cooldown:
            b.opened_at = 0.0
            b.fails = 0
            return False
        return True

    def record(self, ws: str, provider: str, ok: bool) -> None:
        k = self._key(ws, provider)
        b = self._b.setdefault(k, _Breaker())
        if ok:
            b.fails = 0
            b.opened_at = 0.0
        else:
            b.fails += 1
            if b.fails >= self.threshold:
                b.opened_at = time.monotonic()


breaker = CircuitBreaker()


@dataclass
class FallbackResult:
    result: EngineResult
    target: ResolvedTarget
    attempt: int
    provider_ms: float = 0.0
    fallbacks_emitted: list[tuple[str, str, str]] = field(default_factory=list)  # (from,to,reason)
    # Per-attempt outcomes in actual attempt order, so the trace can explain WHY
    # each earlier target failed (not just that it was tried). One entry per
    # failed attempt (incl. same-target retries) and per breaker-skip.
    attempts: list[dict] = field(default_factory=list)
    # The effective per-request timeout (seconds) used for the target that served
    # (or the last one tried). Surfaced so a timeout error can report the ACTUAL
    # configured value instead of an engine's misleading generic default.
    effective_timeout_s: float = 0.0


async def execute(engine: BackendEngine, body: dict, targets: list[ResolvedTarget],
                  workspace_id: str, max_retries: int = 1,
                  timeout: float | None = None) -> FallbackResult:
    """Try targets in order, falling over to the next on failure. Returns first
    success or the last error.

    PROXY PHILOSOPHY (Bifrost/LiteLLM-style): we DON'T loop with long backoff -
    the caller's SDK (e.g. openai-python) already does 5 retries with exponential
    backoff (≈12s budget). Stacking long retries on top wastes wall-clock time.
    But ONE quick same-target retry (≈300-500ms) absorbs sub-second 502 micro-
    blips that the SDK can sometimes miss. Our REAL value-add is the FALLBACK
    CHAIN (next target on failure) - that's how we beat a prolonged single-
    provider outage. A workspace with only one target can only be as resilient
    as that provider.

    Each upstream call is bounded by ``timeout``. When ``timeout`` is None the
    per-target configured timeout (provider config else gateway default, clamped
    to the 2h ceiling) is used, so long-running use cases work without forcing a
    header on every call."""
    fb_events: list[tuple[str, str, str]] = []
    attempts: list[dict] = []
    last: EngineResult | None = None
    prev_provider: str | None = None
    eff_to: float = float(timeout) if timeout else 0.0

    for idx, target in enumerate(targets):
        if breaker.is_open(workspace_id, target.provider):
            if prev_provider:
                fb_events.append((prev_provider, target.provider, "circuit_open_skip_prev"))
            attempts.append({
                "provider": target.provider, "model_id": target.model_id,
                "skipped": True, "error_type": "circuit_open", "http_status": None,
                "message": "circuit breaker open for this provider; skipped without calling it",
            })
            continue
        # attach credentials for this specific target
        cred = await get_provider_credential(workspace_id, target.provider)
        if cred:
            target.credentials = cred.credentials
            target.config = cred.config
            target.bifrost_key_name = cred.bifrost_key_name
            target.hydrate_from_config()

        if idx > 0 and prev_provider:
            fb_events.append((prev_provider, target.provider, "primary_failed"))

        # Effective deadline for THIS target: explicit header override wins, else
        # the per-provider configured timeout (same value pushed to the engine).
        eff_to = float(timeout) if timeout else float(resolve_timeout_s(getattr(target, "config", None)))

        attempt = 0
        while attempt <= max_retries:
            attempt += 1
            _t = time.monotonic()
            try:
                result = await asyncio.wait_for(engine.chat(body, target), timeout=eff_to)
            except asyncio.TimeoutError:
                result = EngineResult({"error": {"message": f"Upstream timed out after {eff_to:g}s.",
                                                 "type": "timeout"}}, 504)
            except Exception as exc:  # noqa: BLE001
                result = EngineResult({"error": {"message": str(exc), "type": "upstream_error"}}, 502)
            provider_ms = (time.monotonic() - _t) * 1000

            if result.ok:
                breaker.record(workspace_id, target.provider, True)
                return FallbackResult(result, target, attempt, provider_ms, fb_events,
                                      attempts=attempts, effective_timeout_s=eff_to)

            last = result
            _errbody = result.body.get("error") if isinstance(result.body, dict) else None
            _errbody = _errbody if isinstance(_errbody, dict) else {}
            err_type = _errbody.get("type", "upstream_error")
            err_msg = _errbody.get("message", "") or ""
            attempts.append({
                "provider": target.provider, "model_id": target.model_id, "attempt": attempt,
                "http_status": result.status_code, "error_type": err_type,
                "message": err_msg[:300], "ms": round(provider_ms, 1),
            })
            breaker.record(workspace_id, target.provider, False)
            # Retry transient failures (by type OR upstream status) with
            # exponential backoff + jitter - this is what lets the gateway absorb
            # provider throttling/overload (e.g. Bedrock under a parallel burst)
            # instead of surfacing a 500 to the component.
            retryable = err_type in RETRYABLE or result.status_code in RETRYABLE_STATUS
            if retryable and attempt <= max_retries:
                # Sub-second backoff only - enough to absorb a micro-blip without
                # racing the client SDK's own exponential retry (which has a ~12s
                # budget across 5 attempts). A real prolonged outage is handled
                # by falling over to the NEXT target, not by long same-target loops.
                backoff = min(0.5, 0.2 * attempt)
                await asyncio.sleep(backoff + random.uniform(0, 0.1))
                continue
            break
        prev_provider = target.provider

    return FallbackResult(last or EngineResult(
        {"error": {"message": "all targets failed", "type": "upstream_error"}}, 502),
        targets[-1], 1, 0.0, fb_events, attempts=attempts, effective_timeout_s=eff_to)
