# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-08-15

Initial public release.

### Added
- OpenAI-compatible governance proxy: `/v1/chat/completions` and `/v1/embeddings`, with streaming.
- Swappable, per-provider translation engines behind a compile-time-enforced `BackendEngine` port:
  **Bifrost**, **LiteLLM**, **Portkey**, and the built-in **Direct** engine, plus a deterministic
  **echo** engine so the test suite runs at `$0`.
- Encrypted provider-key vault (Fernet); applications only ever hold a low-value workspace key (`gw-…`).
- Guardrails: CEL policy rules + PII/secret detector profiles, applied to both request and response.
- Hierarchical budgets (client → workspace → user + per-model) and multi-scope rate limits.
- Routing with weighted targets, fallback chains, and circuit breakers; gradual per-provider insourcing.
- Cost attribution per client / workspace / user / component; one governance event per call.
- OpenTelemetry traces, Prometheus metrics, an optional Kafka event bus, and a live SSE React dashboard.
- Self-host stack (Docker Compose) and a production deployment (Caddy auto-TLS).

[0.1.0]: https://github.com/siva010928/agnos-proxy-oss/releases/tag/v0.1.0
