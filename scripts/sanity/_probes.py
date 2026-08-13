"""Low-level probes that call the gateway through the OpenAI SDK (the real wire).

Every probe returns a small dict with ok/status/usage/correlation_id/text so the
command modules can assert both the call AND the governance it produced.
"""
from __future__ import annotations

import time

import openai


def _cid(raw) -> str:
    try:
        return raw.headers.get("x-gateway-correlation-id", "") or ""
    except Exception:
        return ""


def chat(oai, model: str, messages: list[dict], *, max_tokens: int = 64,
         temperature: float | None = 0.0, extra_body: dict | None = None) -> dict:
    t0 = time.perf_counter()
    try:
        kw: dict = {"model": model, "messages": messages, "max_tokens": max_tokens,
                    "extra_body": extra_body or None}
        # Some models (e.g. Anthropic Opus 4-7/4-8) reject `temperature`; pass None
        # to omit it entirely.
        if temperature is not None:
            kw["temperature"] = temperature
        raw = oai.chat.completions.with_raw_response.create(**kw)
        ms = (time.perf_counter() - t0) * 1000
        comp = raw.parse()
        # Defensive: thinking models with a tiny max_tokens can return 200 with an
        # empty/None choice. A 200 still means the model is REACHABLE.
        choices = getattr(comp, "choices", None) or []
        msg = choices[0].message if choices else None
        text = (getattr(msg, "content", None) or "") if msg else ""
        tcs = (getattr(msg, "tool_calls", None) or []) if msg else []
        usage = comp.usage.model_dump() if getattr(comp, "usage", None) else {}
        return {"ok": True, "status": raw.status_code, "cid": _cid(raw), "ms": ms,
                "text": text, "tool_calls": tcs, "usage": usage}
    except openai.APIStatusError as e:
        cid = ""
        try:
            cid = e.response.headers.get("x-gateway-correlation-id", "") or ""
        except Exception:
            pass
        return {"ok": False, "status": e.status_code, "cid": cid, "ms": (time.perf_counter() - t0) * 1000,
                "exc": type(e).__name__, "error": str(e)[:200]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "cid": "", "ms": (time.perf_counter() - t0) * 1000,
                "exc": type(e).__name__, "error": str(e)[:200]}


def embed(oai, model: str, inputs: list[str], *, dimensions: int | None = None) -> dict:
    t0 = time.perf_counter()
    try:
        kw = {"model": model, "input": inputs}
        if dimensions:
            kw["dimensions"] = dimensions
        raw = oai.embeddings.with_raw_response.create(**kw)
        ms = (time.perf_counter() - t0) * 1000
        resp = raw.parse()
        dim = len(resp.data[0].embedding) if resp.data else 0
        usage = resp.usage.model_dump() if resp.usage else {}
        return {"ok": True, "status": raw.status_code, "cid": _cid(raw), "ms": ms,
                "n": len(resp.data), "dim": dim, "usage": usage}
    except openai.APIStatusError as e:
        cid = ""
        try:
            cid = e.response.headers.get("x-gateway-correlation-id", "") or ""
        except Exception:
            pass
        return {"ok": False, "status": e.status_code, "cid": cid, "ms": (time.perf_counter() - t0) * 1000,
                "exc": type(e).__name__, "error": str(e)[:200]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "cid": "", "ms": (time.perf_counter() - t0) * 1000,
                "exc": type(e).__name__, "error": str(e)[:200]}
