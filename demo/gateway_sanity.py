"""demo/gateway_sanity.py - one command that exercises EVERY major gateway feature, for real.

A production readiness sweep you can run before a demo or in CI. It hits a live
gateway (local or prod), performs real operations, and prints a PASS/FAIL line per feature plus a
summary. It restores the engine to bifrost at the end.

    python demo/gateway_sanity.py
    GATEWAY_URL=http://localhost:8090 python demo/gateway_sanity.py
    ... --engines bifrost,litellm,direct,echo    # limit the engine matrix
    ... --skip-coupling                          # skip the slower checks

What it checks: health + readiness; preview auth; per-provider reachability; the FULL engine
matrix (swap each engine + a real governed chat through it); per-component attribution in the
request log; cost / analytics / models / guardrails / parity endpoints; workspace + key mint;
Prometheus metrics; and the build-enforced
anti-coupling audit.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import httpx

GW = os.getenv("GATEWAY_URL", "http://localhost:8090").rstrip("/")
ADMIN_TOKEN = os.getenv("PLATFORM_ADMIN_TOKEN", "platform-admin-secret")
G, R, Y, X = "\033[92m", "\033[91m", "\033[93m", "\033[0m"

results: list[tuple[str, str, str]] = []  # (status, name, detail)


def rec(status: str, name: str, detail: str = "") -> bool:
    tag = {"PASS": f"{G}PASS{X}", "FAIL": f"{R}FAIL{X}", "SKIP": f"{Y}SKIP{X}"}[status]
    print(f"  [{tag}] {name}" + (f"  - {detail}" if detail else ""), flush=True)
    results.append((status, name, detail))
    return status == "PASS"


def _auth(c: httpx.Client) -> dict:
    try:
        if c.post("/auth/preview", json={"preview_name": "sanity"}).status_code == 200:
            return {}
    except Exception:  # noqa: BLE001
        pass
    return {"X-Admin-Token": ADMIN_TOKEN}


def _anthropic_workspace(c: httpx.Client, hdr: dict) -> str | None:
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
    return None


def _chat(c: httpx.Client, key: str, model: str, component: str, timeout: float = 40) -> tuple[bool, str]:
    r = c.post("/v1/chat/completions",
               headers={"Authorization": f"Bearer {key}", "X-Gateway-Component": component},
               json={"model": model, "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
                     "max_tokens": 8}, timeout=timeout)
    try:
        d = r.json()
    except Exception:  # noqa: BLE001
        return False, f"non-json {r.status_code}"
    if r.status_code == 200 and "choices" in d:
        return True, (d["choices"][0]["message"]["content"] or "").strip()[:40]
    return False, str(d.get("error") or d)[:120]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="bifrost,litellm,direct,echo,portkey")
    ap.add_argument("--model", default="anthropic:claude-sonnet-4-5")
    ap.add_argument("--skip-coupling", action="store_true")
    a = ap.parse_args()

    print(f"\n=== Agnos Proxy sanity :: {GW} ===\n")
    c = httpx.Client(base_url=GW, timeout=30.0)

    # ── platform ──
    try:
        rec("PASS" if c.get("/health").json().get("status") == "ok" else "FAIL", "health")
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "health", str(e)[:80]); print(f"\n{R}gateway unreachable - stopping.{X}"); return 2
    try:
        rd = c.get("/health/ready").json()
        rec("PASS" if rd.get("ready") else "FAIL", "readiness", str(rd.get("checks")))
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "readiness", str(e)[:80])

    hdr = _auth(c)
    try:
        me = c.get("/auth/me", headers=hdr).json()
        rec("PASS" if me.get("authenticated") and "admin" in (me.get("roles") or []) else "FAIL",
            "auth (preview admin session)", f"roles={me.get('roles')}")
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "auth", str(e)[:80])

    # ── provider reachability (through the active engine) ──
    try:
        ph = c.get("/health/providers", params={"force": "true"}, headers=hdr, timeout=70).json()
        provs = ph.get("providers") or {}
        for p, info in provs.items():
            rec("PASS" if info.get("reachable") else "FAIL", f"provider · {p}",
                info.get("model_id") if info.get("reachable") else str(info.get("error"))[:80])
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "provider health", str(e)[:80])

    # ── a workspace key for the engine + chat tests ──
    wsid = _anthropic_workspace(c, hdr)
    key = None
    if wsid:
        try:
            key = c.post(f"/admin/workspaces/{wsid}/keys", headers=hdr, json={"roles": ["member"]}).json()["api_key"]
            rec("PASS", "workspace + key mint (RBAC)", f"{wsid} · {key[:16]}...")
        except Exception as e:  # noqa: BLE001
            rec("FAIL", "workspace + key mint", str(e)[:80])
    else:
        rec("FAIL", "workspace + key mint", "no anthropic workspace found")

    # ── ENGINE MATRIX: swap each engine + a real governed chat through it ──
    catalog = c.get("/admin/engine/catalog", headers=hdr).json().get("engines") or {}
    want = [e.strip() for e in a.engines.split(",") if e.strip() in catalog]
    for eng in want:
        try:
            c.post("/admin/engine", headers=hdr, json={"engine": eng}, timeout=25)
            if eng == "litellm":
                c.post("/admin/engine/reconcile", headers=hdr, timeout=90)
            time.sleep(1.0)
            if not key:
                rec("SKIP", f"engine · {eng} (chat)", "no key"); continue
            # echo ignores the upstream provider (deterministic), but the request still resolves a
            # normal model through the registry - so use the same model for every engine.
            ok, detail = _chat(c, key, a.model, "sanity-engine")
            # echo has no upstream; a 200 with any content is success
            rec("PASS" if ok else "FAIL", f"engine · {eng} (swap + live chat)", detail)
        except Exception as e:  # noqa: BLE001
            rec("FAIL", f"engine · {eng}", str(e)[:100])
    # restore stable engine for the remaining checks
    c.post("/admin/engine", headers=hdr, json={"engine": "bifrost"}, timeout=25); time.sleep(1.0)

    # ── governance: the chats above must be attributed in the request log ──
    try:
        time.sleep(2.0)
        rl = c.get("/admin/request-logs", params={"component": "sanity-engine", "limit": 50}, headers=hdr).json()
        rows = rl.get("rows") or []
        rec("PASS" if rows else "FAIL", "governance · per-component attribution (request logs)",
            f"{len(rows)} governed rows for 'sanity-engine'")
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "request logs", str(e)[:80])

    # ── analytics / config endpoints ──
    for name, path, params in [
        ("cost rollup", "/admin/cost", {"group_by": "component"}),
        ("usage timeseries", "/admin/usage/timeseries", {"granularity": "day"}),
        ("models catalog", "/admin/models", {}),
        ("guardrail rules", "/admin/guardrails", {}),
        ("shadow parity", "/admin/parity", {}),
    ]:
        try:
            r = c.get(path, params=params, headers=hdr, timeout=30)
            rec("PASS" if r.status_code == 200 else "FAIL", f"feature · {name}", f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            rec("FAIL", f"feature · {name}", str(e)[:80])

    # ── metrics (Prometheus) ──
    try:
        m = c.get("/metrics", timeout=15)
        rec("PASS" if m.status_code == 200 and "gateway" in m.text.lower() else "FAIL",
            "observability · Prometheus /metrics", f"{len(m.text)} bytes")
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "metrics", str(e)[:80])

    # ── anti-coupling (build-enforced decoupling) ──
    if a.skip_coupling:
        rec("SKIP", "anti-coupling audit", "--skip-coupling")
    else:
        try:
            repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_anti_coupling.py", "-q",
                                "-p", "no:cacheprovider"], cwd=repo, capture_output=True, text=True)
            rec("PASS" if r.returncode == 0 else "FAIL", "anti-coupling audit (build-enforced)",
                r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "")
        except Exception as e:  # noqa: BLE001
            rec("FAIL", "anti-coupling audit", str(e)[:80])

    # ── summary ──
    p = sum(1 for s, _, _ in results if s == "PASS")
    f = sum(1 for s, _, _ in results if s == "FAIL")
    sk = sum(1 for s, _, _ in results if s == "SKIP")
    print(f"\n=== {G}{p} passed{X}, {R+'{} failed'.format(f)+X if f else '0 failed'}, {sk} skipped "
          f"on {GW} ===")
    if f:
        print(f"{R}Failures:{X}")
        for s, n, d in results:
            if s == "FAIL":
                print(f"  - {n}: {d}")
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
