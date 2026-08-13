"""BVT (Backend Verification & Trust) integration suite.

These tests run against a running gateway (real Postgres + Redis + Kafka +
auth + routing + guardrails + governance) but with the LLM upstream swapped
to the in-process EchoEngine, so the entire suite runs at $0.

Default `pytest` deselects them via `-m 'not live'` only, so to run only the
integration suite use:

    pytest -m integration -v

Or to run against a different gateway:

    GATEWAY_URL=http://localhost:8090 pytest -m integration

Tests are NOT marked `live` (which is reserved for capped real-provider smoke).
"""
