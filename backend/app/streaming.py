import asyncio
import json
import logging
from typing import AsyncGenerator, Any

from app.graph import build_graph
from app.state import AgentState
from app.cache.cache_keys import full_report_key, CACHE_TTL
from datetime import timedelta


logger = logging.getLogger(__name__)

# Nodes forwarded to the frontend as SSE node_start / node_complete events.
# data_preflight is intentionally excluded — it's an internal gate, not a
# user-visible step. We capture its output separately via PREFLIGHT_NODE.
TRACKED_NODES = {"router", "cache_check", "market_agent", "filings_agent", "news_agent", "synthesizer", "compare_agent"}
PREFLIGHT_NODE = "data_preflight"


def _event(type_: str, **kwargs) -> dict:
    return {"data": json.dumps({"type": type_, **kwargs})}

def node_start_event(node: str)              -> dict: return _event("node_start", node=node)
def node_complete_event(node: str, data: Any)-> dict: return _event("node_complete", node=node, data=data)
def token_event(text: str)                   -> dict: return _event("token", text=text)
def error_event(message: str)                -> dict: return _event("error", message=message)
def done_event(
    report: Any,
    ingesting_ticker: str | None = None,
    citations: list | None = None,
) -> dict:
    return _event("done", report=report, ingesting_ticker=ingesting_ticker, citations=citations or [])


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


async def research_stream(
    question:     str,
    ticker:       str,
    db,
    cache=None,
    checkpointer=None,
) -> AsyncGenerator[dict, None]:

    derived_ticker: str = ticker.upper() if ticker else ""
    logger.info("[stream] start  request_ticker=%r derived_ticker=%r question=%r",
                ticker, derived_ticker, question[:80])

    token_queue: asyncio.Queue[str | None] = asyncio.Queue()
    assembled_answer: list[str] = []

    async def on_token(text: str) -> None:
        assembled_answer.append(text)
        await token_queue.put(text)

    graph = build_graph(db=db, on_token=on_token, cache=cache, checkpointer=checkpointer)

    initial_state: AgentState = {
        "question": question,
        "ticker":   derived_ticker,
    }
    thread_id = f"stream-{derived_ticker or 'auto'}-{id(question)}"
    config    = {"configurable": {"thread_id": thread_id}}

    nodes_started:            set[str] = set()
    nodes_completed:          set[str] = set()
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

                # ── Capture data_preflight output (not sent to frontend) ──────
                # LangGraph fires multiple on_chain_end events with
                # langgraph_node="data_preflight": one for the node itself (dict)
                # and one for the conditional-edge routing result (list such as
                # ["market_agent"]).  Guard with isinstance so the routing-result
                # event doesn't crash when we call .get() on the list.
                if kind == "on_chain_end" and node == PREFLIGHT_NODE:
                    raw_output = event.get("data", {}).get("output")
                    output = raw_output if isinstance(raw_output, dict) else {}
                    if output.get("ingest_pending"):
                        captured_ingest_pending = True
                        logger.info(
                            "[stream] data_preflight: ingest_pending=True ticker=%r — "
                            "graph will exit early, no agents will run",
                            derived_ticker,
                        )
                    elif output:
                        logger.info(
                            "[stream] data_preflight: ticker=%r has data, proceeding to agents",
                            derived_ticker,
                        )

                # ── Frontend-visible node events ──────────────────────────────
                if kind == "on_chain_start" and node in TRACKED_NODES:
                    if node not in nodes_started:
                        nodes_started.add(node)
                        logger.info("[stream] node_start  node=%r ticker=%r", node, derived_ticker)
                        await sse_queue.put(node_start_event(node))

                elif kind == "on_chain_end" and node in TRACKED_NODES:
                    if node not in nodes_completed:
                        nodes_completed.add(node)
                        output       = event.get("data", {}).get("output") or {}
                        node_summary = _extract_node_output(node, output)
                        logger.info("[stream] node_complete node=%r ticker=%r summary=%r",
                                    node, derived_ticker, node_summary)
                        await sse_queue.put(node_complete_event(node, node_summary))

                        if node in ("synthesizer", "compare_agent") and isinstance(output, dict):
                            final_answer = output.get("final_answer")

                        if node == "router" and isinstance(output, dict):
                            captured_route = output.get("route", "comprehensive")
                            router_ticker  = (output.get("ticker") or "").upper()
                            if router_ticker:
                                derived_ticker = router_ticker
                            logger.info("[stream] router  route=%r ticker=%r",
                                        captured_route, derived_ticker)

                        if node in ("filings_agent", "compare_agent") and isinstance(output, dict):
                            captured_citations = output.get("citations") or []

        except asyncio.CancelledError:
            # Raised by asyncio.shield inside LangChain's on_chain_end callback when
            # the outer task is cancelled (e.g. client disconnects).  Absorb it here so
            # the task exits cleanly rather than surfacing as an unhandled exception.
            logger.info("[stream] run_graph cancelled ticker=%r", derived_ticker)

        finally:
            await sse_queue.put(None)

    async def forward_tokens():
        while True:
            token = await token_queue.get()
            if token is None:
                break
            await sse_queue.put(token_event(token))

    def _discard_task_exception(task: asyncio.Task) -> None:
        """Retrieve and silence a completed task's exception so it isn't logged
        as 'Task exception was never retrieved' by the asyncio machinery."""
        try:
            task.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError, Exception):
            pass

    try:
        graph_task = asyncio.create_task(run_graph())
        token_task = asyncio.create_task(forward_tokens())
        # Always attach the discard callback so the task result is retrieved
        # even if the stream is abandoned mid-way (GeneratorExit, etc.).
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
            "[stream] complete  ticker=%r route=%r ingest_pending=%s "
            "synthesizer_ran=%s answer_len=%d citations=%d",
            derived_ticker, captured_route, captured_ingest_pending,
            synthesizer_ran, len(final_answer or ""), len(captured_citations),
        )

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
            logger.info("[stream] cache SET  key=%r ok=%s", cache_key, ok)
        elif captured_ingest_pending:
            logger.info("[stream] cache SKIP — ingest pending for %r", derived_ticker)

        ingesting_ticker = derived_ticker if captured_ingest_pending else None
        logger.info("[stream] done event  ingesting_ticker=%r citations=%d",
                    ingesting_ticker, len(captured_citations))
        yield done_event(final_answer, ingesting_ticker=ingesting_ticker, citations=captured_citations)

    except asyncio.CancelledError:
        logger.info("[stream] cancelled — client disconnected ticker=%r", ticker)
        # Cancel the token forwarder only.  Do NOT cancel graph_task — abrupt
        # cancellation propagates into LangGraph's internal asyncio.create_task()
        # calls, leaving sub-tasks whose exceptions are never retrieved, which
        # Python logs as noisy "Task exception was never retrieved" tracebacks.
        # graph_task drains in the background; _discard_task_exception (attached
        # above) silently retrieves its final result/exception.
        token_task.cancel()
        await asyncio.gather(token_task, return_exceptions=True)

    except Exception as exc:
        logger.exception("[stream] error ticker=%r", ticker)
        yield error_event(str(exc))