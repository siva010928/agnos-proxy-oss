"""Sanity harness client: provisions a dedicated sanity tenant via the admin API,
gives OpenAI-SDK clients pointed at the gateway (exactly how a component calls it),
and reads governance back so we can prove usage/cost were recorded per request.
"""
from __future__ import annotations

import json
import os
import time

import httpx

from scripts.sanity._env import ProviderSpec

BASE = os.environ.get("SANITY_GATEWAY_URL", "http://localhost:8090")
ADMIN_TOKEN = os.environ.get("SANITY_ADMIN_TOKEN", "platform-admin-secret")
JAEGER = os.environ.get("SANITY_JAEGER_URL", "http://localhost:16686")
OTEL_SERVICE = os.environ.get("SANITY_OTEL_SERVICE", "agnos-proxy-llm-gateway")
CLIENT_ID = "sanity-co"
_STATE = os.path.join(os.path.dirname(__file__), ".sanity_state.json")


def _load_state() -> dict:
    try:
        with open(_STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        with open(_STATE, "w") as f:
            json.dump(st, f, indent=2)
    except Exception:
        pass


class Admin:
    def __init__(self, base: str = BASE, token: str = ADMIN_TOKEN):
        self.base = base.rstrip("/")
        self.h = {"X-Admin-Token": token, "Content-Type": "application/json"}
        self.c = httpx.Client(timeout=30)
        self._state = _load_state()

    # ── low level ──
    def _post(self, path: str, body: dict) -> httpx.Response:
        return self.c.post(f"{self.base}{path}", headers=self.h, json=body)

    def _patch(self, path: str, body: dict) -> httpx.Response:
        return self.c.patch(f"{self.base}{path}", headers=self.h, json=body)

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        return self.c.get(f"{self.base}{path}", headers=self.h, params=params or {})

    def health(self) -> bool:
        try:
            return self.c.get(f"{self.base}/health", timeout=5).status_code == 200
        except Exception:
            return False

    # ── provisioning (idempotent) ──
    def ensure_client(self) -> None:
        r = self._post("/admin/clients", {"client_id": CLIENT_ID, "name": "Sanity Suite",
                                          "required_headers": [], "budgets": {}, "rate_limits": {}})
        if r.status_code not in (200, 201, 409):
            # a 409 (exists) is fine; anything else is surfaced
            raise RuntimeError(f"ensure_client failed: {r.status_code} {r.text[:200]}")

    def workspace_id(self, spec: ProviderSpec, engine: str, suffix: str = "") -> str:
        return f"san-{spec.key}-{engine}" + (f"-{suffix}" if suffix else "")

    def ensure_workspace(self, spec: ProviderSpec, engine: str, *,
                         rate_rpm: int | None = None, suffix: str = "") -> str:
        wsid = self.workspace_id(spec, engine, suffix)
        chat_alias = {"chat": [{"provider": spec.provider, "model_id": spec.chat_model,
                                "context_window": 200_000}]} if spec.chat_model else {}
        embed_alias = {"embed": [{"provider": spec.provider, "model_id": spec.embed_model}]} \
            if spec.embed_model else {}
        body = {"workspace_id": wsid, "client_id": CLIENT_ID, "name": wsid,
                "chat_models": chat_alias, "embedding_models": embed_alias,
                "default_chat_alias": "chat" if chat_alias else None}
        if rate_rpm is not None:
            body["rate_limits"] = {"rpm": rate_rpm, "tpm": 10_000_000}
        r = self._post("/admin/workspaces", body)
        # keep current on re-runs
        patch = {"chat_models": chat_alias, "embedding_models": embed_alias,
                 "default_chat_alias": "chat" if chat_alias else None,
                 "engine_overrides": {spec.provider: "direct"} if engine == "direct" else {}}
        if rate_rpm is not None:
            patch["rate_limits"] = {"rpm": rate_rpm, "tpm": 10_000_000}
        self._patch(f"/admin/workspaces/{wsid}", patch)
        # provider credentials (upsert)
        cfg = {}
        if spec.credentials.get("region"):
            cfg["region"] = spec.credentials["region"]
        cfg.update(getattr(spec, "config", None) or {})   # e.g. vertex_project/location
        pr = self._post(f"/admin/workspaces/{wsid}/providers",
                        {"provider": spec.provider, "credentials": spec.credentials, "config": cfg})
        if pr.status_code not in (200, 201):
            raise RuntimeError(f"add_provider {wsid} failed: {pr.status_code} {pr.text[:200]}")
        return wsid

    def ensure_key(self, wsid: str) -> str:
        cached = (self._state.get("keys") or {}).get(wsid)
        if cached:
            return cached
        r = self._post(f"/admin/workspaces/{wsid}/keys", {"roles": ["member", "admin"]})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"issue_key {wsid} failed: {r.status_code} {r.text[:200]}")
        key = r.json()["api_key"]
        self._state.setdefault("keys", {})[wsid] = key
        _save_state(self._state)
        return key

    # ── governance readback ──
    def find_log(self, correlation_id: str, timeout: float = 8.0) -> dict | None:
        """Poll request-logs for the row with this correlation id (bus is async)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self._get("/admin/request-logs", {"request_id": correlation_id, "limit": 5})
            if r.status_code == 200:
                rows = r.json().get("rows") or r.json().get("logs") or []
                for row in rows:
                    if correlation_id in (row.get("request_id") or ""):
                        return row
            time.sleep(0.5)
        return None

    def models(self) -> list[dict]:
        r = self._get("/admin/models")
        if r.status_code != 200:
            return []
        d = r.json()
        return d if isinstance(d, list) else d.get("models", [])

    def parity_run(self, wsid: str, provider: str, model_id: str, prompt: str,
                   max_tokens: int = 64) -> dict:
        r = self._post("/admin/parity/run", {"workspace_id": wsid, "provider": provider,
                                             "model_id": model_id, "prompt": prompt,
                                             "max_tokens": max_tokens})
        r.raise_for_status()
        return r.json()

    def facets(self, **filters) -> dict:
        """Cascading filter options for the Logs/Analytics/Routing bars. Every
        facet is scoped by the OTHER selected filters (parent-aware)."""
        params = {k: v for k, v in filters.items() if v is not None}
        r = self._get("/admin/request-logs/facets", params)
        return r.json() if r.status_code == 200 else {}

    # ── client→workspace hierarchy (drives the Providers/Keys/Routing pickers) ──
    def clients(self) -> list[dict]:
        r = self._get("/admin/clients")
        return (r.json().get("clients") or []) if r.status_code == 200 else []

    def workspaces(self) -> list[dict]:
        r = self._get("/admin/workspaces")
        return (r.json().get("workspaces") or []) if r.status_code == 200 else []

    def providers(self, wsid: str) -> list[dict]:
        r = self._get(f"/admin/workspaces/{wsid}/providers")
        return (r.json().get("providers") or []) if r.status_code == 200 else []

    def keys(self, wsid: str) -> list[dict]:
        r = self._get(f"/admin/workspaces/{wsid}/keys")
        return (r.json().get("keys") or []) if r.status_code == 200 else []

    def available_models(self, wsid: str, provider: str) -> dict:
        r = self._get(f"/admin/workspaces/{wsid}/providers/{provider}/available-models")
        return r.json() if r.status_code == 200 else {"ok": False, "error": f"HTTP {r.status_code}", "models": []}

    def test_provider(self, provider: str, credentials: dict, config: dict | None = None,
                      model_id: str | None = None) -> dict:
        """The admin/providers 'Test Connection' flow: a real 1-token probe with the
        supplied creds (isolated; no env/ambient fallback)."""
        r = self._post("/admin/providers/test", {"provider": provider, "credentials": credentials,
                                                 "config": config or {}, "model_id": model_id})
        return r.json() if r.status_code == 200 else {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}

    # ── observability readback ──
    def metrics_text(self) -> str:
        try:
            r = self.c.get(f"{self.base}/metrics", timeout=10)
            return r.text if r.status_code == 200 else ""
        except Exception:
            return ""

    def jaeger_trace(self, correlation_id: str, timeout: float = 15.0) -> dict | None:
        """Poll the Jaeger query API for a trace tagged with this correlation id.
        Returns the first matching trace ({traceID, spans:[...]}) or None."""
        import json as _json
        tags = _json.dumps({"correlation_id": correlation_id})
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = self.c.get(f"{JAEGER}/api/traces", params={
                    "service": OTEL_SERVICE, "tags": tags, "limit": "20", "lookback": "2h"}, timeout=8)
                if r.status_code == 200:
                    data = (r.json() or {}).get("data") or []
                    if data:
                        return data[0]
            except Exception:
                pass
            time.sleep(1.0)
        return None

    def jaeger_up(self) -> bool:
        try:
            return self.c.get(f"{JAEGER}/api/services", timeout=5).status_code == 200
        except Exception:
            return False


def openai_client(api_key: str):
    """An OpenAI SDK client pointed at the gateway - exactly what a component uses."""
    from openai import OpenAI
    return OpenAI(base_url=f"{BASE}/v1", api_key=api_key, max_retries=0, timeout=120)
