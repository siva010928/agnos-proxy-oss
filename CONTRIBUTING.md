# Contributing to Agnos Proxy

Thanks for your interest in improving Agnos Proxy. This gateway is built to be
extended - new engine adapters, providers, guardrails, and dashboard views are
exactly the kind of contribution we want. This guide gets you from clone to merged PR.

By participating you agree to our [Code of Conduct](CODE_OF_CONDUCT.md).

## TL;DR

```bash
git clone https://github.com/siva010928/agnos-proxy-oss.git
cd agnos-proxy-oss
cp .env.example .env                 # set GATEWAY_MASTER_KEY + provider keys
poetry install --with dev            # backend + test deps
./scripts/start_local.sh             # full stack (or: docker compose up -d + uvicorn)
poetry run pytest -m "not live and not integration"   # fast unit suite ($0)
```

## Ways to contribute

- **Report a bug** - open a [Bug report](https://github.com/siva010928/agnos-proxy-oss/issues/new/choose). Include repro steps, expected vs actual, logs.
- **Request a feature** - open a [Feature request](https://github.com/siva010928/agnos-proxy-oss/issues/new/choose) describing the problem first, then your idea.
- **Add an engine adapter** (Bifrost/LiteLLM/Portkey/Direct are just adapters) - see [docs/ENGINES.md](docs/ENGINES.md#add-a-new-engine-adapter-any-oss-model-gateway).
- **Add a provider, guardrail detector, or dashboard view.**
- **Improve docs** - README, `docs/`, code comments.
- **Triage** - reproduce issues, add detail, suggest fixes.

Good first issues are labelled [`good first issue`](https://github.com/siva010928/agnos-proxy-oss/labels/good%20first%20issue).

## Development setup

Prereqs: **Python 3.12**, **Poetry**, **Node 20+**, **Docker** (for Postgres and the sidecar engines).

```bash
poetry install --with dev            # installs the gateway + test tooling
cp .env.example .env                 # then set GATEWAY_MASTER_KEY (see below) + keys
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> GATEWAY_MASTER_KEY

# Bring everything up (Docker infra + build dashboard + gateway on :8090):
./scripts/start_local.sh
# ...or run the app directly against Docker infra:
docker compose up -d postgres bifrost redis
poetry run uvicorn gateway.app:app --reload --port 8090

# Frontend dev server (hot reload) proxied to the gateway:
cd frontend && npm install && npm run dev
```

Dashboard: http://localhost:8090/app/ . In `PREVIEW_MODE=true` (default) it opens without a login wall.

## Running the tests

The suite has three tiers:

| Command | What it runs | Needs |
|---|---|---|
| `poetry run pytest -m "not live and not integration"` | **Unit + logic** (this is what CI runs) | Postgres only, `ENGINE=echo`, **$0** |
| `poetry run pytest -m integration` | **BVT** end-to-end against a **running** gateway (auto-swaps to `echo`) | A gateway on `:8090` (`./scripts/start_local.sh`) |
| `poetry run pytest -m live` | **Real-provider smoke** (costs a few cents) | A running gateway + real provider keys in `.env` |

Integration tests **auto-skip** when no gateway is reachable, so the default `pytest` run is safe anywhere.

The anti-coupling test (`tests/test_anti_coupling.py`) enforces the `BackendEngine` boundary - if your change leaks an engine-specific field past the port, it fails on purpose.

## Pull request flow

1. **Open (or comment on) an issue first** for anything non-trivial, so we can agree on direction.
2. Fork, then branch: `feat/<short-name>`, `fix/<short-name>`, or `docs/<short-name>`.
3. Make the change. Keep PRs focused; one concern per PR.
4. **Before pushing:**
   - `poetry run pytest -m "not live and not integration"` is green
   - `cd frontend && npm run build` succeeds (if you touched the dashboard)
   - No secrets, real keys, or personal data in the diff (`.env` is git-ignored - keep it that way)
5. Open the PR against `main`, fill in the template, and link the issue (`Closes #123`).
6. CI (unit tests + dashboard build) must pass. A maintainer reviews; address feedback with new commits (we squash-merge, so no need to force-push).

### Commit / PR style

- Conventional-ish subjects: `feat(engine): add Ollama adapter`, `fix(routing): ...`, `docs: ...`.
- Explain the **why** in the body, not just the what.
- Update docs/tests in the same PR as the behavior change.

## Coding standards

- **Python**: type hints on public functions; keep the `BackendEngine` contract clean (OpenAI in / OpenAI out; never leak engine internals past `EngineResult`). Prefer small, testable functions.
- **Config is env-driven** (`gateway/config.py`) - never hardcode secrets, hosts, or keys.
- **Frontend**: TypeScript + React; run `npm run build` before pushing.
- **Docs style**: use plain hyphens (`-`), not em dashes.

## Reporting security issues

Please do **not** open a public issue for vulnerabilities. See [SECURITY.md](SECURITY.md) - report privately via GitHub's "Report a vulnerability".

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
