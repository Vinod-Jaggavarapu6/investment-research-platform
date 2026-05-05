from __future__ import annotations

import logging
import os
import sys

import structlog


def _inject_otel_context(logger, method, event_dict: dict) -> dict:
    """Structlog processor: add trace_id + span_id from the active OTel span.

    Uses a lazy import so this file stays importable even before the OTel SDK
    is initialised (e.g. during unit tests that don't call setup_tracing()).
    When no span is active the fields are simply omitted.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        if span.is_recording():
            ctx = span.get_span_context()
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    except Exception:
        pass
    return event_dict


def configure_logging() -> None:
    """Configure structlog for the app.

    Dev  (APP_ENV != "production"): colored, human-readable console output.
    Prod (APP_ENV == "production"):  JSON lines to stdout — one object per log entry.

    All existing stdlib logging.getLogger() calls are routed through the same
    processor pipeline via ProcessorFormatter, so they get structured output
    automatically without any changes to call sites.
    """
    is_prod = os.getenv("APP_ENV", "development") == "production"
    # LOG_FORMAT=json forces JSON output even in dev — used when running inside Docker
    # so Promtail can parse the log lines as structured JSON.
    use_json = is_prod or os.getenv("LOG_FORMAT", "").lower() == "json"

    # Processors shared between structlog-native loggers and stdlib bridge
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _inject_otel_context,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Formatter that bridges stdlib log records through the structlog pipeline
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Reduce noise from chatty HTTP / low-level libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)
