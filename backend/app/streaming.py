import asyncio
import json
import logging
from typing import AsyncGenerator, Any

from app.graph import build_graph
from app.state import AgentState

logger = logging.getLogger(__name__)

TRACKED_NODES = {"router", "market_agent", "filings_agent", "news_agent", "synthesizer"}


# ── SSE event constructors ─────────────────────────────────────────────────────

def _event(type_: str, **kwargs) -> dict:
    return {"data": json.dumps({"type": type_, **kwargs})}

def node_start_event(node: str) -> dict:
    return _event("node_start", node=node)

def node_complete_event(node: str, data: Any) -> dict:
    return _event("node_complete", node=node, data=data)

def token_event(text: str) -> dict:
    return _event("token", text=text)

def error_event(message: str) -> dict:
    return _event("error", message=message)

def done_event(report: Any) -> dict:
    return _event("done", report=report)


def _extract_node_output(node: str, output: Any) -> dict:
    if not isinstance(output, dict):
        return {"status": "complete"}
    if node == "router":
        return {"route": output.get("route"), "ticker": output.get("ticker")}
    elif node == "filings_agent":
        return {
            "has_data":      output.get("filings_output") is not None,
            "has_citations": bool(output.get("citations")),
        }
    elif node in ("market_agent", "news_agent", "synthesizer"):
        return {"status": "complete"}
    return {}


async def research_stream(
    question: str,
    ticker: str,
    db,
) -> AsyncGenerator[dict, None]:

    token_queue: asyncio.Queue = asyncio.Queue()
    graph = build_graph(db=db, token_queue=token_queue)

    initial_state: AgentState = {
        "question": question,
        "ticker":   ticker.upper() if ticker else "",
    }
    thread_id = f"stream-{ticker.upper() if ticker else 'auto'}-{id(question)}"
    config    = {"configurable": {"thread_id": thread_id}}

    nodes_started:   set[str] = set()
    nodes_completed: set[str] = set()
    final_answer: str | None  = None

    # Collect token events here; we flush them into the SSE stream
    # between graph events — avoids blocking the astream_events loop
    pending_tokens: list[dict] = []

    async def drain_tokens():
        """Non-blocking drain — moves whatever is in the queue into pending_tokens."""
        while not token_queue.empty():
            try:
                tok = token_queue.get_nowait()
                pending_tokens.append(token_event(tok))
            except asyncio.QueueEmpty:
                break

    try:
        async for event in graph.astream_events(
            initial_state,
            version="v2",
            config=config,
        ):
            # Flush any tokens that arrived since last event
            await drain_tokens()
            for tok_event in pending_tokens:
                yield tok_event
            pending_tokens.clear()

            event_type = event["event"]
            node       = event.get("metadata", {}).get("langgraph_node", "")

            if event_type == "on_chain_start" and node in TRACKED_NODES:
                if node not in nodes_started:
                    nodes_started.add(node)
                    yield node_start_event(node)

            elif event_type == "on_chain_end" and node in TRACKED_NODES:
                if node not in nodes_completed:
                    nodes_completed.add(node)

                    # Final drain before node_complete
                    await drain_tokens()
                    for tok_event in pending_tokens:
                        yield tok_event
                    pending_tokens.clear()

                    output       = event.get("data", {}).get("output") or {}
                    node_summary = _extract_node_output(node, output)
                    yield node_complete_event(node, node_summary)

                    if node == "synthesizer" and isinstance(output, dict):
                        final_answer = output.get("final_answer")

        # Final drain after graph completes
        await drain_tokens()
        for tok_event in pending_tokens:
            yield tok_event
        pending_tokens.clear()

        yield done_event(final_answer)

    except asyncio.CancelledError:
        logger.info("SSE stream cancelled — client disconnected (ticker=%s)", ticker)
    except Exception as exc:
        logger.exception("Error in research_stream (ticker=%s)", ticker)
        yield error_event(str(exc))