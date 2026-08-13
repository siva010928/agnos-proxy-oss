"""OpenTelemetry tracing - request spans with workspace/provider/model/tokens.

Exports OTLP to OTEL_EXPORTER_OTLP_ENDPOINT if set (e.g. an OTel collector),
otherwise falls back to a console exporter so spans are always visible.
FastAPI is auto-instrumented; we also create explicit gateway pipeline spans.
"""
from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

_TRACER = None


def setup_tracing(service_name: str = "agnos-proxy-llm-gateway") -> None:
    global _TRACER
    if _TRACER is not None:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    elif os.getenv("OTEL_CONSOLE", "").lower() == "true":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _TRACER = trace.get_tracer("agnos.gateway")


def tracer():
    global _TRACER
    if _TRACER is None:
        setup_tracing()
    return _TRACER


def instrument_fastapi(app) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception:  # noqa: BLE001
        pass
