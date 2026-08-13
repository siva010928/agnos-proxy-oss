# Engines - run many, pick the best per provider, swap at runtime

Agnos Proxy's data plane (the thing that translates the OpenAI request shape into
each provider's shape and back) is a **swappable commodity behind one fixed port**.
The control plane - auth, guardrails, budgets, the encrypted key vault, cost/audit -
never moves. This doc explains, for self-hosters and local dev:

- the engines that ship, and what each is good at
- how to **configure** each engine
- how to **swap** the engine - globally (UI / API / env) and **per provider**
- how to route the **best engine per provider / client / workspace**
- how to **add a brand-new engine adapter** (any OSS model gateway) to the port

> TL;DR - “own the control plane, swap the translator.” The engine holds **no keys**:
> the control plane decrypts one provider key from the vault and injects it **per
> request, in flight**. A compromised engine has no key store to dump.

---

## The engines that ship

| Engine | Runtime | What it's best at | Holds keys? | Needs |
|---|---|---|---|---|
| **bifrost** (default) | Go sidecar | Blazing-fast translation for most traffic (~µs overhead); per-request direct-key | No | `bifrost` container |
| **litellm** | Python sidecar | **Widest** provider matrix - **IBM watsonx, Databricks**, Snowflake, SageMaker, 100+ | No (client-side creds per call) | `litellm-engine` container |
| **portkey** | Node sidecar | Clean stateless OSS gateway adapter | No (headers per call) | `portkey` container |
| **direct** | Python, in-process | Owned escape hatch / always-ready fallback (boto3 + SDKs) | No (key never leaves the boundary) | nothing |
| **echo** | Python, in-process | Deterministic **$0** test upstream - the 170+ test suite runs on it | No (no upstream) | nothing |

Metadata source of truth: [`gateway/core/engine_catalog.py`](../gateway/core/engine_catalog.py).
The accepted set is `av.ENGINES` in [`gateway/core/admin_validation.py`](../gateway/core/admin_validation.py).

**All engines are stateless.** bifrost, litellm and portkey are integrated in their
stateless / bring-your-own-key mode (Bifrost direct-key, LiteLLM client-side creds,
Portkey headers); direct and echo run in-process. None store a provider key - the
control plane decrypts one key from the vault and injects it per request, in flight, so
swapping engines never moves keys.

**Only available engines are shown.** `/app/engine` (and `GET /admin/engine/catalog`)
health-probe each engine: direct and echo are always available (in-process); bifrost,
litellm and portkey appear only when their sidecar answers. A deploy that runs just one
sidecar shows only that engine (plus direct/echo) - no dangling options. Bring a sidecar
up on demand with `docker compose up -d bifrost` (or `litellm-engine` / `portkey`) and it
appears automatically.

**Scope today (extensible).** The Direct engine currently ships adapters for a limited
set of providers (Anthropic, Bedrock, Gemini / Vertex, OpenAI-compatible - see
`gateway/engines/direct_*.py`); the sidecar engines cover far more (LiteLLM 100+). Both
the provider set and the engine set are meant to grow - see "Add a new engine adapter".

---

## Configure each engine

All config is environment variables (see [`.env.example`](../.env.example)). The
sidecar engines are already wired in `docker-compose.yml`.

```bash
# Global default engine (used when a provider has no per-provider override)
ENGINE=bifrost                 # bifrost | litellm | portkey | direct | echo

# Sidecar engine endpoints (containers from docker-compose.yml)
BIFROST_URL=http://localhost:8099
LITELLM_ENGINE_URL=http://localhost:4100
LITELLM_ENGINE_KEY=sk-...          # the sidecar's own admin key (not a provider key)
PORTKEY_URL=http://localhost:8787
# direct + echo are in-process - no URL needed
```

Provider credentials themselves live **only** in the encrypted vault (seeded from
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `AWS_*`, … on first boot, then encrypted with
`GATEWAY_MASTER_KEY`). Engines never read them from env at request time - the control
plane injects the decrypted key per call.

---

## Swap the engine - three ways

Swapping is **governance-neutral**: auth, guardrails, budgets, attribution and the
audit trail are identical before and after. An anti-coupling test
([`tests/test_anti_coupling.py`](../tests/test_anti_coupling.py)) fails the build if
any engine-specific detail leaks past the port.

### 1. In the dashboard - `/app/engine` (Engine & Health)
Pick any of the five engines from the **“Swap the backend engine”** selector. The
change is applied live and **persists** across restarts (stored in
`gateway_settings.active_engine`). Provider-health probes and circuit-breaker state
update in place.

### 2. Via the API
```bash
# whole-slot swap (persists)
curl -X POST http://localhost:8090/admin/engine \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: <PLATFORM_ADMIN_TOKEN>' \
  -d '{"engine":"litellm"}'
```
Related admin routes: `POST /admin/engine/quarantine` and `POST /admin/engine/restore`
(the “quarantine & evacuate” flow - drop a clean engine into the same port with no
downtime and no key rotation).

### 3. Via env (cold start)
Set `ENGINE=…` in `.env` and restart. Good for pinning a default in a deployment.

---

## Pick the best engine **per provider / client / workspace**

This is the second dividend of decoupling: you are not stuck with one translator.
Run several at once and route each **provider** to the engine that serves it best -
most traffic on fast **Bifrost**, and the enterprise platforms only **LiteLLM**
speaks (**IBM watsonx**, **Databricks**, Snowflake, SageMaker) to LiteLLM - with **no
app change and one audit trail**.

`runtime.select_engine(overrides, provider)` resolves the engine per request:

- **Per-provider override** - map a provider to an engine (dashboard: `/app/admin/routing`;
  API: `PATCH /admin/engine-routing`):
  ```bash
  curl -X PATCH http://localhost:8090/admin/engine-routing \
    -H 'Content-Type: application/json' -H 'X-Admin-Token: <token>' \
    -d '{"overrides":{"watsonx":"litellm","databricks":"litellm","anthropic":"bifrost"}}'
  ```
- **Gradual insourcing (weighted split)** - move a provider onto your **owned** Direct
  engine a percentage at a time, zero downtime:
  ```bash
  # 30% of anthropic traffic on our owned DirectEngine, 70% on the default (Bifrost)
  -d '{"overrides":{"anthropic":30}}'          # or {"anthropic":{"direct_pct":30}}
  ```
- **Direct-only providers** always resolve to DirectEngine regardless of the setting
  (the rented sidecars have no adapter for them): `ollama`, `hosted_vllm`, `lm-studio`,
  `litellm_proxy`, … (see `_DIRECT_ONLY_PROVIDERS` in [`gateway/runtime.py`](../gateway/runtime.py)).

Because engine choice is keyed off the **provider** resolved from a workspace's
model/alias config, a **client** or **workspace** that standardizes on watsonx/Databricks
automatically rides LiteLLM, while everything else stays on Bifrost.

### Worked example
> A regulated bank's workspace is mandated onto **IBM watsonx**; a data team's
> workspace standardizes on **Databricks**; everyone else uses Anthropic + Gemini.
> Set `{"watsonx":"litellm","databricks":"litellm"}` and leave the default at
> `bifrost`. Now watsonx/Databricks calls transparently use LiteLLM (the only engine
> that speaks them), the rest stay on fast Bifrost, and you can canary a move of
> Anthropic onto your owned Direct engine with `{"anthropic":10}` → `50` → `100`.

---

## Add a new engine adapter (any OSS model gateway)

The port is four methods - implement them and your gateway is a first-class engine.
Contract: **OpenAI in, OpenAI out**; inject the per-request key from `target`; store
nothing; never leak engine-specific fields past `EngineResult`.

**1. Implement the port** - `gateway/engines/mygw_engine.py`:
```python
from collections.abc import AsyncIterator
from gateway.core.registry import ResolvedTarget
from gateway.engines.base import BackendEngine, EngineResult

class MyGwEngine(BackendEngine):
    name = "mygw"

    async def chat(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        # translate openai_request -> your gateway; inject target.credentials per call;
        # return EngineResult(openai_shaped_body, status_code)
        ...

    def chat_stream(self, openai_request: dict, target: ResolvedTarget) -> AsyncIterator[bytes]:
        # yield raw OpenAI SSE lines: b"data: {...}\n\n" ... b"data: [DONE]\n\n"
        ...

    async def embeddings(self, openai_request: dict, target: ResolvedTarget) -> EngineResult:
        ...

    async def healthcheck(self) -> bool:
        ...
```
See [`gateway/engines/echo_engine.py`](../gateway/engines/echo_engine.py) (simplest) and
[`gateway/engines/portkey_engine.py`](../gateway/engines/portkey_engine.py) (a real stateless sidecar) as templates.

**2. Register it** - [`gateway/runtime.py`](../gateway/runtime.py) `engine_by_name`:
```python
if name == "mygw":
    from gateway.engines.mygw_engine import MyGwEngine
    return MyGwEngine()
```

**3. Allow it** - add `"mygw"` to `ENGINES` in [`gateway/core/admin_validation.py`](../gateway/core/admin_validation.py),
and (optionally) to `SAFE_ENGINES` + `ENGINE_META` in
[`gateway/core/engine_catalog.py`](../gateway/core/engine_catalog.py).

**4. Show it in the UI** - add `"mygw"` to the `EngineName` union in
`frontend/src/api/client.ts` and to the `ENGINES` list in
`frontend/src/screens/EngineHealth.tsx`.

**5. Prove the boundary** - run the anti-coupling + engine tests:
```bash
poetry run pytest tests/test_anti_coupling.py tests/test_backend.py
```
If your adapter leaks an engine-specific field (e.g. `extra_fields`, `x-bf-*`) past
`EngineResult`, the test fails - “swappable engine” is a machine-checked invariant.

**6. (If it's a sidecar)** add its container to `docker-compose.yml` and its URL env
var, mirroring the `bifrost` / `litellm-engine` / `portkey` services.
