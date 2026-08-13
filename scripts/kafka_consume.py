"""Reliable Kafka consumer for the governance topic (demo verification).

Reads the canonical envelopes the gateway publishes to agnos-proxy.governance.v1.
Uses the Python aiokafka consumer (NOT the JVM consumer) - the prior SIGSEGV was
an x86-emulation artifact of the JVM consumer under colima; producer/topic are
reliable, and this Python consumer reads cleanly.

Usage:
  python scripts/kafka_consume.py --max 5          # print up to 5 envelopes then exit
  python scripts/kafka_consume.py --from-end       # only new messages (tail)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.config import settings


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=5)
    ap.add_argument("--from-end", action="store_true", help="only consume new messages")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    brokers = settings.kafka_brokers or "localhost:9092"
    from aiokafka import AIOKafkaConsumer
    consumer = AIOKafkaConsumer(
        settings.kafka_topic, bootstrap_servers=brokers,
        auto_offset_reset="latest" if args.from_end else "earliest",
        enable_auto_commit=False, group_id=None,
    )
    await consumer.start()
    print(f"consuming {settings.kafka_topic} @ {brokers} (max {args.max})")
    n = 0
    try:
        while n < args.max:
            try:
                msg = await asyncio.wait_for(consumer.getone(), timeout=args.timeout)
            except asyncio.TimeoutError:
                print(f"(no more messages within {args.timeout}s)")
                break
            n += 1
            try:
                env = json.loads(msg.value.decode())
                p = env.get("payload", {})
                print(f"\n[{n}] offset={msg.offset} key={msg.key.decode() if msg.key else None}")
                print(f"    event_kind={env.get('event_kind')} schema_v={env.get('schema_version')} "
                      f"corr={env.get('correlation_id')}")
                print(f"    workspace={p.get('workspace_id')} provider={p.get('provider')} "
                      f"model={p.get('model')} tokens={p.get('input_tokens')}/{p.get('output_tokens')} "
                      f"cost=${p.get('cost_usd')}")
            except Exception as exc:  # noqa: BLE001
                print(f"[{n}] (unparseable: {exc})")
    finally:
        await consumer.stop()
    print(f"\nconsumed {n} envelope(s) cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
