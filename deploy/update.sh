#!/usr/bin/env bash
# Agnos Proxy - One-command redeploy (run from repo root on VM)
set -euo pipefail

echo "=== Pulling latest ==="
git pull origin main

echo "=== Rebuilding ==="
# NOTE: pass --env-file .env explicitly. With `-f deploy/docker-compose.prod.yml`,
# Compose resolves interpolation vars (e.g. ${DOMAIN} used by the caddy service)
# from the compose file's directory (deploy/), which has no .env - so DOMAIN would
# fall back to "localhost" and Caddy would serve a localhost cert, breaking TLS on
# the real hostname. Pointing at the repo-root .env keeps HTTPS working across redeploys.
docker compose -f deploy/docker-compose.prod.yml --env-file .env up -d --build --remove-orphans

echo "=== Waiting for health ==="
for i in $(seq 1 90); do
    curl -sf http://localhost:8090/health >/dev/null 2>&1 && { echo "Healthy after ${i}s"; break; } || sleep 2
done

echo "=== Status ==="
docker compose -f deploy/docker-compose.prod.yml --env-file .env ps
echo ""
echo "Redeploy complete: $(curl -sf http://localhost:8090/health)"
