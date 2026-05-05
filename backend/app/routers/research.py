import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import state
from app.database import get_db
from app.models import ResearchRequest, ResearchResponse
from app.streaming import research_stream
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Research"])


@router.post(
    "/research",
    response_model=ResearchResponse,
    summary="Multi-agent research pipeline",
    description=(
        "Routes the question through a LangGraph pipeline — "
        "market data agent (yfinance), filings agent (FAISS + Claude), "
        "and a synthesis layer producing a unified grounded answer."
    ),
)
async def research(
    req: ResearchRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.graph import build_graph
    from app.cache.cache_keys import full_report_key, CACHE_TTL
    from datetime import timedelta

    cache_key = full_report_key("", req.question)
    if state.cache:
        cached = await state.cache.get(cache_key)
        if cached:
            logger.info(f"Cache HIT: {cache_key}")
            return ResearchResponse(**cached)

    thread_id = req.thread_id or str(uuid.uuid4())
    config    = {"configurable": {"thread_id": thread_id}}

    graph  = build_graph(db=db, checkpointer=state.checkpointer)
    result = await graph.ainvoke(
        {"question": req.question},
        config=config,
    )

    if not result.get("final_answer"):
        raise HTTPException(status_code=500, detail="Graph produced no answer")

    response = ResearchResponse(
        thread_id    = thread_id,
        route        = result.get("route", "unknown"),
        final_answer = result["final_answer"],
        citations    = result.get("citations") or [],
    )

    if state.cache:
        await state.cache.set(
            key=cache_key,
            value=response.model_dump(),
            ttl=timedelta(seconds=CACHE_TTL["full_report"]),
        )
        logger.info(f"Cache SET: {cache_key}")

    return response


@router.get(
    "/research/stream",
    summary="Multi-agent research — SSE streaming",
    description=(
        "Same pipeline as POST /research but streams progress events "
        "and LLM tokens in real-time over Server-Sent Events. "
        "Connect with EventSource in the browser or curl -N."
    ),
)
async def research_stream_endpoint(
    question:        str           = Query(..., description="Research question"),
    conversation_id: Optional[str] = Query(None, description="Existing conversation ID to resume, or omit to start a new one"),
    session_id:      str           = Query("default", description="Browser session UUID"),
    request:         Request       = None,
    db:              AsyncSession  = Depends(get_db),
):
    async def event_generator():
        if await request.is_disconnected():
            return

        async for sse_event in research_stream(
            question=question,
            db=db,
            cache=state.cache,
            checkpointer=state.checkpointer,
            conversation_id=conversation_id,
            session_id=session_id,
        ):
            if await request.is_disconnected():
                logger.info("Client disconnected mid-stream")
                break

            yield sse_event

    return EventSourceResponse(
        event_generator(),
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control":     "no-cache",
        },
    )
