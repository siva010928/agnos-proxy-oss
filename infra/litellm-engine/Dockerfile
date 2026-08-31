# ─────────────────────────────────────────────────────────────────────────────
# LiteLLM proxy as a commodity translation ENGINE
# (stateless re: provider keys; the key is injected per request).
#
# Built from python:3.12-slim (multi-arch) - the prebuilt ghcr image is amd64-only
# and SIGILLs under Apple-Silicon emulation. This engine stores NO provider keys:
# provider credentials arrive PER REQUEST via LiteLLM "clientside credentials"
# (see config.yaml), injected by the Agnos boundary from its own vault - nothing is
# baked into the image or env. Prisma is included only for LiteLLM's OWN operational
# store (its virtual keys / spend), never provider credentials.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl openssl ca-certificates nodejs npm \
 && rm -rf /var/lib/apt/lists/*

# LiteLLM engine version. Override at build time to pin/upgrade:
#   docker compose build --build-arg LITELLM_VERSION=<x.y.z> litellm-engine
# Default tracks the security-fixed stable referenced in the README (>= 1.83.14).
ARG LITELLM_VERSION=1.83.14
RUN pip install --no-cache-dir "litellm[proxy]==${LITELLM_VERSION}" prisma

# Prisma CLI (Node) creates/migrates the DB schema on first boot; install at build
# so startup is deterministic. Remove the pip shim so the Node global CLI wins PATH.
RUN rm -f /usr/local/bin/prisma && npm install -g prisma@5.17.0
RUN SCHEMA="$(python -c 'import litellm, os; print(os.path.join(os.path.dirname(litellm.__file__), "proxy", "schema.prisma"))')" \
 && python -m prisma py fetch || true \
 && python -m prisma generate --schema "$SCHEMA" || true

WORKDIR /app
EXPOSE 4000
# config.yaml is volume-mounted by docker-compose (editable without rebuild).
ENTRYPOINT ["litellm", "--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
