from __future__ import annotations

import time
import uuid

import sentry_sdk
import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware:
    """Pure ASGI middleware — runs in the same coroutine as the request.

    BaseHTTPMiddleware spawns a background task for the response body, which
    breaks contextvars propagation into SSE streams. This implementation avoids
    that by wrapping the ASGI callable directly.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = str(uuid.uuid4())
        start = time.perf_counter()

        method = scope.get("method", "")
        path = scope.get("path", "")
        client = scope.get("client")
        client_ip = client[0] if client else None
        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        user_agent = next(
            (v.decode("utf-8", errors="ignore") for k, v in raw_headers if k == b"user-agent"),
            "",
        )

        # Track the response status so we can log it after streaming completes
        status_code = 500

        async def send_with_status(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        sentry_sdk.set_tag("correlation_id", correlation_id)

        # Bind per-request fields so every log line in this request carries them
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            method=method,
            path=path,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        logger.info("request.started")
        try:
            await self.app(scope, receive, send_with_status)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request.finished",
                status_code=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
