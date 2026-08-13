"""Engine x Provider sanity matrix - production-grade cross-check.

For every engine that can occupy the swappable slot (bifrost, litellm, portkey,
direct) x every configured+keyed provider (anthropic, bedrock, gemini), route a
tiny real chat THROUGH THE GATEWAY and confirm it returns 200 and that the
engine-of-record recorded matches the engine we routed to. Restores routing at
the end. This is how we prove all engines serve all providers before a live demo.

Run (gateway on :8090, engines' containers up):
    PYTHONPATH=. .venv/bin/python scripts/engine_provider_sanity.py
"""
import json
import sys
import time
import urllib.request

GW = "http://localhost:8090"
ADMIN = {"x-admin-token": "platform-admin-secret", "Content-Type": "application/json"}

# (workspace key, provider, explicit Mode-B model) - configured + keyed providers
MATRIX = [
    ("gw-key-primary-001", "anthropic", "anthropic:claude-sonnet-4-5-20250929"),
    ("gw-key-primary-001", "bedrock",   "bedrock:us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    ("gw-key-gemini-001",  "gemini",    "gemini:gemini-2.5-flash"),
]
ENGINES = ["bifrost", "litellm", "portkey", "direct"]


def _req(method, path, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(GW + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": {"message": str(e)[:200]}}


def set_route(provider, engine):
    _req("PATCH", "/admin/engine-routing", ADMIN, {"overrides": {provider: engine}})


def last_log():
    _, d = _req("GET", "/admin/request-logs?limit=1", {"x-admin-token": "platform-admin-secret"})
    rows = d.get("rows") or []
    return rows[0] if rows else {}


def chat(ws_key, model):
    h = {"Authorization": f"Bearer {ws_key}", "Content-Type": "application/json",
         "X-Gateway-Component": "code-generation"}
    code, d = _req("POST", "/v1/chat/completions", h,
                   {"model": model, "messages": [{"role": "user", "content": "Reply OK"}],
                    "max_tokens": 24})
    reply = ""
    try:
        reply = (d.get("choices", [{}])[0].get("message", {}) or {}).get("content", "")
    except Exception:
        pass
    err = "" if code == 200 else str(d.get("error", {}).get("message", d))[:90]
    return code, reply, err


def main():
    print(f"{'engine':9} {'provider':10} {'HTTP':5} {'engine-of-record':17} {'status':8} note")
    print("-" * 78)
    results = []
    for engine in ENGINES:
        for ws_key, provider, model in MATRIX:
            set_route(provider, engine)
            time.sleep(0.3)
            code, reply, err = chat(ws_key, model)
            time.sleep(0.8)
            row = last_log()
            eng_rec = row.get("engine", "?")
            status = row.get("status", "?")
            ok = (code == 200 and eng_rec == engine and status == "success")
            note = "OK" if ok else (err or f"engine={eng_rec}")
            mark = "\u2713" if ok else "\u2717"
            print(f"{engine:9} {provider:10} {code:<5} {eng_rec:17} {status:8} {mark} {note}")
            results.append(ok)
    # restore clean routing
    _req("POST", "/admin/engine/restore", ADMIN, {"to": "bifrost"})
    passed = sum(results)
    print("-" * 78)
    print(f"PASSED {passed}/{len(results)}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
