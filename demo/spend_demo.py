"""demo/spend_demo.py - a REAL LangChain component driving the Agnos Proxy gateway.

The headline story in one command: a normal LangChain `ChatOpenAI` client with a SINGLE change -
`base_url` pointed at the gateway - plus one workspace key. Every call is governed, attributed
(via `X-Gateway-Component`), costed and traced by the gateway and shows up live in
**Request Logs / Analytics**. It runs a realistic workload until it has spent a target amount
(default $5) so there is substantial, real, costed traffic to generate visible dashboard traffic.

Works against local OR prod, no secrets required - it opens a preview admin session, reuses a
workspace that already has Anthropic, mints its own key, and auto-selects a model that the active
engine can actually serve (so it works whether the slot holds Bifrost, LiteLLM or DirectEngine):

    # local
    python demo/spend_demo.py
    # production
    GATEWAY_URL=http://localhost:8090 python demo/spend_demo.py --target-usd 5

Options:
    --target-usd 5.0     stop once this much NEW spend is attributed to the component
    --component NAME      attribution label shown in Request Logs (default: docforge-demo)
    --concurrency 6       parallel in-flight requests
    --model ID            override the auto-selected model
    --workspace WSID      pin a specific workspace (else auto-pick one with Anthropic)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
except ImportError:
    print("This demo needs LangChain:  pip install langchain-openai langchain-core")
    sys.exit(1)

GW = os.getenv("GATEWAY_URL", "http://localhost:8090").rstrip("/")
ADMIN_TOKEN = os.getenv("PLATFORM_ADMIN_TOKEN", "platform-admin-secret")

# Models to try, in order, until one the active engine can serve is found. The bare
# "anthropic:claude-sonnet-4-5" works on every engine (its scoped model is synced into LiteLLM
# from the workspace chat_models, and Bifrost/Direct route it straight to Anthropic).
MODEL_CANDIDATES = ["anthropic:claude-sonnet-4-5", "anthropic:claude-sonnet-4-5-20250929"]

CONTEXT = ("You are DocForge, a senior staff engineer reviewing a payments microservice.\n"
           "Module under review (Python):\n" + ("""
def charge(customer, amount, idempotency_key=None):
    acct = db.accounts.get(customer.id)
    if acct is None:
        raise NotFound(customer.id)
    if amount <= 0:
        raise ValueError("amount must be positive")
    txn = gateway.create_charge(acct.token, amount)   # network call, may retry
    db.ledger.append(customer.id, txn.id, amount)
    emit_event("charged", customer.id, txn.id, amount)
    return txn
""" * 6))

TASKS = [
    "Review the module for correctness, security and idempotency bugs. Give a numbered list with severity and a concrete fix for each.",
    "Write a thorough docstring and inline comments for the module, then list the edge cases that need tests.",
    "Explain the retry/idempotency risk in detail and propose a production-grade redesign with pseudo-code.",
    "Produce a threat model for this payments code: assets, entry points, likely attacks, and mitigations.",
]


def _client() -> httpx.Client:
    return httpx.Client(base_url=GW, timeout=30.0)


def _auth(c: httpx.Client) -> dict:
    try:
        if c.post("/auth/preview", json={"preview_name": "spend-demo"}).status_code == 200:
            return {}
    except Exception:  # noqa: BLE001
        pass
    return {"X-Admin-Token": ADMIN_TOKEN}


def _pick_workspace(c: httpx.Client, hdr: dict, pin: str | None) -> str:
    if pin:
        return pin
    ws = c.get("/admin/workspaces", headers=hdr).json()
    rows = ws if isinstance(ws, list) else ws.get("rows") or ws.get("workspaces") or []
    for w in sorted(rows, key=lambda w: (0 if "novatech" in (w.get("workspace_id") or "") else 1)):
        wsid = w.get("workspace_id")
        if not wsid:
            continue
        try:
            pv = c.get(f"/admin/workspaces/{wsid}/providers", headers=hdr).json()
            provs = pv.get("providers") if isinstance(pv, dict) else pv
            if any(p.get("provider") == "anthropic" for p in (provs or [])):
                return wsid
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("No workspace with an Anthropic provider found. Configure one in Providers.")


def _mint_key(c: httpx.Client, hdr: dict, wsid: str) -> str:
    r = c.post(f"/admin/workspaces/{wsid}/keys", headers=hdr, json={"roles": ["member"]})
    r.raise_for_status()
    return r.json()["api_key"]


def _pick_model(key: str, component: str, override: str | None) -> str:
    """Return the first candidate model the active engine can actually serve (a 1-token probe),
    so the demo works regardless of which engine is in the slot."""
    cands = [override] if override else MODEL_CANDIDATES
    for m in cands:
        try:
            llm = ChatOpenAI(model=m, base_url=f"{GW}/v1", api_key=key, max_tokens=4, timeout=25,
                             default_headers={"X-Gateway-Component": component})
            llm.invoke([HumanMessage(content="ping")])
            return m
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit(f"None of the candidate models worked on this gateway: {cands}")


def _component_cost(c: httpx.Client, hdr: dict, component: str) -> tuple[float, int]:
    try:
        d = c.get("/admin/cost", params={"group_by": "component"}, headers=hdr).json()
        for row in d.get("rows", []):
            if row.get("key") == component:
                return float(row.get("cost_usd") or 0.0), int(row.get("requests") or 0)
    except Exception:  # noqa: BLE001
        pass
    return 0.0, 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-usd", type=float, default=5.0)
    ap.add_argument("--component", default="docforge-demo")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--model", default=None)
    ap.add_argument("--workspace", default=None)
    a = ap.parse_args()

    c = _client()
    if c.get("/health").status_code != 200:
        print(f"gateway not reachable at {GW}"); return 2
    hdr = _auth(c)
    wsid = _pick_workspace(c, hdr, a.workspace)
    key = _mint_key(c, hdr, wsid)
    model = _pick_model(key, a.component, a.model)
    print(f"-> gateway   : {GW}")
    print(f"-> workspace : {wsid}  (Anthropic)")
    print(f"-> component : {a.component}   model={model}")
    print(f"-> key       : {key[:18]}...  (base_url change only)\n")

    def make_llm(max_tokens: int) -> ChatOpenAI:
        # The component: a normal LangChain ChatOpenAI - base_url points at the gateway.
        return ChatOpenAI(model=model, base_url=f"{GW}/v1", api_key=key,
                          default_headers={"X-Gateway-Component": a.component},
                          max_tokens=max_tokens, temperature=0.7, timeout=45)

    llm = make_llm(220)
    sample_task = TASKS[0]
    ctx_preview = (CONTEXT[:520] + " ...[truncated]") if len(CONTEXT) > 520 else CONTEXT
    print("-" * 78)
    print("THE EXACT CODE THIS DEMO RUNS  (LangChain ChatOpenAI - one base_url change)")
    print("-" * 78)
    print(f"""
  from langchain_openai import ChatOpenAI
  from langchain_core.messages import SystemMessage, HumanMessage

  llm = ChatOpenAI(
      model    = "{model}",         # provider:model - the gateway resolves + governs it
      api_key  = "{key}",           # a workspace key minted from the gateway (RBAC: member)
      base_url = "{GW}/v1",         # <- the ONLY change vs calling Anthropic directly
      default_headers = {{"X-Gateway-Component": "{a.component}"}},   # attribution
      max_tokens = 220, temperature = 0.7,
  )

  llm.invoke([
      SystemMessage(content=SYSTEM_CONTEXT),   # shown below
      HumanMessage(content=TASK),              # shown below
  ])
""")
    print(f"  SYSTEM_CONTEXT ({len(CONTEXT)} chars):\n    {ctx_preview.strip()[:520]}")
    print(f"\n  TASK (one of {len(TASKS)}, rotated):\n    {sample_task}")
    print("\n  Governance the gateway adds transparently: auth + RBAC on the workspace key,")
    print("  per-component attribution (X-Gateway-Component), cost + token metering, guardrails,")
    print("  routing/fallbacks, and one audited event per call - the component sends none of it.")
    print("-" * 78 + "\n")

    base_cost, base_reqs = _component_cost(c, hdr, a.component)
    # Meter spend LOCALLY from each call's returned token usage - the gateway's /admin/cost rollup
    # lags (governance flushes through Kafka async), so polling it in the loop made small targets
    # slow + overshoot. Local metering is instant and accurate; we reconcile with the official
    # number at the end. Anthropic claude-sonnet-4.5 pricing (USD/token).
    PRICE_IN, PRICE_OUT = 3e-6, 15e-6
    print(f"spending ~${a.target_usd:.2f} of Anthropic on '{a.component}' (live token-metered, "
          f"{a.concurrency} workers) ...\n")

    def one_call(i: int):
        # vary the context per call so nothing is prompt-cached (every call is billed in full)
        sys_ctx = f"[review pass #{i}]\n" + CONTEXT
        r = llm.invoke([SystemMessage(content=sys_ctx),
                        HumanMessage(content=f"{TASKS[i % len(TASKS)]} (variant {i})")])
        um = getattr(r, "usage_metadata", None) or {}
        return int(um.get("input_tokens", 0) or 0), int(um.get("output_tokens", 0) or 0)

    sent = ok = fail = in_tok = out_tok = 0
    est = 0.0          # local running cost estimate (instant)
    avg = 0.006        # seed per-call cost, refined as calls complete (for submit-gating)
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        inflight: set = set()
        i = 0
        while est < a.target_usd or inflight:
            # keep the pool full, but don't commit past the target (bounds overshoot)
            while len(inflight) < a.concurrency and (est + len(inflight) * avg) < a.target_usd:
                inflight.add(ex.submit(one_call, i)); i += 1; sent += 1
            if not inflight:
                break
            done = next(as_completed(inflight)); inflight.discard(done)
            try:
                pt, ct = done.result(); ok += 1; in_tok += pt; out_tok += ct
                est += pt * PRICE_IN + ct * PRICE_OUT
                if ok:
                    avg = (in_tok * PRICE_IN + out_tok * PRICE_OUT) / ok
            except Exception as e:  # noqa: BLE001
                fail += 1
                if fail <= 3:
                    print(f"\n  ! call failed: {type(e).__name__}: {str(e)[:160]}", flush=True)
                if fail >= 10 and ok == 0:
                    print("\nevery call is failing - aborting. Check the model/engine on this gateway.", flush=True)
                    return 1
            bar = int(min(1.0, est / a.target_usd) * 28)
            print(f"\r  [{'#'*bar}{'.'*(28-bar)}] ~${est:6.3f}/${a.target_usd:.2f}  "
                  f"calls ok={ok} fail={fail}  tok in/out={in_tok}/{out_tok}", end="", flush=True)

    dt = time.monotonic() - t0
    time.sleep(2.5)  # let the async governance bus settle, then read the OFFICIAL attributed total
    off_cost, off_reqs = _component_cost(c, hdr, a.component)
    print(f"\n\ndone in {dt:.0f}s - ~${est:.3f} spent across {ok} calls "
          f"(tokens in/out={in_tok}/{out_tok}, fails={fail})")
    print(f"  gateway-attributed total for '{a.component}': ${off_cost:.2f}  ({off_reqs - base_reqs} new governed calls)")
    print(f"\n  -> SEE IT LIVE: {GW}/app/logs  and  {GW}/app/cost  filtered by "
          f"component = '{a.component}'  (workspace '{wsid}'); per-call traces in Jaeger.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
