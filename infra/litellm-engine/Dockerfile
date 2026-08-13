# ─────────────────────────────────────────────────────────────────────────────
# LiteLLM proxy as a STATEFUL commodity translator ENGINE.
#
# Built from python:3.11-slim (multi-arch) - the prebuilt ghcr image is amd64-only
# and SIGILLs under Apple-Silicon emulation. Includes Prisma so store_model_in_db
# works: the gateway SYNCS provider models/creds into this proxy's own Postgres at
# runtime (mirroring bifrost/sync.py), so no keys are baked into the image or env.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl openssl ca-certificates nodejs npm \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "litellm[proxy]==1.74.9" prisma

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
