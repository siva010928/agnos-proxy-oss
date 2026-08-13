"""In-memory token-bucket rate limiting per (workspace, model_alias).

RPM enforced exactly (request count). TPM enforced approximately: we reserve an
estimate pre-flight and the bucket refills over time (reconciliation on success
is left to the caller via record_tokens)."""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _Bucket:
    capacity: float
    tokens: float
    refill_per_sec: float
    last: float

    def take(self, n: float) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
        self.last = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def seconds_until(self, n: float) -> float:
        if self.refill_per_sec <= 0:
            return 60.0
        return max(0.0, (n - self.tokens) / self.refill_per_sec)


class RateLimiter:
    def __init__(self):
        self._rpm: dict[str, _Bucket] = {}
        self._tpm: dict[str, _Bucket] = {}

    def _bucket(self, store, key, limit_per_min) -> _Bucket:
        b = store.get(key)
        if b is None or b.capacity != float(limit_per_min):
            b = _Bucket(capacity=float(limit_per_min), tokens=float(limit_per_min),
                        refill_per_sec=limit_per_min / 60.0, last=time.monotonic())
            store[key] = b
        return b

    def check(self, workspace_id: str, alias: str, quota: dict,
              est_tokens: int) -> tuple[bool, str, float]:
        """Returns (allowed, limit_type, retry_after_sec)."""
        rpm = quota.get("rpm")
        tpm = quota.get("tpm")
        key = f"{workspace_id}:{alias}"
        if rpm:
            b = self._bucket(self._rpm, key, rpm)
            if not b.take(1):
                return False, "rpm", round(b.seconds_until(1), 1)
        if tpm:
            b = self._bucket(self._tpm, key, tpm)
            if not b.take(max(1, est_tokens)):
                return False, "tpm", round(b.seconds_until(max(1, est_tokens)), 1)
        return True, "", 0.0

    # ── WAVE 19 TRACK C2 - multi-scope rate limits ──
    def check_multi_scope(
        self,
        *,
        client_id: str | None,
        workspace_id: str,
        user_id: str | None,
        alias: str,
        client_rl: dict | None,           # {"rpm","tpm"}
        workspace_rl: dict | None,        # {"rpm","tpm","user":{"rpm","tpm"}}
        model_quota: dict | None,         # {"rpm","tpm"}
        est_tokens: int,
    ) -> tuple[bool, str, str, float]:
        """Evaluate the four scopes in **User \u2192 Workspace \u2192 Client \u2192 Model**
        order; first violation wins. Returns
        ``(allowed, scope, limit_type, retry_after_sec)`` where ``scope`` is
        one of ``"user" | "workspace" | "client" | "model"``.

        Each scope checks RPM (request count) then TPM (token reservation).
        Buckets are keyed so the four scopes do not share state.
        """
        # User scope (only when user_id present and Workspace.rate_limits.user set)
        if user_id and workspace_rl:
            uq = workspace_rl.get("user") or {}
            scope, key = "user", f"user:{workspace_id}:{user_id}"
            rpm = uq.get("rpm"); tpm = uq.get("tpm")
            if rpm:
                b = self._bucket(self._rpm, key, rpm)
                if not b.take(1):
                    return False, scope, "rpm", round(b.seconds_until(1), 1)
            if tpm:
                b = self._bucket(self._tpm, key, tpm)
                if not b.take(max(1, est_tokens)):
                    return False, scope, "tpm", round(b.seconds_until(max(1, est_tokens)), 1)

        # Workspace scope
        if workspace_rl:
            scope, key = "workspace", f"workspace:{workspace_id}"
            rpm = workspace_rl.get("rpm"); tpm = workspace_rl.get("tpm")
            if rpm:
                b = self._bucket(self._rpm, key, rpm)
                if not b.take(1):
                    return False, scope, "rpm", round(b.seconds_until(1), 1)
            if tpm:
                b = self._bucket(self._tpm, key, tpm)
                if not b.take(max(1, est_tokens)):
                    return False, scope, "tpm", round(b.seconds_until(max(1, est_tokens)), 1)

        # Client scope
        if client_id and client_rl:
            scope, key = "client", f"client:{client_id}"
            rpm = client_rl.get("rpm"); tpm = client_rl.get("tpm")
            if rpm:
                b = self._bucket(self._rpm, key, rpm)
                if not b.take(1):
                    return False, scope, "rpm", round(b.seconds_until(1), 1)
            if tpm:
                b = self._bucket(self._tpm, key, tpm)
                if not b.take(max(1, est_tokens)):
                    return False, scope, "tpm", round(b.seconds_until(max(1, est_tokens)), 1)

        # Model (per-alias) scope \u2014 keyed by (workspace, alias) like the
        # original RateLimiter.check; this is the existing per-model cap.
        if model_quota:
            scope, key = "model", f"model:{workspace_id}:{alias}"
            rpm = model_quota.get("rpm"); tpm = model_quota.get("tpm")
            if rpm:
                b = self._bucket(self._rpm, key, rpm)
                if not b.take(1):
                    return False, scope, "rpm", round(b.seconds_until(1), 1)
            if tpm:
                b = self._bucket(self._tpm, key, tpm)
                if not b.take(max(1, est_tokens)):
                    return False, scope, "tpm", round(b.seconds_until(max(1, est_tokens)), 1)

        return True, "", "", 0.0


limiter = RateLimiter()


async def enforce_multi_scope(
    *, client_id, workspace_id, user_id, alias,
    client_rl, workspace_rl, model_quota, est_tokens,
) -> tuple[bool, str, str, float]:
    """Single entry point the request path uses. When REDIS_URL is set, rate
    limits are enforced with distributed fixed-window counters shared across
    every gateway replica; otherwise the in-memory token buckets are used. Same
    (allowed, scope, limit_type, retry_after) contract either way."""
    from gateway.core.redis_rate_limit import redis_limiter, using_redis
    kw = dict(client_id=client_id, workspace_id=workspace_id, user_id=user_id,
              alias=alias, client_rl=client_rl, workspace_rl=workspace_rl,
              model_quota=model_quota, est_tokens=est_tokens)
    if using_redis():
        return await redis_limiter.check_multi_scope(**kw)
    return limiter.check_multi_scope(**kw)


def _seconds_to_window_reset() -> int:
    """Fixed per-minute windows: seconds until the next minute boundary."""
    return int(60 - (time.time() % 60))


def rate_limit_headers(quota: dict, limit_type: str = "", retry_after: float | None = None) -> dict[str, str]:
    """Full OpenAI-shaped rate-limit headers. On a 429 the breached dimension's
    remaining is 0; the other dimension is reported at its limit (best-effort)."""
    h: dict[str, str] = {}
    reset = _seconds_to_window_reset()
    rpm = quota.get("rpm")
    tpm = quota.get("tpm")
    if rpm:
        h["X-RateLimit-Limit-Requests"] = str(rpm)
        h["X-RateLimit-Remaining-Requests"] = "0" if limit_type == "rpm" else str(rpm)
        h["X-RateLimit-Reset-Requests"] = f"{reset}s"
    if tpm:
        h["X-RateLimit-Limit-Tokens"] = str(tpm)
        h["X-RateLimit-Remaining-Tokens"] = "0" if limit_type == "tpm" else str(tpm)
        h["X-RateLimit-Reset-Tokens"] = f"{reset}s"
    if retry_after is not None:
        h["Retry-After"] = str(int(retry_after) if retry_after >= 1 else 1)
    return h
