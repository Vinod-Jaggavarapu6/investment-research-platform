import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator, Any

import structlog

from app.graph import build_graph
from app.metrics import agent_duration_seconds, research_requests_total
from app.state import AgentState
from app.cache.cache_keys import full_report_key, CACHE_TTL
from app.sse_types import (
    CitationOut,
    NodeStartEvent,
    NodeCompleteEvent,
    NodeErrorEvent,
    TokenEvent,
    DoneEvent,
    ConversationReadyEvent,
    ErrorEvent,
)

logger = structlog.get_logger(__name__)

# Nodes exposed via SSE — excludes internal nodes (data_preflight, cache_check)
TRACKED_NODES = {"router", "market_agent", "filings_agent", "news_agent", "synthesizer", "compare_agent"}
PREFLIGHT_NODE = "data_preflight"


def _sse(model) -> dict:
    return {"data": model.model_dump_json()}

def node_start_event(node: str) -> dict:
    return _sse(NodeStartEvent(node=node))

def node_complete_event(node: str, data: Any) -> dict:
    return _sse(NodeCompleteEvent(node=node, data=data))

def node_error_event(node: str, reason: str) -> dict:
    return _sse(NodeErrorEvent(node=node, reason=reason))

def token_event(text: str) -> dict:
    return _sse(TokenEvent(text=text))

def error_event(message: str) -> dict:
    return _sse(ErrorEvent(message=message))

def done_event(
    report: Any,
    ingesting_ticker: str | None = None,
    citations: list | None = None,
    conversation_id: str | None = None,
) -> dict:
    validated_citations = []
    for c in (citations or []):
        try:
            validated_citations.append(CitationOut.model_validate(c))
        except Exception:
            logger.warning("[stream] citation failed validation, skipping: %r", c)
    return _sse(DoneEvent(
        report=report,
        ingesting_ticker=ingesting_ticker,
        citations=validated_citations,
        conversation_id=conversation_id,
    ))

def conversation_ready_event(conversation_id: str) -> dict:
    return _sse(ConversationReadyEvent(conversation_id=conversation_id))


def _extract_node_output(node: str, output: Any) -> dict:
    if not isinstance(output, dict):
        return {"status": "complete"}
    if node == "router":
        return {
            "route":   output.get("route"),
            "ticker":  output.get("ticker"),
            "tickers": output.get("tickers"),
        }
    elif node == "filings_agent":
        return {
            "has_data":      output.get("filings_output") is not None,
            "has_citations": bool(output.get("citations")),
        }
    elif node == "compare_agent":
        return {
            "tickers":       output.get("tickers"),
            "has_citations": bool(output.get("citations")),
        }
    return {"status": "complete"}


async def _persist_exchange(
    db,
    conversation_id: str,
    session_id: str,
    question: str,
    answer: str,
    ticker: str,
) -> None:
    from sqlalchemy import select, update as sa_update
    from .database import Conversation, Message

    now = datetime.now(timezone.utc)

    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = result.scalar_one_or_none()
    if conv is None:
        ticker_clean = ticker.upper() if ticker else None
        title = f"{ticker_clean} — {question[:60]}" if ticker_clean else question[:60]
        db.add(Conversation(
            id=conversation_id,
            session_id=session_id or "default",
            title=title,
            ticker=ticker_clean,
            created_at=now,
            updated_at=now,
        ))
    else:
        await db.execute(
            sa_update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=now)
        )

    db.add(Message(id=str(uuid.uuid4()), conversation_id=conversation_id, role="user",      content=question,  created_at=now))
    db.add(Message(id=str(uuid.uuid4()), conversation_id=conversation_id, role="assistant", content=answer))
    await db.commit()


async def _load_recent_messages(db, conversation_id: str, n: int = 6) -> list[dict]:
    """Return the last n messages in chronological order for synthesizer context."""
    from sqlalchemy import select
    from .database import Message

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(n)
    )
    msgs = result.scalars().all()
    return [{"role": m.role, "content": m.content} for m in reversed(msgs)]


async def _create_conversation(
    db,
    conversation_id: str,
    session_id: str,
    question: str,
    ticker: str,
) -> None:
    from sqlalchemy import select
    from .database import Conversation

    now = datetime.now(timezone.utc)
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    if result.scalar_one_or_none() is None:
        ticker_clean = ticker.upper() if ticker else None
        title = f"{ticker_clean} — {question[:60]}" if ticker_clean else question[:60]
        db.add(Conversation(
            id=conversation_id,
            session_id=session_id or "default",
            title=title,
            ticker=ticker_clean,
            created_at=now,
            updated_at=now,
        ))
        await db.commit()


async def research_stream(
    question:        str,
    ticker:          str,
    db,
    cache=None,
    checkpointer=None,
    conversation_id: str | None = None,
    session_id:      str = "default",
) -> AsyncGenerator[dict, None]:

    derived_ticker: str = ticker.upper() if ticker else ""
    conversation_id = conversation_id or str(uuid.uuid4())
    logger.info(
        "stream.started",
        request_ticker=ticker,
        derived_ticker=derived_ticker,
        conversation_id=conversation_id,
    )

    token_queue: asyncio.Queue[str | None] = asyncio.Queue()
    assembled_answer: list[str] = []

    async def on_token(text: str) -> None:
        assembled_answer.append(text)
        await token_queue.put(text)

    graph = build_graph(db=db, on_token=on_token, cache=cache, checkpointer=checkpointer)

    # Only set ticker when explicitly provided — omitting it lets LangGraph
    # preserve the checkpoint value from the previous turn, which the router
    # then reads as prev_ticker for follow-up context.
    initial_state: AgentState = {"question": question}
    if derived_ticker:
        initial_state["ticker"] = derived_ticker

    # Load prior messages for follow-up context ("elaborate on X", "that figure you mentioned")
    if db and conversation_id:
        try:
            prior = await _load_recent_messages(db, conversation_id)
            if prior:
                initial_state["messages"] = prior
                logger.info("stream.prior_messages_loaded", count=len(prior), conversation_id=conversation_id)
        except Exception:
            logger.exception("stream.prior_messages_failed", conversation_id=conversation_id)
    # conversation_id = thread_id so LangGraph resumes graph state across turns
    thread_id = conversation_id
    config    = {"configurable": {"thread_id": thread_id}}

    nodes_started:            set[str] = set()
    nodes_completed:          set[str] = set()
    node_start_times:         dict[str, float] = {}
    final_answer:             str | None = None
    captured_route:           str = "comprehensive"
    captured_citations:       list = []
    captured_ingest_pending:  bool = False   # set from data_preflight state output

    sse_queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def run_graph():
        nonlocal final_answer, captured_route, derived_ticker, captured_citations, captured_ingest_pending
        try:
            async for event in graph.astream_events(
                initial_state, version="v2", config=config,
            ):
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")

                # Capture data_preflight output (not forwarded to frontend).
                # LangGraph fires two on_chain_end per node: one dict output and one list
                # routing result — isinstance guard avoids .get() crash on the list.
                if kind == "on_chain_end" and node == PREFLIGHT_NODE:
                    raw_output = event.get("data", {}).get("output")
                    output = raw_output if isinstance(raw_output, dict) else {}
                    if output.get("ingest_pending"):
                        captured_ingest_pending = True
                        logger.info("stream.preflight_ingest_pending", ticker=derived_ticker)
                    elif output:
                        logger.info("stream.preflight_passthrough", ticker=derived_ticker)

                if kind == "on_chain_start" and node in TRACKED_NODES:
                    if node not in nodes_started:
                        nodes_started.add(node)
                        node_start_times[node] = time.perf_counter()
                        logger.info("stream.node_start", node=node, ticker=derived_ticker)
                        await sse_queue.put(node_start_event(node))

                elif kind == "on_chain_end" and node in TRACKED_NODES:
                    raw_output = event.get("data", {}).get("output")

                    # Capture citations on every on_chain_end — nodes_completed dedups node
                    # completion but LangGraph fires two events per node, so citations could
                    # arrive in the second event which that guard skips.
                    if node in ("filings_agent", "compare_agent") and isinstance(raw_output, dict):
                        cits = raw_output.get("citations")
                        if cits:
                            captured_citations = cits

                    if node not in nodes_completed:
                        nodes_completed.add(node)
                        if node in node_start_times:
                            agent_duration_seconds.labels(
                                node_name=node,
                                route=captured_route,
                            ).observe(time.perf_counter() - node_start_times[node])
                        output       = raw_output if isinstance(raw_output, dict) else {}
                        node_failure = (output.get("agent_errors") or {}).get(node)
                        if node_failure:
                            logger.warning("stream.node_error", node=node, reason=node_failure, ticker=derived_ticker)
                            await sse_queue.put(node_error_event(node, node_failure))
                        else:
                            node_summary = _extract_node_output(node, output)
                            logger.info("stream.node_complete", node=node, ticker=derived_ticker)
                            await sse_queue.put(node_complete_event(node, node_summary))

                        if node in ("synthesizer", "compare_agent") and isinstance(output, dict):
                            final_answer = output.get("final_answer")

                        if node == "router" and isinstance(output, dict):
                            captured_route = output.get("route", "comprehensive")
                            router_ticker  = (output.get("ticker") or "").upper()
                            if router_ticker:
                                derived_ticker = router_ticker
                            logger.info("stream.router_done", route=captured_route, ticker=derived_ticker)

        except asyncio.CancelledError:
            # asyncio.shield inside LangChain raises this on outer task cancel — swallow for clean exit
            logger.info("stream.graph_cancelled", ticker=derived_ticker)

        finally:
            await sse_queue.put(None)

    async def forward_tokens():
        while True:
            token = await token_queue.get()
            if token is None:
                break
            await sse_queue.put(token_event(token))

    def _discard_task_exception(task: asyncio.Task) -> None:
        """Silences 'Task exception was never retrieved' warnings from asyncio."""
        try:
            task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError, Exception):
            pass

    try:
        if db:
            try:
                await _create_conversation(db, conversation_id, session_id, question, derived_ticker)
            except Exception:
                logger.exception("stream.create_conversation_failed", conversation_id=conversation_id)
        yield conversation_ready_event(conversation_id)

        graph_task = asyncio.create_task(run_graph())
        token_task = asyncio.create_task(forward_tokens())
        # Ensure result is retrieved even if stream is abandoned mid-way (GeneratorExit, etc.)
        graph_task.add_done_callback(_discard_task_exception)

        while True:
            item = await sse_queue.get()
            if item is None:
                break
            yield item

        await token_queue.put(None)
        await token_task

        while not sse_queue.empty():
            item = sse_queue.get_nowait()
            if item:
                yield item

        await graph_task

        if not final_answer and assembled_answer:
            final_answer = "".join(assembled_answer)

        cache_key      = full_report_key(derived_ticker, question) if derived_ticker else ""
        synthesizer_ran = "synthesizer" in nodes_completed or "compare_agent" in nodes_completed

        logger.info(
            "stream.complete",
            ticker=derived_ticker,
            route=captured_route,
            ingest_pending=captured_ingest_pending,
            synthesizer_ran=synthesizer_ran,
            answer_len=len(final_answer or ""),
            citations=len(captured_citations),
        )
        research_requests_total.labels(route=captured_route, status="completed").inc()

        if cache and final_answer and cache_key and synthesizer_ran and not captured_ingest_pending:
            ok = await cache.set(
                key=cache_key,
                value={
                    "final_answer": final_answer,
                    "route":        captured_route,
                    "citations":    captured_citations,
                },
                ttl=timedelta(seconds=CACHE_TTL["full_report"]),
            )
            logger.info("stream.cache_set", key=cache_key, ok=ok)
        elif captured_ingest_pending:
            logger.info("stream.cache_skip_ingest_pending", ticker=derived_ticker)

        ingesting_ticker = derived_ticker if captured_ingest_pending else None

        if final_answer and db:
            try:
                await _persist_exchange(
                    db=db,
                    conversation_id=conversation_id,
                    session_id=session_id,
                    question=question,
                    answer=final_answer,
                    ticker=derived_ticker,
                )
                logger.info("stream.exchange_persisted", conversation_id=conversation_id)
            except Exception:
                logger.exception("stream.exchange_persist_failed", conversation_id=conversation_id)

        logger.info("stream.done", ingesting_ticker=ingesting_ticker, citations=len(captured_citations))
        yield done_event(final_answer, ingesting_ticker=ingesting_ticker, citations=captured_citations, conversation_id=conversation_id)

    except asyncio.CancelledError:
        logger.info("stream.cancelled", ticker=ticker)
        research_requests_total.labels(route=captured_route, status="cancelled").inc()
        # Cancel token_task only — cancelling graph_task triggers "never retrieved"
        # tracebacks from LangGraph's internal tasks. Let it drain in the background.
        token_task.cancel()
        await asyncio.gather(token_task, return_exceptions=True)

    except Exception as exc:
        logger.exception("stream.error", ticker=ticker)
        research_requests_total.labels(route=captured_route, status="error").inc()
        yield error_event(str(exc))