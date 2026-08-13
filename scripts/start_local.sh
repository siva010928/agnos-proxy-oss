#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# start_local.sh - bring the WHOLE Agnos Proxy up locally, from cold.
#
#   ./scripts/start_local.sh            # start everything (build frontend only if missing)
#   ./scripts/start_local.sh --build    # force-rebuild the dashboard UI first
#   ./scripts/start_local.sh --logs     # tail the gateway log after it starts
#
# It is idempotent - safe to run anytime (e.g. after a reboot). It:
#   1. makes sure Docker is running (starts Docker Desktop on macOS if needed),
#   2. brings up all infra containers (main compose),
#   3. waits for Postgres + Bifrost to be healthy,
#   4. builds the React dashboard (frontend/dist) if it isn't built yet,
#   5. (re)starts the gateway server on :8090, serving the UI + API,
#   6. waits for /health and prints all the URLs.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GW_LOG="$ROOT/.gateway.log"
GW_PID="$ROOT/.gateway.pid"
PORT="$(grep -E '^GATEWAY_PORT=' .env 2>/dev/null | cut -d= -f2 || true)"; PORT="${PORT:-8090}"
PY="$ROOT/.venv/bin/python"
FORCE_BUILD=0; TAIL_LOGS=0
for a in "$@"; do [ "$a" = "--build" ] && FORCE_BUILD=1; [ "$a" = "--logs" ] && TAIL_LOGS=1; done

say()  { printf "\n\033[1;36m▶ %s\033[0m\n" "$1"; }
ok()   { printf "  \033[92m✓ %s\033[0m\n" "$1"; }
warn() { printf "  \033[93m! %s\033[0m\n" "$1"; }

# ── 1. Docker ────────────────────────────────────────────────────────────────
say "1/6  Docker"
if ! docker info >/dev/null 2>&1; then
  if [ "$(uname)" = "Darwin" ]; then
    warn "Docker not running - launching Docker Desktop ..."
    open -a Docker || true
    for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  fi
  docker info >/dev/null 2>&1 || { echo "Docker is not available - start Docker and re-run."; exit 1; }
fi
ok "Docker is running"

# ── 2. Infra containers ──────────────────────────────────────────────────────
say "2/6  Infra containers (postgres, redis, kafka, bifrost, portkey, litellm-engine, jaeger, prometheus, grafana)"
docker compose up -d
ok "compose up issued"

# ── 3. Wait for the data plane to be healthy ─────────────────────────────────
say "3/6  Waiting for Postgres + Bifrost to be healthy"
wait_healthy() {
  local name="$1"
  for i in $(seq 1 60); do
    local s; s="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || echo missing)"
    [ "$s" = "healthy" ] && { ok "$name healthy"; return 0; }
    [ "$s" = "none" ] && { ok "$name up (no healthcheck)"; return 0; }
    sleep 2
  done
  warn "$name not healthy yet - continuing anyway"
}
wait_healthy agnos-proxy-gateway-pg
wait_healthy agnos-proxy-bifrost

# ── 4. Frontend (dashboard UI) ───────────────────────────────────────────────
say "4/6  Dashboard UI"
if [ "$FORCE_BUILD" = "1" ] || [ ! -f frontend/dist/index.html ]; then
  [ -d frontend/node_modules ] || { warn "installing frontend deps ..."; (cd frontend && npm install); }
  warn "building frontend/dist ..."
  (cd frontend && npm run build)
  ok "frontend built"
else
  ok "frontend/dist already built (use --build to rebuild)"
fi

# ── 5. Gateway server (serves UI + API on :$PORT) ────────────────────────────
say "5/6  Gateway server on :$PORT"
[ -x "$PY" ] || { echo "no venv at $PY - create it (python -m venv .venv && .venv/bin/pip install -e .)"; exit 1; }
EXISTING="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$EXISTING" ]; then warn "stopping existing process on :$PORT (pid $EXISTING)"; kill $EXISTING 2>/dev/null || true; sleep 2; fi
nohup "$PY" gateway_server.py > "$GW_LOG" 2>&1 &
echo $! > "$GW_PID"
ok "gateway started (pid $(cat "$GW_PID")), logs -> $GW_LOG"

# ── 6. Readiness ─────────────────────────────────────────────────────────────
say "6/6  Waiting for the gateway to answer /health"
UP=0
for i in $(seq 1 40); do
  if [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://localhost:$PORT/health" 2>/dev/null)" = "200" ]; then UP=1; break; fi
  sleep 1
done
if [ "$UP" = "1" ]; then ok "gateway healthy"; else warn "gateway not answering yet - check $GW_LOG"; fi

cat <<EOF

================================================================
  Agnos Proxy - local stack is up.

  Preview link (dashboard):  http://localhost:$PORT/
  Live dashboard:            http://localhost:$PORT/app/
  Playground:                http://localhost:$PORT/app/playground
  Docs:                      http://localhost:$PORT/app/docs

  Observability:
    Jaeger traces      http://localhost:16686
    Grafana            http://localhost:3001
    Prometheus         http://localhost:9090
    Kafka UI           http://localhost:8085

  Gateway log:   tail -f $GW_LOG
  Stop it all:   ./scripts/stop_local.sh
================================================================
EOF

[ "$TAIL_LOGS" = "1" ] && tail -f "$GW_LOG"
exit 0
