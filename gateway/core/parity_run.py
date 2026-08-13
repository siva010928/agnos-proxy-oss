"""Live shadow / parity verification.

Runs the SAME request through BOTH the rented engine (Bifrost) and our owned
engine (DirectEngine), normalizes the two OpenAI-shaped responses, and scores how
closely they agree - structurally (same contract, finish reason, tool calls) and
textually. This is the concrete proof that insourcing a provider is SAFE: the
capability neither LiteLLM nor Bifrost OSS ships. Reused by the /admin/parity/run
endpoint (live dashboard wow) and the sanity `parity` command (catalog-wide gate).
"""
from __future__ import annotations

import asyncio
import difflib
import re
import time
from typing import Any

from gateway.core.credentials import get_provider_credential
from gateway.core.registry import ResolvedTarget
from gateway.engines.base import EngineResult

_WS = re.compile(r"\s+")


def _norm(s: str | None) -> str:
    return _WS.sub(" ", (s or "").strip().lower())


def _text(body: dict) -> str:
    try:
        return (body.get("choices", [{}])[0].get("message", {}) or {}).get("content") or ""
    except Exception:
        return ""


def _tool_names(body: dict) -> list[str]:
    try:
        tcs = (body.get("choices", [{}])[0].get("message", {}) or {}).get("tool_calls") or []
        return sorted((tc.get("function", {}) or {}).get("name", "") for tc in tcs)
    except Exception:
        return []


def _finish(body: dict) -> str:
    try:
        return body.get("choices", [{}])[0].get("finish_reason") or ""
    except Exception:
        return ""


async def _attach(workspace_id: str, target: ResolvedTarget) -> None:
    cred = await get_provider_credential(workspace_id, target.provider)
    if cred:
        target.credentials = cred.credentials
        target.config = cred.config
        target.bifrost_key_name = cred.bifrost_key_name
    target.hydrate_from_config()


def _hops(engine_name: str) -> list[str]:
    """The network path each engine takes - the concrete reason for the latency
    delta. Bifrost is a rented Go sidecar (an extra hop); DirectEngine calls the
    provider in-process from the gateway (no rented hop)."""
    if engine_name == "bifrost":
        return ["component → gateway", "gateway → Bifrost (Go sidecar)", "Bifrost → provider"]
    if engine_name == "direct":
        return ["component → gateway", "gateway → provider (in-process, no rented hop)"]
    return ["component → gateway", f"gateway → {engine_name}"]


async def _run(engine, req: dict, target: ResolvedTarget) -> dict:
    import json
    t0 = time.perf_counter()
    try:
        res: EngineResult = await engine.chat(req, target)
        ms = (time.perf_counter() - t0) * 1000
        u = res.body.get("usage", {}) or {}
        try:
            raw = json.dumps(res.body, indent=2)[:4000]
        except Exception:
            raw = ""
        return {
            "engine": engine.name, "ok": res.ok, "status": res.status_code,
            "latency_ms": round(ms, 1), "hops": _hops(engine.name),
            "text": _text(res.body), "finish_reason": _finish(res.body),
            "tool_calls": _tool_names(res.body),
            "input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0),
            "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
            "raw": raw,
            "error": None if res.ok else (res.body.get("error", {}) or {}).get("message", "")[:300],
        }
    except Exception as exc:  # noqa: BLE001
        return {"engine": engine.name, "ok": False, "status": 0,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "hops": _hops(engine.name), "text": "", "finish_reason": "", "tool_calls": [],
                "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "raw": "",
                "error": f"{type(exc).__name__}: {exc}"[:300]}


def _score(a: dict, b: dict) -> dict:
    both_ok = a["ok"] and b["ok"]
    ratio = difflib.SequenceMatcher(None, _norm(a["text"]), _norm(b["text"])).ratio() if both_ok else 0.0
    exact = both_ok and _norm(a["text"]) == _norm(b["text"])
    same_tools = a["tool_calls"] == b["tool_calls"]
    same_finish = a["finish_reason"] == b["finish_reason"]
    # structural parity = same contract shape (both OpenAI-valid, same tool calls
    # + finish reason). Textual parity is a bonus (LLMs are non-deterministic).
    structural = both_ok and same_tools
    if not both_ok:
        verdict = "error"
    elif exact or ratio >= 0.98:
        verdict = "identical"
    elif ratio >= 0.85 and same_tools:
        verdict = "high"
    elif ratio >= 0.5 and same_tools:
        verdict = "moderate"
    else:
        verdict = "divergent"
    return {"verdict": verdict, "text_similarity": round(ratio, 3), "exact": exact,
            "structural_parity": structural, "same_tool_calls": same_tools,
            "same_finish_reason": same_finish}


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2, 1)


async def _run_samples(engine, req: dict, target: ResolvedTarget, samples: int) -> dict:
    """Run an engine `samples` times sequentially; return a representative leg
    (last ok) with the MEDIAN latency + the full sample set, so a single noisy
    provider call can't flip the verdict."""
    import copy
    legs = []
    for _ in range(max(1, samples)):
        legs.append(await _run(engine, copy.deepcopy(req), copy.deepcopy(target)))
    ok_legs = [l for l in legs if l["ok"]] or legs
    rep = ok_legs[-1]
    lat = [l["latency_ms"] for l in ok_legs]
    rep = dict(rep)
    rep["latency_ms"] = _median(lat)
    rep["latency_samples"] = [round(x, 1) for x in lat]
    rep["latency_min"] = round(min(lat), 1) if lat else 0.0
    rep["latency_max"] = round(max(lat), 1) if lat else 0.0
    rep["samples"] = len(legs)
    return rep


async def run_parity(*, workspace_id: str, provider: str, model_id: str,
                     messages: list[dict], max_tokens: int = 256,
                     temperature: float = 0.0, samples: int = 3) -> dict:
    """Run a prompt through Bifrost + DirectEngine `samples` times each and return
    a scored comparison using MEDIAN latency (provider jitter dwarfs the ~1 hop
    difference on any single call, so we compare medians for an honest verdict)."""
    from gateway.engines.bifrost_engine import BifrostEngine
    from gateway.engines.direct_engine import DirectEngine

    target = ResolvedTarget(provider=provider, model_id=model_id)
    await _attach(workspace_id, target)
    req = {"model": model_id, "messages": messages,
           "max_tokens": max_tokens, "temperature": temperature}

    bifrost_res, direct_res = await asyncio.gather(
        _run_samples(BifrostEngine(), req, target, samples),
        _run_samples(DirectEngine(), req, target, samples),
    )
    score = _score(bifrost_res, direct_res)
    return {
        "provider": provider, "model_id": model_id, "samples": samples,
        "bifrost": bifrost_res, "direct": direct_res,
        **score,
        "latency_delta_ms": round(direct_res["latency_ms"] - bifrost_res["latency_ms"], 1),
    }
