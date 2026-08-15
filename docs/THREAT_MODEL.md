# Threat model

This document describes what Agnos Proxy protects, the trust boundaries in its design, the
attacker capabilities it assumes, and the mitigations in place. It complements
[SECURITY.md](../SECURITY.md) (how to report a vulnerability) and
[ARCHITECTURE.md](../ARCHITECTURE.md) (how the pieces fit).

## Assets (what we protect)

1. **Provider API keys** - the crown jewels. Encrypted at rest (Fernet) in the vault; only ever
   decrypted in the control plane, for a single in-flight request.
2. **The master key** (`GATEWAY_MASTER_KEY`) that encrypts the vault.
3. **Workspace keys** (`gw-…`) - low-value, revocable credentials apps use to call the gateway.
4. **Governance data** - request logs, spend, and (subject to retention config) prompts/responses.
5. **Admin/session credentials** - dashboard login, platform admin token, session-signing secret.

## Trust boundaries

- **App → Gateway.** Apps authenticate with a workspace key (`gw-…`) or a workspace JWT. They never
  hold provider keys. A leaked workspace key is revocable without touching provider credentials.
- **Control plane ↔ Translation engine (the key boundary).** The engine is treated as untrusted,
  fast-moving third-party code. It holds **no** provider keys; the control plane injects exactly one
  provider key per request, in flight. A fully compromised engine can read only the traffic passing
  through it during the compromise window - never the vault.
- **Gateway ↔ Provider.** Standard TLS to the upstream provider.
- **Operator ↔ Dashboard / Admin API.** Session cookie (HS256, HttpOnly, SameSite=Lax) or the
  platform admin token (`X-Admin-Token`), compared in constant time.

## Attacker capabilities considered

- **Network attacker** between app↔gateway or gateway↔provider — mitigated by TLS.
- **Compromised / CVE'd translation engine** (e.g. RCE in the sidecar) — no key store to dump; blast
  radius is one in-flight key; quarantine + swap the engine by config.
- **Stolen workspace key** — scoped, budgeted, rate-limited, revocable; never a provider key.
- **Malicious / over-curious tenant** reading another workspace's data — workspace scoping on every
  governed query.
- **Misconfigured operator** shipping default secrets — fail-closed startup in login mode.

## Mitigations (summary)

- Provider keys encrypted at rest; decrypted per request only; never persisted in the engine.
- Guardrails (PII/secret detection + CEL policy) on both request and response.
- Hierarchical budgets, multi-scope rate limits, and circuit breakers.
- Fail-closed config validation: with `PREVIEW_MODE=false` the gateway refuses to start while
  `PLATFORM_ADMIN_TOKEN` / `SESSION_SECRET` / `DASHBOARD_ADMIN_PASSWORD` are their shipped defaults.
- Constant-time comparison of session signatures and the platform admin token.
- A compile-time anti-coupling test keeps engine specifics behind the `BackendEngine` port.

## Explicitly out of scope

- Vulnerabilities **inside** a third-party engine (Bifrost / LiteLLM / Portkey) or a model provider -
  report those upstream. A *containment failure* (an engine reaching the vault) **is** in scope.
- The security of a provider's own infrastructure.
- Denial of service from an authenticated tenant beyond its configured rate/budget limits.
- `PREVIEW_MODE` / playground deployments, which are intentionally open, keyless demos.

## Operator responsibilities

- Set strong `GATEWAY_MASTER_KEY`, `PLATFORM_ADMIN_TOKEN`, `SESSION_SECRET`, `DASHBOARD_ADMIN_PASSWORD`.
- Run production with `PREVIEW_MODE=false` and `AGNOS_JWT_DEV_TRUST=false` (+ `OIDC_ISSUER` for JWT
  signature verification).
- Pin engine versions and keep the translation engine patched (see [ENGINES.md](ENGINES.md)).
- Restrict network access to the dashboard and admin API.
