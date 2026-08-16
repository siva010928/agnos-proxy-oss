# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-08-16

### Fixed
- **`pip install agnos-proxy-llm-gateway` now resolves cleanly.** The published wheel
  pinned `uvicorn[standard] <0.33`, which collided with a transitive `uvicorn>=0.35`
  (pulled via `pydantic-ai` -> `fastmcp-slim`) during a fresh pip resolve. The ceiling
  is opened to `<1.0.0`; `poetry.lock` still pins the exact tested version for Docker
  builds. (Docker / Compose / Helm installs were unaffected - they use the lock.)

## [0.3.0] - 2026-08-16

Install-anywhere and packaging release: one-command installers, a Helm chart, and a
PyPI-published `agnos` CLI, on top of a hardened test/CI baseline.

### Added
- **Installers**: interactive `install.sh` setup wizard (engine, provider keys, login
  mode, port), a `docker-compose.quickstart.yml` for a zero-config local run, a `bin/agnos`
  helper + `Makefile`, and `docs/INSTALL.md` covering every path.
- **Helm chart** (`deploy/helm/agnos-proxy`) for Kubernetes deployments.
- **PyPI package**: `agnos-proxy-llm-gateway` ships the `agnos` console-script CLI
  (`agnos init`, run, ...); PEP 621 packaging and a Trusted-Publishing release workflow.

### Changed
- Integration BVTs now self-provision their fixtures, so the full 95-test suite runs in
  CI with no deselects (now a required gate; closes the flaky-subset gap).
- `mypy` gate expanded to cover `gateway/core` and `gateway/routes`.
- Dependency upgrades: `recharts` 3, `react-router-dom` 7, `openai` (JS SDK) 3.
- Dependabot retuned to low-noise (grouped, scheduled, majors held).

### Fixed
- Nightly Playwright e2e workflow is now deterministic: provider keys are sourced from
  the environment (live "Test Connection" green paths self-skip when absent) and release
  capture galleries are excluded on CI.

## [0.2.0] - 2026-08-15

Open-source hardening and security fixes.

### Security
- Fail-closed startup: in login mode (`PREVIEW_MODE=false`) the gateway refuses to start while
  `PLATFORM_ADMIN_TOKEN`, `SESSION_SECRET`, or `DASHBOARD_ADMIN_PASSWORD` are left at their shipped defaults.
- Production compose now **requires** those secrets and ships with `PREVIEW_MODE=false` (no
  passwordless dashboard in production).
- Constant-time comparison for the platform admin token; removed shipped default secrets from `.env.example`.

### Engines
- Bundled LiteLLM engine upgraded `1.74.9` -> `1.83.14` (past the CVE fixes the README references) and
  made configurable via the `LITELLM_VERSION` build arg; engine base image aligned to Python 3.12.
- Engine image tags are now env-configurable (`BIFROST_VERSION`, `PORTKEY_VERSION`); documented in `docs/ENGINES.md`.

### Tooling / CI
- Ruff lint gate, dependency caching, and test coverage in CI.
- CodeQL, dependency review, Dependabot, and a tagged-release workflow (multi-arch GHCR image + SBOM).
- Added `CODEOWNERS`, `.editorconfig`, `.gitattributes`, `.nvmrc`, `.pre-commit-config.yaml`, and `docs/THREAT_MODEL.md`.
- `/health` now reports the running version.

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

[0.3.1]: https://github.com/siva010928/agnos-proxy-oss/releases/tag/v0.3.1
[0.3.0]: https://github.com/siva010928/agnos-proxy-oss/releases/tag/v0.3.0
[0.2.0]: https://github.com/siva010928/agnos-proxy-oss/releases/tag/v0.2.0
[0.1.0]: https://github.com/siva010928/agnos-proxy-oss/releases/tag/v0.1.0
