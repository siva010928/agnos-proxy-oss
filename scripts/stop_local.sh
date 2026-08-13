#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# stop_local.sh - stop the local Agnos Proxy stack.
#
#   ./scripts/stop_local.sh          # stop the gateway server + the infra containers
#   ./scripts/stop_local.sh --keep-infra   # stop only the gateway server (leave Docker up)
#   ./scripts/stop_local.sh --down          # also `compose down` (remove containers; keeps volumes)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
GW_PID="$ROOT/.gateway.pid"
PORT="$(grep -E '^GATEWAY_PORT=' .env 2>/dev/null | cut -d= -f2 || true)"; PORT="${PORT:-8090}"
KEEP_INFRA=0; DOWN=0
for a in "$@"; do [ "$a" = "--keep-infra" ] && KEEP_INFRA=1; [ "$a" = "--down" ] && DOWN=1; done

say() { printf "\033[1;36m▶ %s\033[0m\n" "$1"; }

say "Stopping the gateway server (:$PORT)"
if [ -f "$GW_PID" ] && kill "$(cat "$GW_PID")" 2>/dev/null; then echo "  stopped pid $(cat "$GW_PID")"; else
  P="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"; [ -n "$P" ] && { kill $P 2>/dev/null || true; echo "  stopped pid $P"; } || echo "  (no gateway process found)"
fi
rm -f "$GW_PID"

if [ "$KEEP_INFRA" = "1" ]; then
  say "Leaving Docker containers running (--keep-infra)"
elif [ "$DOWN" = "1" ]; then
  say "compose down (removing containers; volumes/data kept)"
  docker compose down
else
  say "Stopping Docker containers (data kept; start again with start_local.sh)"
  docker compose stop
fi
printf "\033[92m✓ stopped\033[0m\n"
