"""Synthetic governance-history seeder for the Agnos Proxy demo.

Populates RequestLog + GuardrailViolation with 45 days of realistic, tagged
(source='synthetic') traffic across the four Agnos-component workspaces, so
every dashboard panel is rich: diurnal/weekday patterns, a fallback "incident
window", a realistic event-kind mix, real costs from synced LiteLLM pricing.

Usage:
  python scripts/seed_synthetic.py --days 45                 # seed (idempotent-ish)
  python scripts/seed_synthetic.py --days 45 --reset         # wipe synthetic rows first
  python scripts/seed_synthetic.py --reset-only              # just delete synthetic rows
  python scripts/seed_synthetic.py --replay-kafka 200        # publish a recent slice to Kafka
  python scripts/seed_synthetic.py --daily 6000              # avg events/day (weighted)

Only ever touches rows tagged source='synthetic'. Never mints workspaces/keys.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, insert, select

from gateway.config import settings
from gateway.core.pricing import compute_cost, sync_pricing
from gateway.db.database import async_session, init_db
from gateway.db.models import GuardrailViolation, RequestLog
from gateway.db.seed import ANTHROPIC_CLAUDE, BEDROCK_CLAUDE, GEMINI_FLASH

# ── component definitions (the four relabeled workspaces) ──
COMPONENTS = {
    "ws-novatech-payments": {
        "display": "Document Processing", "alias": "claude-sonnet-4-5", "weight": 0.30,
        "primary": ("bedrock", BEDROCK_CLAUDE), "fallback": ("anthropic", ANTHROPIC_CLAUDE),
        "users": ["architect-agent", "backend-agent", "test-agent", "alice.chen", "ravi.k"],
        "use_cases": {"reverse_engineer": 9000, "capability_map": 4000,
                      "spec_validate": 2500, "doc_generate": 1500},
        "guard": {"detectors": ["regex_pii"], "action": "block", "severity": "high"},
    },
    "ws-novatech-platform": {
        "display": "Code Generation", "alias": "claude-sonnet-4-5", "weight": 0.35,
        "primary": ("anthropic", ANTHROPIC_CLAUDE), "fallback": ("bedrock", BEDROCK_CLAUDE),
        "users": ["backend-agent", "frontend-agent", "test-agent", "pipeline-agent",
                  "sam.ortiz", "ravi.k"],
        "use_cases": {"backend_gen": 5000, "frontend_gen": 4000,
                      "test_gen": 3000, "pipeline_gen": 2000},
        "guard": {"detectors": ["secrets", "keyword"], "action": "block", "severity": "high"},
    },
    "ws-novatech-knowledge": {
        "display": "Search Index", "alias": "gemini-flash", "weight": 0.22,
        "primary": ("gemini", GEMINI_FLASH), "fallback": ("bedrock", BEDROCK_CLAUDE),
        "users": ["architect-agent", "context-agent", "alice.chen", "maya.p"],
        "use_cases": {"superspec_index": 6000, "context_retrieval": 3000, "pattern_match": 1500},
        "guard": {"detectors": ["regex_pii"], "action": "redact", "severity": "medium"},
    },
    "ws-novatech-payments": {
        "display": "Control Plane", "alias": "claude-sonnet-4-5", "weight": 0.13,
        "primary": ("bedrock", BEDROCK_CLAUDE), "fallback": None,
        "users": ["policy-agent", "audit-agent", "sam.ortiz"],
        "use_cases": {"policy_check": 500, "lineage_trace": 1200, "usage_audit": 800},
        "guard": {"detectors": ["regex_pii"], "action": "audit", "severity": "low"},
    },
}

# event-kind base mix (per total)
EVENT_MIX = [("completion", 0.92), ("fallback", 0.025), ("guardrail_block", 0.02),
             ("cache_hit", 0.02), ("rate_limited", 0.01), ("error", 0.005)]

# per-provider latency (lognormal): (mu of ln(ms), sigma)
PROVIDER_LAT = {"bedrock": (math.log(900), 0.45), "anthropic": (math.log(800), 0.42),
                "gemini": (math.log(700), 0.40)}

GUARDRAIL_SAMPLES = {
    "regex_pii": [("ssn", "123***"), ("email", "bob.***"), ("us_phone", "415***")],
    "secrets": [("aws_access_key", "AKIA***"), ("openai_key", "sk-p***")],
    "keyword": [("blocklist", "proj***"), ("blocklist", "conf***")],
    "presidio": [("PERSON", "Jane***")],
}

INCIDENT_DAYS = {29, 30}  # ~day 30 of 45: a provider spikes failures → fallbacks rise


def _diurnal_weight(hour: int) -> float:
    # gaussian peak ~13:00 + small overnight floor
    return 0.15 + math.exp(-((hour - 13) ** 2) / (2 * 4.0 ** 2))


def _lognormal_clamped(mu: float, sigma: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, random.lognormvariate(mu, sigma)))


def _pick_event_kind(ws_id: str, day_index: int) -> str:
    mix = dict(EVENT_MIX)
    comp = COMPONENTS[ws_id]
    if comp["fallback"] is None:
        mix["fallback"] = 0.0           # Control Plane has no fallback chain
    # incident window: primary provider (bedrock) degrades → more fallbacks + errors
    if ws_id == "ws-novatech-payments" and day_index in INCIDENT_DAYS:
        mix["fallback"] = 0.22
        mix["error"] = 0.04
    kinds, weights = zip(*mix.items())
    return random.choices(kinds, weights=weights, k=1)[0]


def _daily_count(ws_id: str, day_index: int, total_days: int, daily_total: int) -> int:
    comp = COMPONENTS[ws_id]
    growth = 0.7 + 0.3 * (day_index / max(1, total_days - 1))   # mild upward trend
    base = daily_total * comp["weight"] * growth
    return max(1, int(base * random.uniform(0.85, 1.15)))


async def reset_synthetic() -> tuple[int, int]:
    async with async_session() as s:
        r1 = await s.execute(delete(RequestLog).where(RequestLog.source == "synthetic"))
        r2 = await s.execute(delete(GuardrailViolation).where(GuardrailViolation.source == "synthetic"))
        await s.commit()
        return r1.rowcount or 0, r2.rowcount or 0


async def seed(days: int, daily_total: int) -> dict:
    await sync_pricing()
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days - 1)
    rows: list[dict] = []
    gvs: list[dict] = []
    by_ws: dict[str, int] = {k: 0 for k in COMPONENTS}

    for d in range(days):
        day_date = (start + timedelta(days=d)).date()
        is_weekend = day_date.weekday() >= 5
        weekend_factor = 0.4 if is_weekend else 1.0
        for ws_id in COMPONENTS:
            n = int(_daily_count(ws_id, d, days, daily_total) * weekend_factor)
            # distribute across hours by diurnal weight
            hour_weights = [_diurnal_weight(h) for h in range(24)]
            tot = sum(hour_weights)
            for h in range(24):
                cnt = int(round(n * hour_weights[h] / tot))
                for _ in range(cnt):
                    ts = datetime(day_date.year, day_date.month, day_date.day, h,
                                  random.randint(0, 59), random.randint(0, 59))
                    if ts > now:
                        continue
                    kind = _pick_event_kind(ws_id, d)
                    row, gv = _make_row_kind(ws_id, ts, kind)
                    rows.append(row)
                    by_ws[ws_id] += 1
                    if gv:
                        gvs.append(gv)

    # bulk insert in chunks
    inserted = await _bulk_insert(rows, gvs)
    return {"request_logs": inserted, "guardrail_violations": len(gvs), "by_workspace": by_ws,
            "from": start.date().isoformat(), "to": now.date().isoformat()}


def _make_row_kind(ws_id: str, ts: datetime, kind: str) -> tuple[dict, dict | None]:
    # wrapper that injects the kind without abusing datetime.__dict__
    comp = COMPONENTS[ws_id]
    user = random.choice(comp["users"])
    use_case, in_mu = random.choice(list(comp["use_cases"].items()))
    provider, model_id = comp["primary"]
    in_tok = int(_lognormal_clamped(math.log(max(50, in_mu)), 0.6, 50, 60000))
    out_tok = int(_lognormal_clamped(math.log(350), 0.7, 30, 1500))
    mu, sigma = PROVIDER_LAT[provider]
    latency = _lognormal_clamped(mu, sigma, 120, 9000)
    status, cost, gv = "success", None, None
    cost = compute_cost(model_id, in_tok, out_tok)

    if kind == "fallback" and comp["fallback"]:
        fb_provider, fb_model = comp["fallback"]
        fmu, fsigma = PROVIDER_LAT[fb_provider]
        latency += _lognormal_clamped(fmu, fsigma, 120, 9000)
        provider, model_id = fb_provider, fb_model
        cost = compute_cost(model_id, in_tok, out_tok)
    elif kind == "cache_hit":
        latency, cost, out_tok = random.uniform(1, 8), 0.0, 0
    elif kind == "rate_limited":
        status, latency, cost, out_tok = "rate_limited", random.uniform(1, 5), 0.0, 0
    elif kind == "error":
        status, cost, out_tok = "error", 0.0, 0
    elif kind == "guardrail_block":
        status, cost, out_tok = "blocked", 0.0, 0
        det = random.choice(comp["guard"]["detectors"])
        cat, excerpt = random.choice(GUARDRAIL_SAMPLES.get(det, [("pii", "***")]))
        gv = {"timestamp": ts, "request_id": f"syn-{uuid.uuid4().hex[:12]}",
              "workspace_id": ws_id, "rule": "default", "detector": det,
              "action": comp["guard"]["action"], "stage": "input",
              "excerpt": f"{cat}:{excerpt}", "severity": comp["guard"]["severity"],
              "source": "synthetic"}

    row = {"timestamp": ts, "request_id": f"syn-{uuid.uuid4().hex[:12]}",
           "workspace_id": ws_id, "user_id": user, "use_case": use_case, "engine": "bifrost",
           "provider": provider, "model_alias": comp["alias"], "provider_model_id": model_id,
           "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": round(cost, 8),
           "latency_ms": round(latency, 2), "stream": False, "status": status,
           "error_type": "upstream_error" if status == "error" else None,
           "call_kind": "chat", "event_kind": kind, "source": "synthetic"}
    return row, gv


async def _bulk_insert(rows: list[dict], gvs: list[dict], chunk: int = 2000) -> int:
    async with async_session() as s:
        for i in range(0, len(rows), chunk):
            await s.execute(insert(RequestLog), rows[i:i + chunk])
            await s.commit()
        for i in range(0, len(gvs), chunk):
            await s.execute(insert(GuardrailViolation), gvs[i:i + chunk])
            await s.commit()
    return len(rows)


async def replay_kafka(n: int) -> int:
    """Publish the N most-recent synthetic rows as canonical envelopes to Kafka."""
    if not settings.kafka_brokers:
        print("KAFKA_BROKERS not set; skipping replay")
        return 0
    from aiokafka import AIOKafkaProducer
    async with async_session() as s:
        rows = (await s.scalars(
            select(RequestLog).where(RequestLog.source == "synthetic")
            .order_by(RequestLog.timestamp.desc()).limit(n))).all()
    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_brokers)
    await producer.start()
    sent = 0
    try:
        import json
        for r in rows:
            env = {"schema_version": 1, "occurred_at": r.timestamp.replace(tzinfo=timezone.utc).isoformat(),
                   "idempotency_key": str(uuid.uuid4()),
                   "event_kind": "completion" if r.event_kind == "completion" else r.event_kind,
                   "correlation_id": r.request_id,
                   "payload": {"workspace_id": r.workspace_id, "user_id": r.user_id, "use_case": r.use_case,
                               "component": None, "engine": r.engine, "provider": r.provider,
                               "model": r.model_alias, "provider_model_id": r.provider_model_id,
                               "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                               "cost_usd": r.cost_usd, "latency_ms": r.latency_ms, "status": r.status,
                               "metadata": {"call_kind": r.call_kind, "source": "synthetic"}}}
            await producer.send_and_wait(settings.kafka_topic, json.dumps(env).encode(),
                                         key=(r.workspace_id or "").encode())
            sent += 1
    finally:
        await producer.stop()
    return sent


async def main() -> None:
    ap = argparse.ArgumentParser(description="Seed synthetic gateway governance history")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--daily", type=int, default=5000, help="avg events/day across all workspaces")
    ap.add_argument("--reset", action="store_true", help="delete synthetic rows before seeding")
    ap.add_argument("--reset-only", action="store_true", help="only delete synthetic rows")
    ap.add_argument("--replay-kafka", type=int, default=0, metavar="N")
    args = ap.parse_args()

    await init_db()
    if args.reset or args.reset_only:
        d1, d2 = await reset_synthetic()
        print(f"reset: deleted {d1} request_logs + {d2} guardrail_violations (synthetic)")
        if args.reset_only:
            return

    if args.replay_kafka:
        sent = await replay_kafka(args.replay_kafka)
        print(f"replay-kafka: published {sent} envelopes to {settings.kafka_topic}")
        return

    t0 = datetime.now()
    summary = await seed(args.days, args.daily)
    dt = (datetime.now() - t0).total_seconds()
    print(f"seeded {summary['request_logs']} request_logs + {summary['guardrail_violations']} "
          f"guardrail_violations over {args.days} days [{summary['from']}..{summary['to']}] in {dt:.1f}s")
    for ws, n in summary["by_workspace"].items():
        print(f"  {COMPONENTS[ws]['display']:18} {ws:16} {n}")


if __name__ == "__main__":
    asyncio.run(main())
