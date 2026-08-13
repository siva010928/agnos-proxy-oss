# Gateway sanity suite

Real-provider, real-wire sanity checks for the Agnos Proxy. Every check
runs through the **live gateway** using the **OpenAI SDK** (exactly how a component
calls it), then reads governance back to prove usage/cost were recorded. This is
the acceptance gate before a demo, a release, or any engine/DirectEngine change.

It exercises **both engines** (Bifrost + our owned DirectEngine), **all three AWS
Bedrock auth modes** (static / bearer / SSO), LLM + embedding calls, governance
emission, framework-native exceptions, provider-native param passthrough, engine
parity, and pricing sync - for every provider whose credentials are present.

## Prerequisites

1. The gateway running (default `http://localhost:8090`):
   ```bash
   python gateway_server.py
   ```
2. Provider credentials in an env file. By default the suite reads the gateway
   `.env` (repo root). Point at any other provider env file with `--env-file PATH`
   or `$SANITY_ENV_FILE` (it wins per-key over the gateway `.env`):
   ```bash
   cp providers.env-template .env   # then fill in the providers you want
   ```
   A provider with no credentials is **SKIPPED**, never failed. The catalog source
   defaults to the repo's own `data/provider_catalog.yaml` (`$SANITY_CATALOG` to override).

## Run

```bash
python -m scripts.sanity all                 # everything, one summary
python -m scripts.sanity provision           # just create the sanity tenant
python -m scripts.sanity calls               # chat + embeddings through the wire
python -m scripts.sanity governance          # usage/cost recorded per call
python -m scripts.sanity exceptions          # openai.* native errors
python -m scripts.sanity passthrough         # provider-native extra params
python -m scripts.sanity authmodes           # bedrock static / bearer / sso
python -m scripts.sanity parity              # bifrost vs direct, scored
python -m scripts.sanity pricing             # synced pricing + cost applied
python -m scripts.sanity observability       # metrics + governance row + Jaeger trace
python -m scripts.sanity filters             # hierarchical cascading filter facets
python -m scripts.sanity catalog --full      # reachability across the Agnos Proxy catalog
```

Options: `--engine bifrost|direct|both` (default both) · `--env-file PATH` ·
`--target KEY` (repeatable; e.g. `--target bedrock-static`) · `--max-models N` ·
`--full`. Override the gateway with `SANITY_GATEWAY_URL` / `SANITY_ADMIN_TOKEN`.

## What each command proves

| Command | Proves |
|---|---|
| `provision` | Idempotently creates the `sanity-co` client + one workspace per (provider, engine, auth-mode) with aliases, provider creds, and a minted key. |
| `calls` | Chat **and** embeddings work through the gateway's OpenAI-compatible wire, on both engines. |
| `governance` | Every call emits an **attributed usage/cost record** - looked up by the response's `X-Gateway-Correlation-Id` in `/admin/request-logs`, with input/output tokens, provider, and cost asserted. We don't just check the call - we check the governance it produced. |
| `exceptions` | A component using its framework's OpenAI interface catches the **right native exception**: `openai.NotFoundError` (unknown model), `openai.AuthenticationError` (bad key), `openai.RateLimitError` (429), `openai.BadRequestError` (context overflow, provider-dependent). |
| `passthrough` | Provider-native fields the generic OpenAI shape drops survive through the boundary - e.g. Anthropic **prompt caching** (`extra_body={"prompt_cache": true}` → cache-create then cache-read tokens), plus benign extras (`metadata`, `seed`) are accepted. |
| `authmodes` | AWS Bedrock authenticates via DirectEngine in **static**, **bearer** (`AWS_BEARER_TOKEN_BEDROCK`), and **SSO** (profile) modes. SSO SKIPs if no profile is configured. |
| `parity` | The same prompt through **Bifrost vs DirectEngine** returns an equivalent result (scored: `identical`/`high`/`moderate`), with the latency delta - the proof an engine swap is safe. |
| `pricing` | Model pricing is **synced from source** (models carry non-zero prices) and **applied** to governed requests (recorded `cost_usd > 0`). |
| `observability` | The full request lifecycle is captured: Prometheus `gateway_*` series increment, the governance row carries tokens/cost/latency/engine, and a **Jaeger trace** (parent `gateway.chat` + child stage spans) exists - with the error path marked `status=ERROR`. |
| `filters` | The **parent-aware cascading facets** behind the Logs/Analytics/Routing filter bars: picking a client narrows workspaces to that client, each facet is scoped by the *other* selections (never itself, so it stays re-pickable), and unscoped facets union the full provider/component enum while scoped facets are purely data-driven. |
| `catalog` | Reachability map: probes catalog model ids per provider/engine and classifies **accessible (PASS)**, **unavailable/deprecated/region-mismatch (SKIP)**, **wiring defect (FAIL)**. |

## Interpreting results

- **PASS** - the check succeeded.
- **FAIL** - a real defect in the gateway/wiring. The `all` summary lists every failure; the process exit code is non-zero iff there is ≥1 FAIL (CI-friendly).
- **SKIP** - a precondition was absent (missing creds, a provider with no embeddings API, provider-side unavailability like quota/429/503/timeout, a deprecated or region-mismatched model). Never a failure - provider realities are not our bug.

## Files

```
scripts/sanity/
  __main__.py       unified CLI (python -m scripts.sanity ...)
  _env.py           env loading + provider/auth/model matrix from creds
  _client.py        admin provisioning + OpenAI-SDK factory + governance readback
  _probes.py        low-level chat/embedding probes (real wire, defensive)
  _reporting.py     PASS/FAIL/SKIP result types + console formatting
  commands.py       the command implementations
  README.md         this file
  .sanity_state.json   cached minted keys (gitignored; safe to delete)
```

Credentials are read at runtime only; nothing here is committed. `.sanity_state.json`
caches the minted sanity workspace keys so re-runs are fast - delete it to re-mint.
