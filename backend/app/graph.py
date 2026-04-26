import logging

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Callable, Awaitable

from .state import AgentState

logger = logging.getLogger(__name__)
from .agents.router_agent import router_node
from .agents.financial_agent import make_market_node
from .agents.filings_agent import make_filings_node
from .agents.synthesizer import make_synthesizer_node
from .agents.news_agent import make_news_node


def make_cache_check_node(cache, on_token):
    async def cache_check_node(state: AgentState) -> dict:
        ticker   = (state.get("ticker") or "").upper()
        question = state.get("question", "")
        if not cache or not ticker:
            return {}

        from .cache.cache_keys import full_report_key
        cached = await cache.get(full_report_key(ticker, question))
        if not cached:
            return {}

        final_answer = cached.get("final_answer", "")
        if on_token and final_answer:
            await on_token(final_answer)

        return {
            "final_answer": final_answer,
            "route":        cached.get("route", state.get("route", "comprehensive")),
            "citations":    cached.get("citations") or [],
        }
    return cache_check_node


FILINGS_ROUTES = {"filings", "filings_recent", "both", "comprehensive"}

def make_data_preflight_node(db: AsyncSession):
    async def data_preflight_node(state: AgentState) -> dict:
        if state.get("final_answer"):
            logger.info("[preflight] cache hit detected — skipping preflight")
            return {}

        route  = state.get("route", "comprehensive")
        ticker = (state.get("ticker") or "").upper()

        if route not in FILINGS_ROUTES or not ticker:
            logger.info("[preflight] route=%r ticker=%r — no filings needed, passthrough", route, ticker)
            return {}

        from .tools.retrieval import ticker_has_data, ticker_has_recent_data
        from .rag.background_ingest import is_ingesting, trigger_ingest

        # For filings_recent we need 10-Q/8-K chunks, not just any chunks.
        # A ticker indexed only via an earlier "filings" (10-K) route would
        # pass the generic check but return empty results at retrieval time.
        if route == "filings_recent":
            has_data = await ticker_has_recent_data(ticker, db)
        else:
            has_data = await ticker_has_data(ticker, db)

        logger.info("[preflight] ticker=%r route=%r has_data=%s is_ingesting=%s",
                    ticker, route, has_data, is_ingesting(ticker))

        if has_data:
            return {}

        already_ingesting = is_ingesting(ticker)
        if not already_ingesting:
            trigger_ingest(ticker)
            logger.info("[preflight] ingest triggered for %r — short-circuiting graph", ticker)
        else:
            logger.info("[preflight] ingest already running for %r — short-circuiting graph", ticker)

        return {"ingest_pending": True, "skip_cache": True}

    return data_preflight_node


def pick_agents_for_route(state: AgentState) -> list[str]:
    if state.get("final_answer") or state.get("ingest_pending"):
        return [END]
    route = state.get("route", "both")
    if route == "market":                  return ["market_agent"]
    elif route in ("filings",
                   "filings_recent"):      return ["filings_agent"]
    elif route == "news":                  return ["news_agent"]
    elif route == "both":                  return ["market_agent", "filings_agent"]
    else:                                  return ["market_agent", "filings_agent", "news_agent"]


def build_graph(
    db:          AsyncSession,
    checkpointer=None,
    on_token:    Callable[[str], Awaitable[None]] | None = None,
    cache=None,
):
    g = StateGraph(AgentState)

    g.add_node("router",          router_node)
    g.add_node("cache_check",     make_cache_check_node(cache, on_token))
    g.add_node("data_preflight",  make_data_preflight_node(db))
    g.add_node("market_agent",    make_market_node())
    g.add_node("filings_agent",   make_filings_node(db))
    g.add_node("news_agent",      make_news_node())
    g.add_node("synthesizer",     make_synthesizer_node(on_token))

    g.set_entry_point("router")

    g.add_edge("router",       "cache_check")
    g.add_edge("cache_check",  "data_preflight")
    g.add_conditional_edges(
        "data_preflight",
        pick_agents_for_route,
        ["market_agent", "filings_agent", "news_agent", END],
    )
    g.add_edge("market_agent",  "synthesizer")
    g.add_edge("filings_agent", "synthesizer")
    g.add_edge("news_agent",    "synthesizer")
    g.add_edge("synthesizer",   END)

    return g.compile(checkpointer=checkpointer or MemorySaver())