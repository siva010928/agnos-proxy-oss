#!/usr/bin/env bash
# Demo Script - Agnos Proxy
# Cold-start sequenced run that proves the headline beats end-to-end.
#
# Prerequisites:
#   * Docker running (postgres, redis, kafka, bifrost containers from docker-compose up -d)
#   * .env populated (GATEWAY_MASTER_KEY, AWS_*, ANTHROPIC_API_KEY, GEMINI_API_KEY)
#   * Gateway started (poetry run python gateway_server.py) at localhost:8090
#   * Frontend built (cd frontend && npm run build)
#
# Usage:  ./demo/demo_script.sh
# Each step prints what it's about to do and waits for [Enter] before running,
# so the operator can narrate. Pass -y to skip the prompts (CI run).

set -euo pipefail
cd "$(dirname "$0")/.."

GW="${GATEWAY_URL:-http://localhost:8090}"
ADMIN="${PLATFORM_ADMIN_TOKEN:-platform-admin-secret}"
H_ADMIN=(-H "X-Admin-Token: $ADMIN" -H "Content-Type: application/json")

YES=0
[[ "${1:-}" == "-y" ]] && YES=1

step() {
    echo
    echo "════════════════════════════════════════════════════════════════"
    echo "  $1"
    echo "════════════════════════════════════════════════════════════════"
    [[ $YES == 0 ]] && read -p "  press [Enter] to run, ^C to abort … " _
}

step "1. Health check (gateway + Bifrost + Postgres + Redis + Kafka + Grafana)"
curl -sS --max-time 3 "$GW/health" | python -m json.tool
curl -sS --max-time 3 "$GW/health/ready" | python -m json.tool
docker ps --format '{{.Names}}\t{{.Status}}' | head -10

step "2. Engine swap proof (live, governance-unaffected)"
echo "→ swap to echo (deterministic in-process, \$0 upstream)"
curl -sS "${H_ADMIN[@]}" -X POST "$GW/admin/engine" -d '{"engine":"echo"}' | python -m json.tool
echo "→ swap back to bifrost (default Go sidecar)"
curl -sS "${H_ADMIN[@]}" -X POST "$GW/admin/engine" -d '{"engine":"bifrost"}' | python -m json.tool

step "3. Server-side validation (no half-cooked saves)"
echo "→ pricing.model_substr=\"\" must 422 (would have wiped the price table)"
curl -sS -w "\n%{http_code}\n" "${H_ADMIN[@]}" -X POST "$GW/admin/pricing" -d '{"model_substr":"","input_per_1k":1,"output_per_1k":1}'
echo
echo "→ key.expires_at=\"never\" must 422 (would have silently nulled to forever)"
curl -sS -w "\n%{http_code}\n" "${H_ADMIN[@]}" -X POST "$GW/admin/workspaces/ws-novatech-payments/keys" -d '{"expires_at":"never","roles":["member"]}'
echo
echo "→ chat_models alias pointing at unconfigured provider must 422 (resolvability)"
curl -sS -w "\n%{http_code}\n" "${H_ADMIN[@]}" -X POST "$GW/admin/workspaces/ws-novatech-payments" -d '{"chat_models":{"bad-alias":[{"provider":"openai","model_id":"gpt-4o"}]}}'

step "4. NovaTech tenant - provision via API (workspace + 3 components + 3 providers + 3 keys)"
.venv/bin/python scripts/seed_tenant.py --reset

step "5. Headline: NovaTech components via base_url ONLY - no SDK, no shim"
echo "Each component uses vanilla openai.OpenAI(base_url=...) against this gateway."
echo "Three different REAL providers (Bedrock + Anthropic + Gemini); attribution rolls up."
.venv/bin/python demo/gateway_baseurl_demo.py

step "6. External-producer governance ingest"
echo "→ external service emits a completion event via POST /governance/events"
curl -sS "${H_ADMIN[@]}" -X POST "$GW/governance/events" -d '{
  "event_kind":"completion",
  "correlation_id":"req-demo-ingest",
  "payload":{"workspace_id":"novatech","provider":"anthropic","model":"claude-sonnet-4-5",
             "input_tokens":120,"output_tokens":80,"cost_usd":0.005,"latency_ms":380,
             "component":"external-batch-job"}
}' | python -m json.tool

step "7. Consume from Kafka (proves the same envelope reaches downstream consumers)"
.venv/bin/python scripts/kafka_consume.py --max 3 --from-end --timeout 5 &
sleep 1
# Trigger one fresh event so the consumer has something to read
curl -sS "${H_ADMIN[@]}" -X POST "$GW/governance/events" -d '{
  "event_kind":"completion",
  "correlation_id":"req-demo-kafka",
  "payload":{"workspace_id":"novatech","provider":"bedrock","model":"claude-sonnet-4-5",
             "input_tokens":50,"output_tokens":25,"cost_usd":0.002,"latency_ms":150,
             "component":"novatech.gateway"}
}' >/dev/null
wait

step "8. Live attribution rollup (per-component cost across the 3 NovaTech components)"
curl -sS "${H_ADMIN[@]}" "$GW/admin/cost?group_by=component&workspace=novatech" | python -m json.tool

step "9. Trust ladder - run all four test suites"
echo "→ unit ($0)"
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
echo
echo "→ BVT integration ($0, ENGINE=echo)"
.venv/bin/python -m pytest tests/integration -m integration -q 2>&1 | tail -3
echo
echo "→ Playwright UI"
( cd frontend && npx playwright test --reporter=line 2>&1 | tail -3 )
echo
echo "→ capped live smoke (~\$0.001)"
.venv/bin/python -m pytest -m live tests/integration/test_live_smoke.py -q 2>&1 | tail -3

step "Done. Open http://localhost:8090/app for the dashboard."
echo "  · /app          - overview + live SSE feed"
echo "  · /app/cost     - analytics with per-component rollup"
echo "  · /app/admin/*  - operator console (Components, Routing, Keys, Providers, Pricing)"
echo "  · /app/guardrails/rules - visual Rule Builder"
echo "  · http://localhost:3001  - Grafana (provisioned dashboard + SLO alerts)"
echo "  · http://localhost:8085  - Kafka UI (topic agnos-proxy.governance.v1)"
