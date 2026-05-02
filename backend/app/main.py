from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app import state
from app.cache.redis_client import ResearchCacheClient, RedisConfig
from app.clients import init_clients
from app.database import create_tables, get_checkpointer_url
from app.routers import analysis, cache, conversations, ingest, news, rag, research


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Investment Research Platform — Phase 3 starting ===")
    logger.info(f"ANTHROPIC_API_KEY set: {bool(os.getenv('ANTHROPIC_API_KEY'))}")
    logger.info(f"OPENAI_API_KEY set:    {bool(os.getenv('OPENAI_API_KEY'))}")

    init_clients()
    logger.info("LLM clients initialized")

    await create_tables()

    state.cache = ResearchCacheClient(RedisConfig())
    redis_ok = await state.cache.health_check()
    logger.info(f"Redis connected: {redis_ok}")

    async with AsyncPostgresSaver.from_conn_string(get_checkpointer_url()) as checkpointer:
        await checkpointer.setup()
        state.checkpointer = checkpointer
        logger.info("LangGraph Postgres checkpointer initialized")

        yield

    await state.cache.close()
    logger.info("=== Shutting down ===")


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


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["Infrastructure"])
async def health():
    return {
        "status": "healthy",
        "service": "investment-research-platform",
        "phase": 2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


app.include_router(analysis.router)
app.include_router(rag.router)
app.include_router(research.router)
app.include_router(news.router)
app.include_router(conversations.router)
app.include_router(ingest.router)
app.include_router(cache.router)
