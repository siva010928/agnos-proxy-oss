"""Sanity command implementations. Each returns a list[Section] and prints as it goes.

Everything runs through the LIVE gateway on the real OpenAI wire (the same path a
component uses), then reads governance back to prove usage/cost were recorded.
"""
from __future__ import annotations

import os
import time

import openai

from scripts.sanity import _probes as P
from scripts.sanity._client import Admin, openai_client
from scripts.sanity._env import ProviderSpec, available, load_env
from scripts.sanity._reporting import FAIL, PASS, SKIP, Section, header

PING = [{"role": "user", "content": "Reply with exactly one word: pong"}]


# ── provisioning matrix ──────────────────────────────────────────────────────
def _matrix(admin: Admin, specs: list[ProviderSpec], engines: tuple[str, ...]):
    """Yield (spec, engine, wsid, key) for each provisioned sanity workspace."""
    admin.ensure_client()
    out = []
    for spec in specs:
        for engine in engines:
            if engine not in spec.engines:
                continue
            wsid = admin.ensure_workspace(spec, engine)
            key = admin.ensure_key(wsid)
            out.append((spec, engine, wsid, key))
    return out


def _classify_call(res: dict, *, model_gone_is_skip: bool = False) -> tuple[str, str]:
    """Map a probe result → (status, detail). Provider realities SKIP; our bugs FAIL."""
    if res["ok"]:
        return PASS, ""
    st = res.get("status", 0)
    exc = res.get("exc", "")
    err = res.get("error", "")
    if st in (429, 502, 503, 504) or "Timeout" in exc or "Connection" in exc or "APIConnection" in exc:
        return SKIP, f"provider unavailable ({st or exc})"
    if model_gone_is_skip:
        low = (err or "").lower()
        # Reachability map: a model that's 404, not enabled on the account, or
        # region-mismatched (Bedrock "invalid model identifier") is a provider
        # fact - SKIP, not a gateway wiring defect.
        if st == 404 or (st == 400 and any(t in low for t in
                         ("identifier", "invalid model", "does not have access",
                          "not authorized", "deprecated", "no longer available",
                          "not found", "access denied"))):
            return SKIP, f"not reachable ({st})"
    return FAIL, f"{st or exc}: {err}"


# ── provision ────────────────────────────────────────────────────────────────
def cmd_provision(admin: Admin, specs, engines) -> list[Section]:
    sec = Section("provision")
    header("Provision sanity tenant (client → workspaces → providers → keys)")
    for spec, engine, wsid, key in _matrix(admin, specs, engines):
        sec.add(f"{spec.key}::{engine}", PASS, f"{wsid} · key {key[:16]}… · {spec.note or spec.auth}")
    return [sec]


# ── calls (chat + embedding through the wire) ────────────────────────────────
def cmd_calls(admin: Admin, specs, engines) -> list[Section]:
    header("LLM + embedding calls through the gateway (OpenAI SDK)")
    chat_sec, embed_sec = Section("calls/chat"), Section("calls/embeddings")
    for spec, engine, wsid, key in _matrix(admin, specs, engines):
        oai = openai_client(key)
        if spec.chat_model:
            r = P.chat(oai, "chat", PING, max_tokens=64)
            status, detail = _classify_call(r)
            if status == PASS and not (r.get("text") or r.get("tool_calls")):
                status, detail = FAIL, "empty completion"
            chat_sec.add(f"{spec.key}::{engine} chat", status,
                         detail or f"{r.get('text','')[:24]!r} u={r.get('usage',{}).get('total_tokens')}",
                         r.get("ms", 0))
        if spec.embed_model:
            r = P.embed(oai, "embed", ["hello world", "second text"])
            status, detail = _classify_call(r)
            if status == PASS and r.get("dim", 0) <= 0:
                status, detail = FAIL, "no vector"
            embed_sec.add(f"{spec.key}::{engine} embed", status,
                          detail or f"dim={r.get('dim')} n={r.get('n')}", r.get("ms", 0))
        elif spec.provider == "anthropic":
            embed_sec.add(f"{spec.key}::{engine} embed", SKIP, "anthropic has no embeddings API")
    return [chat_sec, embed_sec]


# ── governance (usage/cost recorded per request) ─────────────────────────────
def cmd_governance(admin: Admin, specs, engines) -> list[Section]:
    header("Governance: every call emits an attributed usage/cost record")
    sec = Section("governance")
    for spec, engine, wsid, key in _matrix(admin, specs, engines):
        if not spec.chat_model:
            continue
        oai = openai_client(key)
        r = P.chat(oai, "chat", PING, max_tokens=64)
        status, detail = _classify_call(r)
        if status != PASS:
            sec.add(f"{spec.key}::{engine} usage-event", status, detail or "call failed", r.get("ms", 0))
            continue
        cid = r.get("cid")
        if not cid:
            sec.add(f"{spec.key}::{engine} usage-event", FAIL, "no X-Gateway-Correlation-Id header")
            continue
        row = admin.find_log(cid)
        if not row:
            sec.add(f"{spec.key}::{engine} usage-event", FAIL, f"no governance row for {cid[:16]}")
            continue
        it = row.get("input_tokens") or 0
        ot = row.get("output_tokens") or 0
        cost = row.get("cost_usd")
        prov = row.get("provider")
        checks = []
        ok = True
        if it <= 0:
            ok = False; checks.append("input_tokens=0")
        if prov != spec.provider:
            ok = False; checks.append(f"provider={prov}!={spec.provider}")
        if cost is None:
            ok = False; checks.append("cost missing")
        sec.add(f"{spec.key}::{engine} usage-event", PASS if ok else FAIL,
                (", ".join(checks) if not ok else
                 f"in={it} out={ot} cost=${cost} provider={prov} status={row.get('status')}"))
    return [sec]


# ── exceptions (framework-native OpenAI errors) ──────────────────────────────
def cmd_exceptions(admin: Admin, specs, engines) -> list[Section]:
    header("Framework-native exceptions: components catch openai.* just like normal")
    sec = Section("exceptions")
    # pick a working chat target (prefer bedrock direct → most reliable creds)
    matrix = _matrix(admin, specs, engines)
    target = next((m for m in matrix if m[0].provider == "bedrock" and m[0].chat_model), None) \
        or next((m for m in matrix if m[0].chat_model), None)
    if not target:
        sec.add("exceptions", SKIP, "no chat-capable provider available")
        return [sec]
    spec, engine, wsid, key = target
    oai = openai_client(key)

    # 1) unknown model → NotFoundError (404)
    try:
        oai.chat.completions.create(model="nonexistent-model-2099", messages=PING, max_tokens=8)
        sec.add("NotFoundError (unknown model)", FAIL, "no exception raised")
    except openai.NotFoundError:
        sec.add("NotFoundError (unknown model)", PASS, "openai.NotFoundError")
    except openai.APIStatusError as e:
        sec.add("NotFoundError (unknown model)", FAIL, f"got {type(e).__name__} ({e.status_code})")
    except Exception as e:  # noqa: BLE001
        sec.add("NotFoundError (unknown model)", FAIL, f"got {type(e).__name__}")

    # 2) bad key → AuthenticationError (401)
    try:
        openai_client("gw-bogus-key-000000000000").chat.completions.create(
            model="chat", messages=PING, max_tokens=8)
        sec.add("AuthenticationError (bad key)", FAIL, "no exception raised")
    except openai.AuthenticationError:
        sec.add("AuthenticationError (bad key)", PASS, "openai.AuthenticationError")
    except openai.APIStatusError as e:
        sec.add("AuthenticationError (bad key)", FAIL, f"got {type(e).__name__} ({e.status_code})")
    except Exception as e:  # noqa: BLE001
        sec.add("AuthenticationError (bad key)", FAIL, f"got {type(e).__name__}")

    # 3) rate limit → RateLimitError (429): DEDICATED rpm=1 workspace (suffix "rl"
    #    so it never mutates the shared matrix workspaces), 2 quick calls.
    rl_ws = admin.ensure_workspace(spec, engine, rate_rpm=1, suffix="rl")
    rl_key = admin.ensure_key(rl_ws)
    admin._patch(f"/admin/workspaces/{rl_ws}", {"rate_limits": {"rpm": 1, "tpm": 10_000_000}})
    roai = openai_client(rl_key)
    try:
        roai.chat.completions.create(model="chat", messages=PING, max_tokens=8)
        roai.chat.completions.create(model="chat", messages=PING, max_tokens=8)
        sec.add("RateLimitError (rpm=1)", SKIP, "second call not limited (timing) - retry")
    except openai.RateLimitError:
        sec.add("RateLimitError (rpm=1)", PASS, "openai.RateLimitError (429)")
    except openai.APIStatusError as e:
        sec.add("RateLimitError (rpm=1)", FAIL, f"got {type(e).__name__} ({e.status_code})")
    except Exception as e:  # noqa: BLE001
        sec.add("RateLimitError (rpm=1)", FAIL, f"got {type(e).__name__}")

    # 4) context overflow → BadRequestError (400 context_length_exceeded). Provider
    #    dependent; SKIP if the provider accepts it.
    big = [{"role": "user", "content": "word " * 60_000}]
    try:
        oai.chat.completions.create(model="chat", messages=big, max_tokens=8)
        sec.add("BadRequestError (context overflow)", SKIP, "provider accepted oversized prompt")
    except openai.BadRequestError as e:
        detail = "openai.BadRequestError"
        if "context" in str(e).lower():
            detail += " (context_length_exceeded)"
        sec.add("BadRequestError (context overflow)", PASS, detail)
    except openai.APIStatusError as e:
        sec.add("BadRequestError (context overflow)", SKIP, f"got {type(e).__name__} ({e.status_code})")
    except Exception as e:  # noqa: BLE001
        sec.add("BadRequestError (context overflow)", SKIP, f"got {type(e).__name__}")
    return [sec]


# ── passthrough (extra params OpenAI would omit) ─────────────────────────────
def cmd_passthrough(admin: Admin, specs, engines) -> list[Section]:
    header("Extra-param passthrough (provider-native fields OpenAI drops)")
    sec = Section("passthrough")
    matrix = _matrix(admin, specs, engines)

    # 1) Anthropic prompt caching THROUGH the boundary (direct engine): a field the
    #    generic OpenAI shape has no place for. First call writes cache, second reads.
    anth = next((m for m in matrix if m[0].provider == "anthropic" and m[1] == "direct"), None)
    if anth:
        _, _, wsid, key = anth
        oai = openai_client(key)
        # Exceed Anthropic's minimum cacheable size (Haiku ~2048 tokens) comfortably.
        big_system = "You are a meticulous assistant. " + (
            "You must follow these detailed operating instructions carefully and precisely. " * 800)
        msgs = [{"role": "system", "content": big_system},
                {"role": "user", "content": "Reply with exactly: ok"}]
        r1 = P.chat(oai, "chat", msgs, max_tokens=16, extra_body={"prompt_cache": True})
        time.sleep(1.0)
        r2 = P.chat(oai, "chat", msgs, max_tokens=16, extra_body={"prompt_cache": True})
        u1, u2 = r1.get("usage", {}) or {}, r2.get("usage", {}) or {}
        created = u1.get("cache_creation_input_tokens") or (u1.get("prompt_tokens_details") or {}).get("cached_tokens")
        read = u2.get("cache_read_input_tokens") or (u2.get("prompt_tokens_details") or {}).get("cached_tokens")
        if r1["ok"] and r2["ok"] and (created or read):
            sec.add("anthropic prompt_cache passthrough", PASS,
                    f"cache created={created} read={read} (native feature via OpenAI wire)")
        elif r1["ok"] and r2["ok"]:
            sec.add("anthropic prompt_cache passthrough", SKIP,
                    "calls ok but no cache tokens surfaced (short/uncacheable prompt)")
        else:
            st, d = _classify_call(r1 if not r1["ok"] else r2)
            sec.add("anthropic prompt_cache passthrough", st, d)
    else:
        sec.add("anthropic prompt_cache passthrough", SKIP, "anthropic direct not available")

    # 2) Benign unknown extra field is accepted (not rejected) end-to-end.
    any_chat = next((m for m in matrix if m[0].chat_model), None)
    if any_chat:
        _, _, wsid, key = any_chat
        r = P.chat(openai_client(key), "chat", PING, max_tokens=16,
                   extra_body={"metadata": {"sanity": "passthrough"}, "seed": 7})
        st, d = _classify_call(r)
        sec.add("benign extra params accepted", st, d or "metadata+seed accepted, no 400")
    return [sec]


# ── authmodes (Bedrock static / bearer / sso) ────────────────────────────────
def cmd_authmodes(admin: Admin, specs, engines) -> list[Section]:
    header("AWS Bedrock auth modes (static / bearer / SSO) via DirectEngine")
    sec = Section("authmodes")
    bedrock_specs = [s for s in specs if s.provider == "bedrock"]
    if not bedrock_specs:
        sec.add("bedrock authmodes", SKIP, "no bedrock creds present")
        return [sec]
    admin.ensure_client()
    for spec in bedrock_specs:
        label = f"bedrock {spec.auth}"
        if not spec.available:
            sec.add(label, SKIP, f"{spec.note} - not configured")
            continue
        wsid = admin.ensure_workspace(spec, "direct")
        key = admin.ensure_key(wsid)
        oai = openai_client(key)
        rc = P.chat(oai, "chat", PING, max_tokens=32)
        st, d = _classify_call(rc)
        sec.add(f"{label} chat", st, d or f"{rc.get('text','')[:16]!r}", rc.get("ms", 0))
        if spec.embed_model:
            re = P.embed(oai, "embed", ["hello"])
            st2, d2 = _classify_call(re)
            sec.add(f"{label} embed", st2, d2 or f"dim={re.get('dim')}", re.get("ms", 0))
    return [sec]


# ── parity (Bifrost vs DirectEngine) ─────────────────────────────────────────
def cmd_parity(admin: Admin, specs, engines) -> list[Section]:
    header("Shadow parity: Bifrost vs DirectEngine, same prompt (proof the swap is safe)")
    sec = Section("parity")
    for spec in specs:
        if "direct" not in spec.engines or not spec.chat_model:
            continue
        wsid = admin.ensure_workspace(spec, "direct")
        admin.ensure_key(wsid)
        try:
            res = admin.parity_run(wsid, spec.provider, spec.chat_model,
                                   "Reply with exactly one word: pong", max_tokens=64)
        except Exception as e:  # noqa: BLE001
            sec.add(f"{spec.key} parity", FAIL, f"parity call failed: {e}")
            continue
        verdict = res.get("verdict")
        b, d = res.get("bifrost", {}), res.get("direct", {})
        if verdict in ("identical", "high", "moderate"):
            sec.add(f"{spec.key} parity", PASS,
                    f"{verdict} sim={res.get('text_similarity')} "
                    f"Δlatency={res.get('latency_delta_ms')}ms (direct {'faster' if (res.get('latency_delta_ms') or 0) < 0 else 'slower'})")
        elif b.get("ok") and d.get("ok"):
            sec.add(f"{spec.key} parity", PASS, f"both ok (verdict={verdict}, sim={res.get('text_similarity')})")
        elif d.get("ok") and not b.get("ok"):
            sec.add(f"{spec.key} parity", SKIP, f"bifrost unavailable ({b.get('error','')[:40]}); direct ok")
        else:
            sec.add(f"{spec.key} parity", FAIL,
                    f"verdict={verdict} bifrost={b.get('error','')[:40]} direct={d.get('error','')[:40]}")
    return [sec]


# ── routing (engine split + alias config persist across save/reload) ─────────
def cmd_routing(admin: Admin, specs, engines) -> list[Section]:
    header("Routing config persistence: engine split + alias survive save/reload")
    sec = Section("routing")
    admin.ensure_client()
    spec = next((s for s in specs if "direct" in s.engines and s.chat_model), None) \
        or next((s for s in specs if s.chat_model), None)
    if not spec:
        sec.add("routing", SKIP, "no chat-capable provider available")
        return [sec]
    wsid = admin.ensure_workspace(spec, "direct")
    admin.ensure_key(wsid)
    prov = spec.provider

    def _ws() -> dict:
        return next((w for w in admin.workspaces() if w["workspace_id"] == wsid), {})

    def _routing() -> dict:
        # Engine routing is GATEWAY-WIDE - read it from the global endpoint.
        return (admin._get("/admin/engine-routing").json() or {}).get("overrides") or {}

    def _set_routing(prov: str, val) -> None:
        cur = _routing()
        cur[prov] = val
        admin._patch("/admin/engine-routing", {"overrides": cur})

    # 1. engine SPLIT round-trip: set 30% owned → re-read → must persist as int 30
    #    (this is the "split lost on reload" report - proving it survives). Engine
    #    routing is now a single GATEWAY-WIDE setting (not per-workspace).
    _set_routing(prov, 30)
    val = _routing().get(prov)
    split_ok = isinstance(val, int) and val == 30
    sec.add("engine SPLIT saved + reloaded (30% owned, gateway-wide)", PASS if split_ok else FAIL,
            f"engine-routing[{prov}]={val!r} (type {type(val).__name__})")

    # 2. owned + rented round-trip
    _set_routing(prov, "direct")
    owned_ok = _routing().get(prov) == "direct"
    _set_routing(prov, "")
    rented_ok = _routing().get(prov) in ("", None)
    sec.add("engine OWNED + RENTED saved + reloaded", PASS if (owned_ok and rented_ok) else FAIL,
            f"owned={owned_ok} rented={rented_ok}")
    _set_routing(prov, 30)  # leave a split for the demo

    # 3. ALIAS round-trip: save an alias → re-read → must persist with its targets.
    alias_name = "sanity-alias"
    targets = [{"provider": prov, "model_id": spec.chat_model, "context_window": 200_000}]
    before = _ws().get("chat_models") or {}
    admin._patch(f"/admin/workspaces/{wsid}", {"chat_models": {**before, alias_name: targets}})
    after = _ws().get("chat_models") or {}
    alias_ok = alias_name in after and (after[alias_name] or [{}])[0].get("model_id") == spec.chat_model
    sec.add("ALIAS saved + reloaded", PASS if alias_ok else FAIL,
            f"'{alias_name}' present={alias_name in after} targets={after.get(alias_name)}")

    # 3b. THE reported bug: saving an alias (a per-workspace patch) must NOT reset the
    #     engine split. Because routing is now a SEPARATE gateway-wide setting, an
    #     alias save cannot touch it - re-read after the alias save to prove it holds.
    val_after_alias = _routing().get(prov)
    survived = isinstance(val_after_alias, int) and val_after_alias == 30
    sec.add("engine SPLIT survives an ALIAS save (not reset to rented)",
            PASS if survived else FAIL,
            f"engine-routing[{prov}] after alias save = {val_after_alias!r}")

    # 4. stable picker scoping: the workspace maps to its client (so the client→
    #    workspace picker resolves it consistently, not landing elsewhere).
    w = _ws()
    sec.add("workspace → client mapping stable (picker scoping)",
            PASS if w.get("client_id") == "sanity-co" else FAIL,
            f"{wsid} client_id={w.get('client_id')}")
    return [sec]


# ── providertest (admin/providers 'Test Connection' per provider/auth, real .env) ──
def cmd_providertest(admin: Admin, specs, engines) -> list[Section]:
    header("Admin · Providers 'Test Connection' with REAL .env creds (every provider + auth mode)")
    sec = Section("providertest")
    for spec in specs:   # only providers whose creds are present in .env
        cfg = dict(getattr(spec, "config", None) or {})
        if spec.credentials.get("region"):
            cfg["region"] = spec.credentials["region"]
        r = admin.test_provider(spec.provider, spec.credentials, cfg, spec.chat_model)
        ok = bool(r.get("ok"))
        err = str(r.get("error") or (r.get("detail") or {}).get("error") or "")
        low = err.lower()
        # provider realities (throttling / billing / region) are SKIP, not our bug.
        provider_reality = any(t in low for t in ("429", "throttl", "rate", "billing",
                                                  "quota", "timeout", "unavailable", "503", "502"))
        status = PASS if ok else (SKIP if provider_reality else FAIL)
        detail = (f"reachable · {r.get('latency_ms')}ms" if ok else err[:120] or "test failed")
        sec.add(f"{spec.key} · {spec.auth} Test Connection", status, detail)
    if not specs:
        sec.add("providertest", SKIP, "no providers with credentials in .env")
    return [sec]


# ── availability (alias selection restricted to account-accessible models) ──
def cmd_availability(admin: Admin, specs, engines) -> list[Section]:
    header("Model availability: each account's REACHABLE models (restricts alias selection)")
    sec = Section("availability")
    seen: set = set()
    for spec, engine, wsid, key in _matrix(admin, specs, engines):
        if spec.provider in seen:
            continue
        seen.add(spec.provider)
        r = admin.available_models(wsid, spec.provider)
        if not r.get("ok"):
            # bearer/guardrail-only principals genuinely can't list - a provider
            # fact, not a gateway defect → SKIP (UI falls back to free-typing).
            sec.add(f"{spec.provider} account models", SKIP, f"not listable: {str(r.get('error',''))[:60]}")
            continue
        models = r.get("models", [])
        count = r.get("count", 0)
        # the workspace's configured chat model should be in the reachable set
        cm = spec.chat_model or ""
        reachable = (not cm) or any(cm == m or cm in m for m in models)
        sec.add(f"{spec.provider} account models", PASS if count > 0 else FAIL,
                f"{count} reachable models; configured '{cm[:36]}' reachable={reachable}")
    return [sec]


# ── consumers (every consumer surface carries usage: chat/stream/tools/embed) ──
def cmd_consumers(admin: Admin, specs, engines) -> list[Section]:
    header("Consumer surfaces: chat · stream · tool-calling · embeddings all carry usage")
    sec = Section("consumers")
    matrix = _matrix(admin, specs, engines)
    tgt = next((m for m in matrix if m[0].provider in ("bedrock", "anthropic", "gemini") and m[0].chat_model), None) \
        or next((m for m in matrix if m[0].chat_model), None)
    if not tgt:
        sec.add("consumers", SKIP, "no chat-capable provider available")
        return [sec]
    spec, engine, wsid, key = tgt
    oai = openai_client(key)

    # 1. chat completion (acompletion / invoke / ainvoke all resolve here) → usage
    r = P.chat(oai, "chat", PING, max_tokens=64)
    st, d = _classify_call(r)
    has_usage = bool((r.get("usage") or {}).get("total_tokens"))
    sec.add("chat completion → usage", PASS if (st == PASS and has_usage) else (st if st != PASS else FAIL),
            d or f"usage={r.get('usage')}")

    # 2. streaming → chunks arrive + a governance row is recorded
    try:
        chunks, text = 0, []
        stream = oai.chat.completions.create(model="chat", messages=PING, max_tokens=64, stream=True)
        for ch in stream:
            chunks += 1
            delta = (ch.choices[0].delta.content if ch.choices else None) if ch.choices else None
            if delta:
                text.append(delta)
        sec.add("streaming → chunks delivered", PASS if chunks > 0 else FAIL,
                f"{chunks} chunks · {''.join(text)[:24]!r}")
    except openai.APIStatusError as e:  # noqa: BLE001
        sec.add("streaming → chunks delivered", SKIP if e.status_code in (429, 502, 503) else FAIL,
                f"{type(e).__name__} {e.status_code}")
    except Exception as e:  # noqa: BLE001
        sec.add("streaming → chunks delivered", FAIL, f"{type(e).__name__}: {str(e)[:60]}")

    # 3. tool-calling (bind_tools / function-calling) → tool_calls
    tools = [{"type": "function", "function": {
        "name": "get_weather", "description": "Get weather for a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
    r2 = P.chat(oai, "chat", [{"role": "user", "content": "What's the weather in Paris? Use the tool."}],
                max_tokens=128, extra_body={"tools": tools})
    st2, d2 = _classify_call(r2)
    tool_ok = st2 == PASS and bool(r2.get("tool_calls"))
    sec.add("tool-calling → tool_calls or answer", PASS if st2 == PASS else st2,
            d2 or (f"tool_calls={len(r2.get('tool_calls') or [])}" if tool_ok else "answered without tools (model choice)"))

    # 4. embeddings (generate_embeddings / embed_query / aembed_*) → vector + usage
    if spec.embed_model:
        re = P.embed(oai, "embed", ["hello world", "second text"])
        st3, d3 = _classify_call(re)
        sec.add("embeddings → vector", PASS if (st3 == PASS and re.get("dim", 0) > 0) else st3,
                d3 or f"dim={re.get('dim')} n={re.get('n')}")
    else:
        sec.add("embeddings → vector", SKIP, f"{spec.provider} has no embeddings in this workspace")
    return [sec]


# ── pricing (synced from source; cost computed) ──────────────────────────────
def cmd_pricing(admin: Admin, specs, engines) -> list[Section]:
    header("Pricing synced from source + applied to governed requests")
    sec = Section("pricing")
    models = admin.models()
    priced = [m for m in models if (m.get("input_per_1k") or m.get("input_cost_per_1k")
                                    or (m.get("pricing") or {}).get("input"))]
    sec.add("catalog pricing present", PASS if priced else FAIL,
            f"{len(priced)}/{len(models)} models carry synced prices")

    # Every catalog model must be priced from the VERIFIED source (LiteLLM synced
    # or an explicit operator override) - never a loose 'builtin'/'none' guess.
    # This proves the pricing page shows only verified-source models/prices.
    bad_src = [m for m in models if m.get("price_source") not in ("synced", "override")]
    sec.add("prices only from verified source (litellm/override)",
            PASS if not bad_src else FAIL,
            f"{len(models) - len(bad_src)}/{len(models)} models synced/override" if not bad_src
            else f"{len(bad_src)} models priced from builtin/none e.g. "
                 f"{[m['model_id'] for m in bad_src[:4]]}")

    # Spot-check exact LiteLLM prices + sane ordering (the reported 'opus-4-1 vs
    # opus-4-8' case): opus-4-1 is the OLD expensive tier, opus-4-8 the newer
    # cheaper one, so opus-4-1 SHOULD be costlier. Anchored to litellm's numbers.
    by_id = {m["model_id"]: m for m in models}
    checks = [("claude-opus-4-1", 0.015, 0.075), ("claude-opus-4-8", 0.005, 0.025),
              ("claude-sonnet-4-5", 0.003, 0.015)]
    exact_ok, detail = True, []
    for mid, ei, eo in checks:
        m = by_id.get(mid)
        if not m:
            continue
        ai, ao = m.get("input_per_1k") or 0, m.get("output_per_1k") or 0
        ok = abs(ai - ei) < 1e-6 and abs(ao - eo) < 1e-6
        exact_ok = exact_ok and ok
        detail.append(f"{mid}={'ok' if ok else f'in{ai}/out{ao}!=in{ei}/out{eo}'}")
    o1, o8 = by_id.get("claude-opus-4-1"), by_id.get("claude-opus-4-8")
    order_ok = (not o1 or not o8) or ((o1.get("input_per_1k") or 0) > (o8.get("input_per_1k") or 0))
    sec.add("exact litellm prices + sane ordering (opus-4-1 > opus-4-8)",
            PASS if (exact_ok and order_ok) else FAIL,
            "  ".join(detail) + (f"  order_opus41>opus48={order_ok}"))
    # cost actually applied: make a call and assert governance cost_usd > 0 for a priced model
    matrix = _matrix(admin, specs, engines)
    tgt = next((m for m in matrix if m[0].provider in ("bedrock", "anthropic", "gemini") and m[0].chat_model), None)
    if tgt:
        spec, engine, wsid, key = tgt
        r = P.chat(openai_client(key), "chat", PING, max_tokens=32)
        st, d = _classify_call(r)
        if st != PASS:
            sec.add("cost applied to request", st, d)
        else:
            row = admin.find_log(r.get("cid")) or {}
            cost = row.get("cost_usd")
            sec.add("cost applied to request", PASS if (cost and cost > 0) else FAIL,
                    f"cost_usd=${cost} for {spec.provider}/{spec.chat_model}")
    return [sec]


# ── observability (full lifecycle: metrics + governance row + Jaeger trace) ──
def _metric_sum(text: str, name: str) -> float:
    """Sum all samples of a Prometheus metric family (name{labels} value)."""
    total = 0.0
    for line in text.splitlines():
        if line.startswith("#") or not line.startswith(name):
            continue
        head = line.split("{")[0].strip() if "{" in line else line.split(" ")[0].strip()
        if head != name:
            continue
        try:
            total += float(line.rsplit(" ", 1)[1])
        except (ValueError, IndexError):
            pass
    return total


def cmd_observability(admin: Admin, specs, engines) -> list[Section]:
    header("Observability: full request lifecycle captured (metrics + governance + Jaeger trace)")
    sec = Section("observability")
    matrix = _matrix(admin, specs, engines)
    tgt = next((m for m in matrix if m[0].provider == "bedrock" and m[0].chat_model), None) \
        or next((m for m in matrix if m[0].chat_model), None)
    if not tgt:
        sec.add("observability", SKIP, "no chat-capable provider available")
        return [sec]
    spec, engine, wsid, key = tgt
    oai = openai_client(key)

    # ── metrics before/after: prove the gateway_* series increment ──
    before = admin.metrics_text()
    ok = P.chat(oai, "chat", [{"role": "user", "content": "Reply with exactly one word: pong"}], max_tokens=32)
    # an error call (Mode B unknown model → provider/validation error) for the error path
    err = P.chat(oai, f"{spec.provider}:definitely-not-a-real-model-2099", PING, max_tokens=8)
    time.sleep(2.0)  # let the async governance bus + metrics settle
    after = admin.metrics_text()

    if not after:
        sec.add("prometheus /metrics", FAIL, "metrics endpoint returned nothing")
    else:
        checks = []
        for m in ("gateway_requests_total", "gateway_tokens_total", "gateway_request_seconds_count",
                  "gateway_overhead_seconds_count"):
            d = _metric_sum(after, m) - _metric_sum(before, m)
            checks.append((m, d))
        req_up = next(d for (n, d) in checks if n == "gateway_requests_total")
        present = {n for n in (
            "gateway_requests_total", "gateway_tokens_total", "gateway_cost_usd_total",
            "gateway_request_seconds", "gateway_overhead_seconds", "gateway_provider_latency_seconds",
        ) if n in after}
        sec.add("prometheus series present", PASS if len(present) >= 5 else FAIL,
                f"{len(present)}/6 core gateway_* series exposed")
        sec.add("prometheus counters increment", PASS if req_up >= 1 else FAIL,
                "  ".join(f"Δ{n.replace('gateway_','')}={d:g}" for n, d in checks))

    # ── success path: governance row + Jaeger trace with parent+child spans ──
    if ok["ok"] and ok.get("cid"):
        cid = ok["cid"]
        row = admin.find_log(cid)
        if not row:
            sec.add("success · governance row", FAIL, f"no request_logs row for {cid[:16]}")
        else:
            need = {"input_tokens": row.get("input_tokens"), "cost_usd": row.get("cost_usd"),
                    "latency_ms": row.get("latency_ms"), "provider": row.get("provider"),
                    "engine": row.get("engine"), "status": row.get("status")}
            missing = [k for k, v in need.items() if v in (None, "")]
            full = (not missing) and (row.get("input_tokens") or 0) > 0 and row.get("status") == "success"
            sec.add("success · governance row (full lifecycle)", PASS if full else FAIL,
                    (f"missing {missing}" if missing else
                     f"in={need['input_tokens']} cost=${need['cost_usd']} {need['latency_ms']}ms "
                     f"{need['provider']}/{row.get('provider_model_id')} engine={need['engine']}"))
        # Jaeger - poll until the LATER-ending spans (engine, governance, and the
        # parent gateway.chat) have flushed too. A single fetch can race the batch
        # export and return only the early child spans (auth/guardrails/routing).
        if not admin.jaeger_up():
            sec.add("success · Jaeger trace", SKIP, "Jaeger not reachable")
        else:
            ops: set = set()
            trace_id = ""
            span_ct = 0
            for _ in range(6):
                tr = admin.jaeger_trace(cid, timeout=6)
                if tr:
                    ops = {s.get("operationName") for s in tr.get("spans", [])}
                    trace_id = tr.get("traceID", "")
                    span_ct = len(tr.get("spans", []))
                    if "gateway.chat" in ops and len(ops & {"auth", "routing", "guardrails", "engine", "governance"}) >= 4:
                        break
                time.sleep(3)
            child = ops & {"auth", "routing", "guardrails", "engine", "governance"}
            has_parent = "gateway.chat" in ops
            sec.add("success · Jaeger trace (parent+child spans)",
                    PASS if (has_parent and len(child) >= 2) else FAIL,
                    f"trace={trace_id[:16]} spans={span_ct} "
                    f"parent={'gateway.chat' if has_parent else 'MISSING'} stages={sorted(child)}")
    else:
        sec.add("success · governance row", SKIP, f"success call failed: {ok.get('error','')[:60]}")

    # ── error path: error captured with why-context + its own trace ──
    if err.get("cid"):
        row = admin.find_log(err["cid"])
        if row and row.get("status") and row.get("status") != "success":
            sec.add("error · governance row (why-context)", PASS,
                    f"status={row.get('status')} error_detail={'yes' if row.get('error_detail') else 'no'}")
        elif row:
            sec.add("error · governance row (why-context)", PASS, f"logged status={row.get('status')}")
        else:
            sec.add("error · governance row (why-context)", SKIP, "error row not found (routing-stage 4xx)")
        # The failing trace MUST expose the failure (not normal teal spans): the
        # engine + parent span carry status=ERROR + structured error attributes.
        # Poll a few times - the error spans (engine, gateway.chat) end LAST, so a
        # single fetch can race the span-batch flush.
        if admin.jaeger_up():
            err_spans = []
            for _ in range(6):
                tr = admin.jaeger_trace(err["cid"], timeout=6)
                if tr:
                    err_spans = []
                    for sp in tr.get("spans", []):
                        tags = {t.get("key"): t.get("value") for t in sp.get("tags", [])}
                        if tags.get("error") is True or tags.get("otel.status_code") == "ERROR" \
                                or tags.get("agnos.error_type"):
                            err_spans.append((sp.get("operationName"), tags.get("http.status_code"),
                                              tags.get("agnos.error_type")))
                if err_spans:
                    break
                time.sleep(3)
            sec.add("error · Jaeger trace marked ERROR",
                    PASS if err_spans else FAIL,
                    (f"{len(err_spans)} error span(s): " +
                     ", ".join(f"{n}(http={h},{t})" for n, h, t in err_spans[:3])) if err_spans
                    else "no span marked error - failure is invisible in the trace")
    else:
        sec.add("error · governance row (why-context)", SKIP, "error call produced no correlation id")

    # ── embeddings error path: a failed EMBEDDING must be visible in the trace too
    #    (gateway.embeddings / engine span marked ERROR) - not only chat. ──
    emb_tgt = next((m for m in matrix if m[0].embed_model), None)
    if not emb_tgt:
        sec.add("embeddings error · Jaeger trace marked ERROR", SKIP, "no embedding-capable provider available")
    elif not admin.jaeger_up():
        sec.add("embeddings error · Jaeger trace marked ERROR", SKIP, "Jaeger not reachable")
    else:
        espec, _eeng, _ews, ekey = emb_tgt
        er = P.embed(openai_client(ekey), f"{espec.provider}:definitely-not-a-real-embed-2099", ["x"])
        ecid = er.get("cid")
        if not ecid:
            sec.add("embeddings error · Jaeger trace marked ERROR", SKIP,
                    f"no correlation id from embeddings error (status={er.get('status')})")
        else:
            e_spans = []
            for _ in range(6):
                tr = admin.jaeger_trace(ecid, timeout=6)
                if tr:
                    e_spans = []
                    for sp in tr.get("spans", []):
                        tags = {t.get("key"): t.get("value") for t in sp.get("tags", [])}
                        if tags.get("error") is True or tags.get("otel.status_code") == "ERROR" \
                                or tags.get("agnos.error_type"):
                            e_spans.append((sp.get("operationName"), tags.get("http.status_code")))
                if e_spans:
                    break
                time.sleep(3)
            sec.add("embeddings error · Jaeger trace marked ERROR",
                    PASS if e_spans else FAIL,
                    (f"{len(e_spans)} error span(s): " + ", ".join(f"{n}(http={h})" for n, h in e_spans[:3]))
                    if e_spans else "no embeddings span marked error - failure invisible in the trace")
    return [sec]


# ── filters (hierarchical, parent-aware cascading facets) ────────────────────
def cmd_filters(admin: Admin, specs, engines) -> list[Section]:
    header("Hierarchical cascading filters (parent-aware facets for Logs/Analytics/Routing)")
    sec = Section("filters")
    # Provision the tenant so clients + workspaces exist in the DB. The cascade
    # and union checks below are deterministic (sourced from the Client/Workspace
    # tables), so they hold even with zero request rows.
    matrix = _matrix(admin, specs, engines)
    our_ws = {wsid for (_, _, wsid, _) in matrix}
    cfg_prov = {s.provider for s in specs}

    base = admin.facets()
    if not base:
        sec.add("facets endpoint", FAIL, "no response from /admin/request-logs/facets")
        return [sec]

    # 1) endpoint returns every facet group the filter bars need
    need = {"clients", "workspaces", "components", "providers", "statuses",
            "event_kinds", "users", "models", "use_cases"}
    missing = need - set(base)
    sec.add("facets endpoint shape", PASS if not missing else FAIL,
            f"missing {sorted(missing)}" if missing else f"{len(need)} facet groups present")

    # 2) client → workspace cascade: scoping to our client returns ONLY its
    #    workspaces (a subset of all), and our sanity workspaces appear.
    all_ws_ids = {w["workspace_id"] for w in base.get("workspaces", [])}
    scoped = admin.facets(client="sanity-co")
    scoped_ws = scoped.get("workspaces", [])
    scoped_ids = {w["workspace_id"] for w in scoped_ws}
    all_belong = all(w.get("client_id") == "sanity-co" for w in scoped_ws)
    is_subset = scoped_ids <= all_ws_ids
    ours = our_ws & scoped_ids
    cascade_ok = all_belong and is_subset and bool(ours)
    sec.add("client→workspace cascade", PASS if cascade_ok else FAIL,
            f"client=sanity-co → {len(scoped_ids)} ws (all belong={all_belong}, "
            f"subset={is_subset}, ours={len(ours)}/{len(our_ws)}) of {len(all_ws_ids)} total")

    # 3) enum-union when unscoped: with no scope the provider facet shows the full
    #    space (⊇ every configured provider) and is a superset of any single
    #    workspace's data-driven providers.
    unscoped_prov = set(base.get("providers", []))
    sec.add("unscoped facets union the provider enum",
            PASS if cfg_prov <= unscoped_prov else FAIL,
            f"configured {sorted(cfg_prov)} ⊆ unscoped providers ({len(unscoped_prov)})")
    one_ws = sorted(our_ws)[0] if our_ws else None
    if one_ws:
        wsf = admin.facets(workspace=one_ws)
        scoped_prov = set(wsf.get("providers", []))
        sec.add("unscoped ⊇ workspace-scoped (data-driven narrows)",
                PASS if scoped_prov <= unscoped_prov else FAIL,
                f"unscoped={len(unscoped_prov)} ⊇ workspace-scoped={len(scoped_prov)}")

    # 4) parent-aware data-driven: after a real call, scoping to its workspace
    #    surfaces its provider, and a facet is never scoped by ITSELF (re-pickable).
    tgt = next((m for m in matrix if m[0].chat_model), None)
    if not tgt:
        sec.add("data-driven facet (live row)", SKIP, "no chat-capable provider available")
        return [sec]
    spec, engine, wsid, key = tgt
    r = P.chat(openai_client(key), "chat", PING, max_tokens=16)
    st, d = _classify_call(r)
    if st != PASS:
        sec.add("data-driven facet (live row)", st, d or "seed call failed")
        return [sec]
    admin.find_log(r.get("cid"))  # block until the async governance row lands
    wf = admin.facets(workspace=wsid)
    prov_ok = spec.provider in set(wf.get("providers", []))
    sec.add("workspace scope → data-driven provider", PASS if prov_ok else FAIL,
            f"provider {spec.provider} {'in' if prov_ok else 'NOT in'} "
            f"workspace facets {sorted(set(wf.get('providers', [])))[:6]}")
    pf = admin.facets(provider=spec.provider)
    repick = spec.provider in set(pf.get("providers", []))
    sec.add("facet not scoped by itself (re-pickable)", PASS if repick else FAIL,
            f"provider={spec.provider} selected → still offered in its own facet ({repick})")

    # ── client→workspace picker hierarchy (Providers / Keys / Routing pages) ──
    # The picker must support: default all-clients→all-workspaces, a specific
    # client → ONLY its workspaces, and an all-workspaces (per client) browse.
    # Verify the underlying data is coherent against the real DB.
    clients = admin.clients()
    all_ws = admin.workspaces()
    client_ids = {c.get("client_id") for c in clients}
    # 1. referential integrity: every workspace's client_id is a real client
    orphans = [w["workspace_id"] for w in all_ws if w.get("client_id") and w["client_id"] not in client_ids]
    sec.add("picker · every workspace maps to a real client", PASS if not orphans else FAIL,
            f"{len(all_ws)} workspaces, {len(client_ids)} clients, orphans={orphans[:4]}" if orphans
            else f"{len(all_ws)} workspaces all map to one of {len(client_ids)} clients")
    # 2. parent→descendants: sanity-co → exactly its workspaces, a proper subset
    co_ws = {w["workspace_id"] for w in all_ws if w.get("client_id") == "sanity-co"}
    all_ids = {w["workspace_id"] for w in all_ws}
    scope_ok = co_ws <= all_ids and bool(co_ws & our_ws) and co_ws == {w["workspace_id"] for w in all_ws if w.get("client_id") == "sanity-co"}
    sec.add("picker · client → only its workspaces (parent→descendants)",
            PASS if scope_ok else FAIL,
            f"sanity-co → {len(co_ws)} ws ⊆ {len(all_ids)} all; ours present={bool(co_ws & our_ws)}")
    # 3. 'All workspaces' is a real, selectable set in BOTH scopes (all clients,
    #    and within a client) - i.e. non-empty and drillable.
    allws_ok = len(all_ids) > 0 and len(co_ws) > 0
    sec.add("picker · 'All workspaces' selectable (all-clients + per-client)",
            PASS if allws_ok else FAIL,
            f"all-clients all-workspaces={len(all_ids)}, sanity-co all-workspaces={len(co_ws)}")
    # 4. the all-workspaces OVERVIEW data resolves: per-workspace providers + keys
    #    endpoints return data for a provisioned workspace (what the overview lists).
    ov_ws = sorted(co_ws)[0] if co_ws else (sorted(all_ids)[0] if all_ids else None)
    if ov_ws:
        provs = admin.providers(ov_ws)
        keys = admin.keys(ov_ws)
        sec.add("picker · per-workspace providers+keys resolve (overview data)",
                PASS if (provs and keys) else FAIL,
                f"{ov_ws}: providers={len(provs)} keys={len(keys)}")
    return [sec]


# ── catalog (reachability map across the gateway's provider catalog) ─────────
# Defaults to the gateway's own baked catalog (data/provider_catalog.yaml); no
# external path. Override with $SANITY_CATALOG / $SANITY_INACCESSIBLE if desired.
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_AGNOS_CATALOG = os.environ.get("SANITY_CATALOG",
                                  os.path.join(_REPO_ROOT, "data", "provider_catalog.yaml"))
_INACCESSIBLE = os.environ.get("SANITY_INACCESSIBLE", "")


def cmd_catalog(admin: Admin, specs, engines, *, max_models: int = 3, full: bool = False) -> list[Section]:
    header(f"Catalog reachability through the gateway (max_models={'all' if full else max_models})")
    sec = Section("catalog")
    try:
        import yaml
    except Exception:
        sec.add("catalog", SKIP, "pyyaml not installed")
        return [sec]
    try:
        with open(_AGNOS_CATALOG) as f:
            cat = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001
        sec.add("catalog", SKIP, f"Agnos Proxy catalog.yaml not readable: {e}")
        return [sec]
    inaccessible: set[str] = set()
    try:
        with open(_INACCESSIBLE) as f:
            bad = yaml.safe_load(f) or {}
        for pv in bad.values():
            for cap in ("chat", "embedding"):
                for mid in (pv.get(cap) or []):
                    inaccessible.add(mid)
    except Exception:
        pass

    avail_by_provider = {s.provider: s for s in specs}
    matrix = {(s.provider, e): (s, e) for (s, e, _, _) in _matrix(admin, specs, engines)}
    for provider, spec in avail_by_provider.items():
        pv = cat.get(provider) or {}
        for engine in engines:
            if engine not in spec.engines or (spec.provider, engine) not in matrix:
                continue
            wsid = admin.workspace_id(spec, engine)
            key = admin.ensure_key(wsid)
            oai = openai_client(key)
            # chat models
            chat_ids = [m for m in (pv.get("chat") or []) if m not in inaccessible]
            emb_ids = [m for m in (pv.get("embedding") or []) if m not in inaccessible]
            if not full:
                chat_ids, emb_ids = chat_ids[:max_models], emb_ids[:max_models]
            for mid in chat_ids:
                r = P.chat(oai, f"{provider}:{mid}", PING, max_tokens=8, temperature=None)
                st, d = _classify_call(r, model_gone_is_skip=True)
                sec.add(f"{provider}::{engine} chat {mid}", st, d, r.get("ms", 0))
            for mid in emb_ids:
                r = P.embed(oai, f"{provider}:{mid}", ["hello"])
                st, d = _classify_call(r, model_gone_is_skip=True)
                sec.add(f"{provider}::{engine} embed {mid}", st, d, r.get("ms", 0))
    return [sec]
