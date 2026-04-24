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

from app.agents.financial_agent import analyze_ticker
from app.agents.filings_agent import answer_filing_question
from app.database import create_tables, get_db, get_checkpointer_url
from app.models import (
    AnalysisRequest, AnalysisResponse,
    FilingsRequest,
    NewsRequest, NewsResponse,
    ResearchRequest, ResearchResponse,
)
from app.streaming import research_stream
from app.tools.retrieval import init_retrieval, retrieve_chunks, format_retrieval_response


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_checkpointer = None

# ─────────────────────────────────────────────
# LIFESPAN
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _checkpointer

    logger.info("=== Investment Research Platform — Phase 3 starting ===")
    logger.info(f"ANTHROPIC_API_KEY set: {bool(os.getenv('ANTHROPIC_API_KEY'))}")
    logger.info(f"OPENAI_API_KEY set:    {bool(os.getenv('OPENAI_API_KEY'))}")

    await create_tables()
    init_retrieval()

    # Hold the checkpointer connection open for the entire app lifetime
    async with AsyncPostgresSaver.from_conn_string(get_checkpointer_url()) as checkpointer:
        await checkpointer.setup()
        _checkpointer = checkpointer
        logger.info("LangGraph Postgres checkpointer initialized")

        yield   # ← app runs here, checkpointer stays open

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


# @app.post(
#     "/research",
#     response_model=ResearchResponse,
#     tags=["Research"],
#     summary="Multi-agent research pipeline",
#     description=(
#         "Routes the question through a LangGraph pipeline — "
#         "market data agent (yfinance), filings agent (FAISS + Claude), "
#         "and a synthesis layer producing a unified grounded answer."
#     ),
# )
# async def research(
#     req: AgentState,
#     db: AsyncSession = Depends(get_db),
# ):
#     from .graph import build_graph          # local import avoids circular deps

#     thread_id = req.thread_id or str(uuid.uuid4())
#     config    = {"configurable": {"thread_id": thread_id}}

#     graph  = build_graph(db=db, checkpointer=_checkpointer)
#     result = await graph.ainvoke(
#         {"question": req.question},
#         config=config,
#     )

#     if not result.get("final_answer"):
#         raise HTTPException(status_code=500, detail="Graph produced no answer")

#     return ResearchResponse(
#         thread_id    = thread_id,
#         route        = result.get("route", "unknown"),
#         final_answer = result["final_answer"],
#         citations    = result.get("citations") or [],
#     )

# main.py — fix the /research endpoint signature

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
    
    thread_id = req.thread_id or str(uuid.uuid4())
    config    = {"configurable": {"thread_id": thread_id}}

    graph  = build_graph(db=db, checkpointer=_checkpointer)
    result = await graph.ainvoke(
        {"question": req.question},
        config=config,
    )

    if not result.get("final_answer"):
        raise HTTPException(status_code=500, detail="Graph produced no answer")

    return ResearchResponse(
        thread_id    = thread_id,
        route        = result.get("route", "unknown"),
        final_answer = result["final_answer"],
        citations    = result.get("citations") or [],
    )


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
    question: str = Query(..., description="Research question"),
    ticker: str = Query("", description="Stock ticker, e.g. AAPL (optional — extracted from question if omitted)"),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
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
            db=db,                  # ← was db_pool, matches streaming.py param name
        ):
            if await request.is_disconnected():
                logger.info(
                    "Client disconnected mid-stream, stopping (ticker=%s)", ticker
                )
                break

            yield sse_event

    return EventSourceResponse(event_generator())