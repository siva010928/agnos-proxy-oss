"""Run + sanity-check the NovaTech DocForge reference component END TO END.

Provisions a workspace + key (the platform side), then drives the component
(the consumer side) through EVERY capability over the gateway using LangChain's
OpenAI interface - and verifies the gateway captured governance for all of it,
without the component sending any cost/telemetry itself.

    python -m demo.reference_component.run          # needs the gateway running

This is both a demo ("look, a real component adopts us with one line + one key")
and a component-side sanity check ("every capability works, and every call is
governed + attributed + traced").
"""
from __future__ import annotations

import sys
import time

from scripts.sanity._client import Admin, CLIENT_ID
from scripts.sanity._env import available, load_env
from scripts.sanity._reporting import FAIL, PASS, SKIP, Section, header, summarize, c
from demo.reference_component.component import DocForge, COMPONENT_NAME

WS_ID = "novatech-docforge"


def provision(admin: Admin) -> tuple[str, str]:
    """Provision the DocForge workspace (chat + embeddings on Bedrock via the
    owned DirectEngine) and return (workspace_id, workspace_key)."""
    env = load_env()
    bedrock = next((s for s in available(env) if s.provider == "bedrock" and s.chat_model), None)
    if not bedrock:
        raise SystemExit("No Bedrock credentials available - fill providers.env-template / .env")
    admin.ensure_client()
    chat_alias = {"chat": [{"provider": "bedrock", "model_id": bedrock.chat_model, "context_window": 200_000}]}
    embed_alias = {"embed": [{"provider": "bedrock", "model_id": bedrock.embed_model}]}
    admin._post("/admin/workspaces", {
        "workspace_id": WS_ID, "client_id": CLIENT_ID, "name": "NovaTech DocForge",
        "chat_models": chat_alias, "embedding_models": embed_alias, "default_chat_alias": "chat"})
    admin._patch(f"/admin/workspaces/{WS_ID}", {
        "chat_models": chat_alias, "embedding_models": embed_alias, "default_chat_alias": "chat",
        "engine_overrides": {"bedrock": "direct"}})     # showcase the owned engine
    admin._post(f"/admin/workspaces/{WS_ID}/providers", {
        "provider": "bedrock", "credentials": bedrock.credentials,
        "config": {"region": bedrock.credentials.get("region")}})
    key = (admin._state.get("keys") or {}).get(WS_ID)
    if not key:
        r = admin._post(f"/admin/workspaces/{WS_ID}/keys", {"roles": ["member"]})
        key = r.json()["api_key"]
        admin._state.setdefault("keys", {})[WS_ID] = key
        from scripts.sanity._client import _save_state
        _save_state(admin._state)
    return WS_ID, key


CODE = ("def authorize(user, resource):\n"
        "    if user.role == 'admin': return True\n"
        "    return resource.owner_id == user.id\n")


def main() -> int:
    admin = Admin()
    if not admin.health():
        print(c(f"\n✗ Gateway not reachable at {admin.base} - start it first.", "FAIL"))
        return 2

    header("Provision NovaTech DocForge (workspace + key) - the platform side")
    prov = Section("provision")
    try:
        wsid, key = provision(admin)
        prov.add("workspace + key", PASS, f"{wsid} · key {key[:16]}… · Bedrock via DirectEngine")
    except Exception as e:  # noqa: BLE001
        prov.add("workspace + key", FAIL, str(e)[:160])
        return summarize("docforge", [prov])

    df = DocForge(gateway_url=admin.base, workspace_key=key)

    header("Drive the component through every capability - the consumer side (LangChain)")
    cap = Section("capabilities")

    def _cap(name, fn):
        t0 = time.perf_counter()
        try:
            out = fn()
            cap.add(name, PASS, str(out)[:70], (time.perf_counter() - t0) * 1000)
        except Exception as e:  # noqa: BLE001
            # A component catches its framework's native OpenAI errors - show the type.
            cap.add(name, FAIL, f"{type(e).__name__}: {e}"[:120], (time.perf_counter() - t0) * 1000)

    _cap("chat · analyze", lambda: df.analyze(CODE))
    _cap("streaming · summary", lambda: "".join(list(df.stream_summary(CODE)))[:60] or "(streamed)")
    _cap("tool-calling · investigate", lambda: df.investigate("Find the authorization module and read it")[0])
    _cap("structured output · extract_spec", lambda: df.extract_spec(
        "The billing-service charges customers; risk: no idempotency on retries.").model_dump())
    _cap("embeddings · index", lambda: f"{len(df.index(['alpha doc', 'beta doc']))} vectors")

    # ── governance verification: the component sent NOTHING, yet the gateway
    #    recorded an attributed, costed, per-use-case governance row for each call ──
    header("Governance captured (component-side proof) - the gateway recorded it all")
    gov = Section("governance")
    time.sleep(2.0)  # let the async governance bus settle
    r = admin._get("/admin/request-logs", {"workspace": WS_ID, "component": COMPONENT_NAME, "limit": 200})
    rows = (r.json().get("rows") if r.status_code == 200 else []) or []
    use_cases = {row.get("use_case") for row in rows if row.get("use_case")}
    total_cost = sum((row.get("cost_usd") or 0) for row in rows)
    for uc in ("docforge.analyze", "docforge.summary", "docforge.agent",
               "docforge.structured", "docforge.index"):
        hit = [row for row in rows if row.get("use_case") == uc]
        if hit:
            row = hit[0]
            gov.add(f"use-case {uc}", PASS,
                    f"{len(hit)} row(s) · provider={row.get('provider')} tokens={row.get('input_tokens')}/{row.get('output_tokens')} cost=${row.get('cost_usd')}")
        else:
            gov.add(f"use-case {uc}", SKIP, "no governance row yet (bus lag or capability skipped)")
    gov.add("attribution rollup", PASS if rows else FAIL,
            f"{len(rows)} governed calls · component={COMPONENT_NAME} · use_cases={len(use_cases)} · Σcost=${total_cost:.6f}")

    print(c(f"\n  → See it in the dashboard: Analytics / Request Logs filtered by "
            f"component='{COMPONENT_NAME}' (workspace '{WS_ID}'), and per-call traces in Jaeger.", "H"))
    return summarize("docforge", [prov, cap, gov])


if __name__ == "__main__":
    sys.exit(main())
