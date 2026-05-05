from __future__ import annotations

import os

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = structlog.get_logger(__name__)


def setup_tracing(app=None) -> None:
    """Set up OpenTelemetry with OTLP/gRPC export to Jaeger.

    Must be called after configure_logging() so the trace-context structlog
    processor can read span context, and before the app handles requests so
    auto-instrumentation hooks are in place before any engines/clients are used.
    """
    otlp_endpoint = os.getenv("OTLP_ENDPOINT", "http://jaeger:4317")

    resource = Resource.create(
        {
            SERVICE_NAME: "investment-research-platform",
            "service.version": "0.3.0",
            "deployment.environment": os.getenv("APP_ENV", "development"),
        }
    )

    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(
        endpoint=otlp_endpoint,
        # Plain gRPC — no TLS for local dev. In prod, drop insecure and set
        # credentials via OTEL_EXPORTER_OTLP_CERTIFICATE env var instead.
        insecure=True,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Auto-instrument: one span per HTTP request (FastAPI), per DB query
    # (SQLAlchemy async), and per Redis command.
    if app is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="metrics,health",
        )

    # Instrument at the library level — hooks into any engine/connection-pool
    # created *after* this call, which covers our async SQLAlchemy setup.
    SQLAlchemyInstrumentor().instrument(enable_commenter=True, commenter_options={})
    RedisInstrumentor().instrument()

    logger.info("tracing.initialized", otlp_endpoint=otlp_endpoint)
