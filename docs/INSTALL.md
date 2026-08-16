# Installing Agnos Proxy

Ways to run Agnos Proxy, simplest first. Every path self-hosts the whole thing - the
control plane, the encrypted vault and the dashboard stay in your infrastructure.

**Requirements:** [Docker](https://docs.docker.com/get-docker/) and the Docker Compose v2
plugin (`docker compose`). The source path also needs Python 3.12 + Poetry + Node 20.

Configuration is env-driven; every knob lives in [`.env.example`](../.env.example) and the
[README configuration table](../README.md#configuration).

## Contents

- [1. One-liner (recommended)](#1-one-liner-recommended)
- [2. Prebuilt image / compose](#2-prebuilt-image--compose)
- [3. Single docker run (keyless look-around)](#3-single-docker-run-keyless-look-around)
- [4. Kubernetes (Helm)](#4-kubernetes-helm)
- [5. CLI via pipx](#5-cli-via-pipx)
- [6. The `agnos` CLI](#6-the-agnos-cli)
- [7. From source (dev)](#7-from-source-dev)
- [8. Configuration](#8-configuration)
- [Notes](#notes)

---

## 1. One-liner (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/siva010928/agnos-proxy-oss/main/install.sh | sh
```

This is the secure default. The installer:

- checks Docker + Compose are present,
- drops the quickstart compose file and the `agnos` CLI into `./agnos-proxy`,
- generates a `.env` with **strong random secrets** (`GATEWAY_MASTER_KEY`,
  `PLATFORM_ADMIN_TOKEN`, `SESSION_SECRET`, `DASHBOARD_ADMIN_PASSWORD`),
- runs the keyless **`echo`** engine (deterministic, $0, no provider keys), with
  `PREVIEW_MODE=false` so login is enforced,
- pulls the published image, waits for `/health`, then prints the dashboard URL and the
  generated admin login.

It never overwrites an existing `.env`, so it is safe to re-run. When you are ready for
real traffic, set `ENGINE=direct` and add a provider key (see [Configuration](#8-configuration)).

## 2. Prebuilt image / compose

Pull the published multi-arch gateway image (pin a release for reproducibility):

```bash
docker pull ghcr.io/siva010928/agnos-proxy:latest   # or a pinned release, e.g. :v0.2.0
```

Bring up the no-build stack (gateway + Postgres + Redis) with
[`deploy/docker-compose.quickstart.yml`](../deploy/docker-compose.quickstart.yml). First
create a `.env` **next to the compose file** (Compose resolves `env_file:` and `${VAR}`
relative to the compose file's directory). A minimal, secure `.env`:

```bash
# from a clone, write the .env beside the quickstart compose:
cat > deploy/.env <<EOF
GATEWAY_MASTER_KEY=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')
PLATFORM_ADMIN_TOKEN=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')
SESSION_SECRET=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')
DASHBOARD_ADMIN_USER=admin
DASHBOARD_ADMIN_PASSWORD=$(openssl rand -base64 18 | tr '+/' '-_' | tr -d '=')
ENGINE=echo
PREVIEW_MODE=false
EOF

docker compose -f deploy/docker-compose.quickstart.yml up -d --pull always
```

Then open **http://localhost:8090/**. Notes:

- `PREVIEW_MODE=false` (the default) requires the three secrets above - the gateway
  refuses to boot on shipped defaults. For a no-secret look-around instead, set
  `PREVIEW_MODE=true` and only `GATEWAY_MASTER_KEY` is required.
- Pin the image with `AGNOS_VERSION` (e.g. `AGNOS_VERSION=v0.2.0`); switch engines with
  `ENGINE`. Provider sidecars (bifrost / litellm / portkey) are optional - see
  [docs/ENGINES.md](ENGINES.md).
- Prefer keeping the compose file and its `.env` in the same directory (this is what the
  one-liner does), or pass `--project-directory .` if your `.env` is elsewhere.

## 3. Single docker run (keyless look-around)

The gateway image itself is a single container, but it **needs a Postgres** for its
governance store (it creates its schema on boot). So a self-contained run is two commands
on a shared network - for anything more, prefer the compose path above.

```bash
docker network create agnos-net

docker run -d --name agnos-pg --network agnos-net \
  -e POSTGRES_USER=agnos -e POSTGRES_PASSWORD=agnos -e POSTGRES_DB=agnos_gateway \
  postgres:16

docker run -d --name agnos-gw --network agnos-net -p 8090:8090 \
  -e ENGINE=echo \
  -e PREVIEW_MODE=true \
  -e GATEWAY_MASTER_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')" \
  -e GOVERNANCE_DB_URL="postgresql+asyncpg://agnos:agnos@agnos-pg:5432/agnos_gateway" \
  ghcr.io/siva010928/agnos-proxy:latest
```

`ENGINE=echo` needs no provider keys and `PREVIEW_MODE=true` opens the dashboard without a
login wall - ideal to click around. Redis is optional (omit it; distributed rate-limiting
just stays off). Tear it down with `docker rm -f agnos-gw agnos-pg && docker network rm agnos-net`.

> There is no single-container `docker run` that boots without a database. For a true
> one-command experience use the [one-liner](#1-one-liner-recommended) or the
> [compose path](#2-prebuilt-image--compose).

## 4. Kubernetes (Helm)

Deploy the gateway (plus a bundled Postgres and Redis) to any Kubernetes cluster with
the chart in [`deploy/helm/agnos-proxy`](../deploy/helm/agnos-proxy). It generates
strong secrets on first install and reuses them across upgrades (via `lookup`), so a
`helm upgrade` never rotates your `GATEWAY_MASTER_KEY`.

```bash
# from a repo clone - keyless echo engine, bundled Postgres + Redis:
helm install agnos deploy/helm/agnos-proxy -n agnos --create-namespace

# expose it on your host via an Ingress:
helm install agnos deploy/helm/agnos-proxy -n agnos --create-namespace \
  --set ingress.enabled=true,ingress.host=agnos.example.com
```

Follow the printed NOTES for the URL, then read the generated admin password:

```bash
kubectl -n agnos get secret agnos-agnos-proxy-secrets \
  -o jsonpath="{.data.DASHBOARD_ADMIN_PASSWORD}" | base64 -d; echo
```

Common overrides (`--set key=value` or your own `-f values.yaml`):

| Key | Default | Purpose |
|---|---|---|
| `engine` | `echo` | Backend engine: echo, direct, bifrost, litellm, portkey |
| `previewMode` | `false` | true opens the dashboard without a login wall |
| `image.tag` | chart appVersion | Pin the gateway image, e.g. `v0.2.0` |
| `postgres.enabled` | `true` | Bundle Postgres (false + `governanceDbUrl` for an external DB) |
| `redis.enabled` | `true` | Bundle Redis for distributed rate-limiting |
| `providerKeys.ANTHROPIC_API_KEY` | (unset) | Provider keys for non-echo engines |
| `ingress.enabled` / `ingress.host` | `false` / `agnos.local` | Expose via Ingress |
| `existingSecret` | `""` | Use your own Secret instead of the managed one |

Notes:

- Both the liveness and readiness probes hit `GET /health`. `/health/ready` additionally
  pings the backend engine and returns 503 on the keyless `echo` engine, so it is
  deliberately not used for the k8s probes.
- Preview the rendered manifests without a cluster: `helm template agnos deploy/helm/agnos-proxy`.
- For production, prefer an external managed Postgres: `--set postgres.enabled=false`
  and `--set governanceDbUrl=postgresql+asyncpg://user:pass@host:5432/db`.

## 5. CLI via pipx

Install just the `agnos` CLI from PyPI (no Docker needed to install it):

```bash
pipx install agnos-proxy-llm-gateway
```

Then generate a `.env` and start the stack:

```bash
agnos init          # wizard: engine/login/port + strong secrets -> writes .env + compose
agnos up            # docker compose up -d (pulls the published image)
agnos open          # open the dashboard in your browser
```

`agnos init` refuses to overwrite an existing `.env` unless you pass `--force`, and
`--no-input` keeps secure defaults (engine `echo`, login required, port 8090) for
scripted installs. The pip-installed `agnos` and the POSIX wrapper below expose the
same subcommands (`up`, `down`, `logs`, `status`, `update`, `config`, `open`,
`version`); both shell out to `docker compose`, so Docker is still required to *run*
the stack.

## 6. The `agnos` CLI

[`bin/agnos`](../bin/agnos) is a thin POSIX-sh wrapper over the quickstart compose stack.
The one-liner installer drops it into your install directory as `./agnos`. To install it
system-wide:

```bash
sudo curl -fsSL https://raw.githubusercontent.com/siva010928/agnos-proxy-oss/main/bin/agnos \
  -o /usr/local/bin/agnos && sudo chmod +x /usr/local/bin/agnos
```

Run it from your install directory (the one holding `docker-compose.yml` + `.env`) or from
a repo clone:

| Command | What it does |
|---|---|
| `agnos init` | Create a `.env` (strong secrets) + compose file (`--force` to overwrite) |
| `agnos up` | Start the stack (pulls the published image) |
| `agnos down` | Stop the stack |
| `agnos logs [service]` | Follow logs (all, or one, e.g. `agnos logs gateway`) |
| `agnos status` | Container status + gateway `/health` |
| `agnos update` | Pull the latest image and restart |
| `agnos config` | Open `.env` in `$EDITOR` |
| `agnos open` | Open the dashboard in your browser |
| `agnos version` | Print the gateway version (from `/health`) + CLI version |
| `agnos help` | Show usage |

## 7. From source (dev)

For hacking on the gateway or dashboard, run it from a clone:

```bash
git clone https://github.com/siva010928/agnos-proxy-oss.git
cd agnos-proxy-oss
cp .env.example .env                 # set GATEWAY_MASTER_KEY (any passphrase) + keys
poetry install --with dev
./scripts/start_local.sh             # infra + dashboard build + gateway on :8090
```

`make dev` runs the same script; `make help` lists the developer targets (`test`, `lint`,
`typecheck`, `build-dashboard`, `sanity`, ...). Full dev guide:
[CONTRIBUTING.md](../CONTRIBUTING.md).

## 8. Configuration

All configuration is environment variables. Start from [`.env.example`](../.env.example)
and the [README configuration table](../README.md#configuration).

**Engine choice (`ENGINE`):**

- `echo` - deterministic, $0, **no provider keys** (safe first run / look-around).
- `direct` - in-process adapters (Anthropic / Bedrock / Gemini / OpenAI-compatible), no
  sidecar; add the matching provider key(s) to `.env`.
- `bifrost` | `litellm` | `portkey` - optional translator sidecars; configure and swap per
  provider as described in [docs/ENGINES.md](ENGINES.md).

**Security must-dos for production:**

- Set `PREVIEW_MODE=false` (require login).
- Provide strong, unique `GATEWAY_MASTER_KEY`, `PLATFORM_ADMIN_TOKEN`, `SESSION_SECRET`
  and `DASHBOARD_ADMIN_PASSWORD`. The gateway refuses to start with `PREVIEW_MODE=false`
  while these are left at their shipped defaults - the one-liner installer generates real
  ones for you.
- Never commit `.env` (it is git-ignored). Generate a master key without Python via
  `openssl rand -base64 32 | tr '+/' '-_' | tr -d '='`.

## Notes

- The published image at `ghcr.io/siva010928/agnos-proxy` must be **public** to pull
  anonymously (no `docker login`). If you host a private fork, run `docker login ghcr.io`
  first.
- **Status: pre-1.0.** Pin a [release](https://github.com/siva010928/agnos-proxy-oss/releases)
  (`AGNOS_VERSION=v0.2.0`) rather than `latest` for stability.
