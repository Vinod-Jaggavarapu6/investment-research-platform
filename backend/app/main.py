from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional
import uuid
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Path, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from sqlalchemy import select, delete as sa_delete, update as sa_update

from app.agents.financial_agent import analyze_ticker
from app.agents.filings_agent import answer_filing_question
from app.database import create_tables, get_db, get_checkpointer_url, Conversation, Message


from app.models import (
    AnalysisRequest, AnalysisResponse,
    FilingsRequest,
    NewsRequest, NewsResponse,
    ResearchRequest, ResearchResponse,
    ConversationResponse, MessageResponse,
)

from app.streaming import research_stream
from app.tools.retrieval import retrieve_chunks, format_retrieval_response, ticker_has_data
from app.rag.background_ingest import is_ingesting, trigger_ingest
from app.cache.redis_client import ResearchCacheClient, RedisConfig
from app.clients import init_clients


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_checkpointer = None
_cache: ResearchCacheClient | None = None

# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _checkpointer, _cache

    logger.info("=== Investment Research Platform — Phase 3 starting ===")
    logger.info(f"ANTHROPIC_API_KEY set: {bool(os.getenv('ANTHROPIC_API_KEY'))}")
    logger.info(f"OPENAI_API_KEY set:    {bool(os.getenv('OPENAI_API_KEY'))}")

    init_clients()
    logger.info("LLM clients initialized")

    await create_tables()

    # RedisConfig reads REDIS_HOST, REDIS_PORT automatically from environment
    # because of env_prefix = "REDIS_" in its BaseSettings config.
    # No arguments needed — it picks them up from docker-compose environment block.
    _cache = ResearchCacheClient(RedisConfig())
    redis_ok = await _cache.health_check()
    logger.info(f"Redis connected: {redis_ok}")


    # Hold the checkpointer connection open for the entire app lifetime
    async with AsyncPostgresSaver.from_conn_string(get_checkpointer_url()) as checkpointer:
        await checkpointer.setup()
        _checkpointer = checkpointer
        logger.info("LangGraph Postgres checkpointer initialized")

        yield   # ← app runs here, checkpointer stays open

    await _cache.close()

    logger.info("=== Shutting down ===")


# ─────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# HELPERS 
# ─────────────────────────────────────────────

async def _run_analysis(ticker: str, include_raw: bool = False) -> AnalysisResponse:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, analyze_ticker, ticker, include_raw)


def _raise_if_failed(result: AnalysisResponse) -> None:
    if not result.success:
        error_lower = (result.error or "").lower()
        if any(w in error_lower for w in ("invalid", "no price data", "not found")):
            raise HTTPException(status_code=422, detail=result.error)
        raise HTTPException(status_code=503, detail=result.error)


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

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


@app.get(
    "/analyze/{ticker}",
    response_model=AnalysisResponse,
    tags=["Analysis"],
    summary="Analyze a ticker (GET)",
)
async def analyze_get(
    ticker: str = Path(..., description="Stock ticker symbol", examples=["AAPL"]),
    include_raw: bool = Query(False, description="Include raw data for debugging"),
):
    ticker = ticker.upper().strip()
    result = await _run_analysis(ticker, include_raw)
    _raise_if_failed(result)
    return result


@app.post(
    "/analyze",
    response_model=AnalysisResponse,
    tags=["Analysis"],
    summary="Analyze a ticker (POST)",
)
async def analyze_post(request: AnalysisRequest):
    ticker = request.ticker.upper().strip()
    result = await _run_analysis(ticker, request.include_raw_data)
    _raise_if_failed(result)
    return result


@app.post(
    "/analyze/batch",
    response_model=list[AnalysisResponse],
    tags=["Analysis"],
    summary="Analyze multiple tickers",
)
async def analyze_batch(
    tickers: list[str],
    include_raw: bool = Query(False),
):
    if not tickers:
        raise HTTPException(status_code=422, detail="tickers list cannot be empty")
    if len(tickers) > 10:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum 10 tickers per batch — got {len(tickers)}"
        )

    results = []
    for ticker in tickers:
        ticker = ticker.upper().strip()
        result = await _run_analysis(ticker, include_raw)
        results.append(result)
        logger.info(
            f"[batch] {ticker} done — "
            f"{'✓' if result.success else '✗'} "
            f"{result.snapshot.signal.value if result.snapshot else result.error}"
        )
    return results


@app.get(
    "/retrieve",
    tags=["RAG"],
    summary="Retrieve relevant SEC filing chunks",
    description=(
        "Embeds the query, searches the FAISS index, and returns the "
        "most relevant 10-K chunks from PostgreSQL. "
        "Optionally filter by ticker."
    ),
)
async def retrieve(
    query:  str           = Query(..., description="Question to search for"),
    ticker: Optional[str] = Query(None, description="Filter by ticker e.g. AAPL"),
    k:      int           = Query(5, description="Number of results", ge=1, le=20),
    db:     AsyncSession  = Depends(get_db),
):
    """
    GET /retrieve?query=what+are+the+main+risk+factors&k=3
    GET /retrieve?query=revenue+growth&ticker=AAPL&k=5
    """
    chunks = await retrieve_chunks(query=query, db=db, ticker=ticker, k=k)
    return format_retrieval_response(chunks)

@app.post(
    "/filings/ask",
    tags=["RAG"],
    summary="Ask a question about SEC filings",
    description=(
        "Retrieves relevant 10-K chunks and generates a grounded "
        "answer with citations. Optionally filter by ticker."
    ),
)
async def ask_filings(
    request: FilingsRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    POST /filings/ask
    {
      "question": "What was Apple's gross margin in 2025?",
      "ticker": "AAPL",
      "k": 5
    }
    """
    result = await answer_filing_question(
        question=request.question,
        db=db,
        ticker=request.ticker,
        k=request.k,
    )
    return {
        "question": result.question,
        "ticker":   result.ticker,
        "answer":   result.answer,
        "model":    result.model,
        "sources": [
            {
                "rank":    i + 1,
                "ticker":  s["ticker"],
                "year":    s["year"],
                "section": s["section"],
                "score":   s["score"],
            }
            for i, s in enumerate(result.sources)
        ],
    }


@app.post(
    "/research",
    response_model=ResearchResponse,
    tags=["Research"],
    summary="Multi-agent research pipeline",
    description=(
        "Routes the question through a LangGraph pipeline — "
        "market data agent (yfinance), filings agent (FAISS + Claude), "
        "and a synthesis layer producing a unified grounded answer."
    ),
)
async def research(
    req: ResearchRequest,        # ← was AgentState, must be ResearchRequest
    db: AsyncSession = Depends(get_db),
):
    
    from .graph import build_graph
    from .cache.cache_keys import full_report_key, CACHE_TTL
    from datetime import timedelta

    ticker = req.ticker.upper().strip() if hasattr(req, "ticker") and req.ticker else ""

    # ── Cache-aside ────────────────────────────────────────────────────
    cache_key = full_report_key(ticker, req.question)
    if _cache:
        cached = await _cache.get(cache_key)
        if cached:
            logger.info(f"Cache HIT: {cache_key}")
            return ResearchResponse(**cached)
    
    thread_id = req.thread_id or str(uuid.uuid4())
    config    = {"configurable": {"thread_id": thread_id}}

    graph  = build_graph(db=db, checkpointer=_checkpointer)
    result = await graph.ainvoke(
        {"question": req.question},
        config=config,
    )

    if not result.get("final_answer"):
        raise HTTPException(status_code=500, detail="Graph produced no answer")

    response= ResearchResponse(
        thread_id    = thread_id,
        route        = result.get("route", "unknown"),
        final_answer = result["final_answer"],
        citations    = result.get("citations") or [],
    )

     # ── Store in cache ─────────────────────────────────────────────────
    if _cache:
        await _cache.set(
            key=cache_key,
            value=response.model_dump(),
            ttl=timedelta(seconds=CACHE_TTL["full_report"]),
        )
        logger.info(f"Cache SET: {cache_key}")
    # ──────────────────────────────────────────────────────────────────

    return response


@app.post(
    "/news/sentiment",
    response_model=NewsResponse,
    tags=["News"],
    summary="Analyze news sentiment for a ticker",
)
async def news_sentiment(request: NewsRequest):
    from app.agents.news_agent import analyze_news_sentiment

    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        analyze_news_sentiment,
        request.ticker.upper().strip(),
        request.days,
    )

    if not result.success:
        raise HTTPException(status_code=503, detail=result.error)

    return result


### ══════════════════════════════════════════════════════
### Conversation endpoints
### ══════════════════════════════════════════════════════

@app.get("/conversations", tags=["Conversations"], response_model=list[ConversationResponse])
async def list_conversations(
    session_id: str = Query(..., description="Browser session UUID"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.session_id == session_id)
        .order_by(Conversation.updated_at.desc())
    )
    return [ConversationResponse.model_validate(c) for c in result.scalars().all()]


@app.post("/conversations", tags=["Conversations"], response_model=ConversationResponse, status_code=201)
async def create_conversation(
    session_id: str           = Query(...),
    ticker:     Optional[str] = Query(None),
    title:      Optional[str] = Query(None),
    db:         AsyncSession  = Depends(get_db),
):
    ticker_clean = ticker.upper() if ticker else None
    conv = Conversation(
        id         = str(uuid.uuid4()),
        session_id = session_id,
        title      = title or (f"{ticker_clean} — New Research" if ticker_clean else "New Research"),
        ticker     = ticker_clean,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return ConversationResponse.model_validate(conv)


@app.get("/conversations/{conversation_id}/messages", tags=["Conversations"])
async def get_conversation_messages(
    conversation_id: str         = Path(...),
    db:              AsyncSession = Depends(get_db),
):
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return {
        "conversation": ConversationResponse.model_validate(conv),
        "messages":     [MessageResponse.model_validate(m) for m in msgs_result.scalars().all()],
    }


@app.delete("/conversations/{conversation_id}", tags=["Conversations"], status_code=204)
async def delete_conversation(
    conversation_id: str         = Path(...),
    db:              AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.execute(sa_delete(Message).where(Message.conversation_id == conversation_id))
    await db.execute(sa_delete(Conversation).where(Conversation.id == conversation_id))
    await db.commit()


@app.get("/cache/debug", tags=["Cache"], summary="Inspect a cache entry")
async def cache_debug_get(
    ticker:   str = Query(..., description="Ticker symbol, e.g. AAPL"),
    question: str = Query(..., description="Research question"),
):
    from .cache.cache_keys import full_report_key
    if not _cache:
        raise HTTPException(status_code=503, detail="Cache not initialised")
    key = full_report_key(ticker.upper().strip(), question)
    exists = await _cache.exists(key)
    value  = await _cache.get(key) if exists else None
    return {
        "key":    key,
        "exists": exists,
        "value":  value,
    }


@app.delete("/cache/debug", tags=["Cache"], summary="Evict a cache entry")
async def cache_debug_delete(
    ticker:   str = Query(..., description="Ticker symbol"),
    question: str = Query(..., description="Research question"),
):
    from .cache.cache_keys import full_report_key
    if not _cache:
        raise HTTPException(status_code=503, detail="Cache not initialised")
    key = full_report_key(ticker.upper().strip(), question)
    ok  = await _cache.delete(key)
    logger.info("cache EVICT key=%r ok=%s", key, ok)
    return {"key": key, "deleted": ok}


@app.get("/ingest/status/{ticker}", tags=["Ingest"], summary="Check ingest status for a ticker")
async def ingest_status(
    ticker: str = Path(..., description="Stock ticker, e.g. SNOW"),
    db: AsyncSession = Depends(get_db),
):
    t = ticker.upper().strip()
    has_data = await ticker_has_data(t, db)
    if has_data:
        return {"ticker": t, "status": "ready"}
    if is_ingesting(t):
        return {"ticker": t, "status": "ingesting"}
    return {"ticker": t, "status": "not_found"}


@app.post("/ingest/trigger/{ticker}", tags=["Ingest"], summary="Manually trigger ingest for a ticker")
async def ingest_trigger(
    ticker: str = Path(..., description="Stock ticker, e.g. SNOW"),
    db: AsyncSession = Depends(get_db),
):
    t = ticker.upper().strip()
    if is_ingesting(t):
        return {"ticker": t, "status": "already_ingesting"}
    has_data = await ticker_has_data(t, db)
    if has_data:
        return {"ticker": t, "status": "already_ready"}
    trigger_ingest(t)
    return {"ticker": t, "status": "ingesting_started"}


@app.get("/cache/health", tags=["Cache"], summary="Redis ping")
async def cache_health():
    if not _cache:
        raise HTTPException(status_code=503, detail="Cache not initialised")
    ok = await _cache.health_check()
    return {"redis_ok": ok}


### ══════════════════════════════════════════════════════
### Add the SSE endpoint to app
### ══════════════════════════════════════════════════════
### This endpoint streams the same research pipeline as /research but emits real-time events for each node completion and LLM token. The frontend can listen to these events to update the UI progressively.
### The stream_research_graph function in streamer.py handles the orchestration of running the graph and yielding events as they happen.
### The frontend can connect to this endpoint using EventSource and update the UI with progress indicators, intermediate results, and a typewriter effect for the final answer.
 
@app.get(
    "/research/stream",
    tags=["Research"],
    summary="Multi-agent research — SSE streaming",
    description=(
        "Same pipeline as POST /research but streams progress events "
        "and LLM tokens in real-time over Server-Sent Events. "
        "Connect with EventSource in the browser or curl -N."
    ),
)
async def research_stream_endpoint(
    question:        str           = Query(..., description="Research question"),
    ticker:          str           = Query("", description="Stock ticker, e.g. AAPL (optional — extracted from question if omitted)"),
    conversation_id: Optional[str] = Query(None, description="Existing conversation ID to resume, or omit to start a new one"),
    session_id:      str           = Query("default", description="Browser session UUID"),
    request:         Request       = None,
    db:              AsyncSession  = Depends(get_db),
):
    """
    Streaming research endpoint using Server-Sent Events.

    Usage:
        GET /research/stream?ticker=AAPL&question=What+is+Apple%27s+revenue+trend

    Returns a stream of SSE events:
        {type: "node_start",    node: "router"}
        {type: "node_complete", node: "router",  data: {route, ticker}}
        {type: "token",         text: "..."}
        {type: "done",          report: {...}}
    """

    async def event_generator():
        if await request.is_disconnected():
            return

        async for sse_event in research_stream(
            question=question,
            ticker=ticker,
            db=db,
            cache=_cache,
            checkpointer=_checkpointer,
            conversation_id=conversation_id,
            session_id=session_id,
        ):
            if await request.is_disconnected():
                logger.info(
                    "Client disconnected mid-stream, stopping (ticker=%s)", ticker
                )
                break

            yield sse_event

    return EventSourceResponse(
        event_generator(),
        headers={
            "X-Accel-Buffering": "no",     
            "Cache-Control":     "no-cache",
        },
    )