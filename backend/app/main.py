from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import sentry_sdk
import structlog
from fastapi import FastAPI
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from prometheus_fastapi_instrumentator import Instrumentator

from app import state
from app.cache.redis_client import ResearchCacheClient, RedisConfig
from app.clients import init_clients
from app.database import create_tables, get_checkpointer_url
from app.logging_config import configure_logging
from app.middleware.request_logging import RequestLoggingMiddleware
from app.routers import analysis, cache, conversations, ingest, news, rag, research
from app.tracing import setup_tracing

_sentry_dsn = os.getenv("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        environment=os.getenv("ENVIRONMENT", "development"),
        send_default_pii=False,
    )

configure_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", phase=3, service="investment-research-platform")
    logger.info(
        "api_keys_present",
        anthropic=bool(os.getenv("ANTHROPIC_API_KEY")),
        openai=bool(os.getenv("OPENAI_API_KEY")),
    )

    init_clients()
    logger.info("llm_clients_initialized")

    await create_tables()

    state.cache = ResearchCacheClient(RedisConfig())
    redis_ok = await state.cache.health_check()
    logger.info("redis_connected", ok=redis_ok)

    async with AsyncPostgresSaver.from_conn_string(get_checkpointer_url()) as checkpointer:
        await checkpointer.setup()
        state.checkpointer = checkpointer
        logger.info("checkpointer_initialized", backend="postgres")

        yield

    await state.cache.close()
    logger.info("shutdown")


app = FastAPI(
    title="Investment Research Platform",
    description=(
        "Financial Analysis + SEC Filing RAG Pipeline. "
        "Fetches real market data via yfinance and retrieves "
        "relevant SEC 10-K sections using FAISS vector search."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

# Auto-instrument all HTTP routes and expose /metrics.
# Excludes /metrics and /health themselves to avoid self-referential noise.
Instrumentator(
    should_group_status_codes=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/metrics", "/health", "/"],
    inprogress_labels=True,
).instrument(app).expose(app, tags=["Infrastructure"])

# OTel tracing — wires FastAPI, SQLAlchemy, Redis auto-instrumentation and
# starts exporting spans to Tempo via OTLP/gRPC.
setup_tracing(app)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["Infrastructure"])
async def health():
    return {
        "status": "healthy",
        "service": "investment-research-platform",
        "phase": 3,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


app.include_router(analysis.router)
app.include_router(rag.router)
app.include_router(research.router)
app.include_router(news.router)
app.include_router(conversations.router)
app.include_router(ingest.router)
app.include_router(cache.router)
