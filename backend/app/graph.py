from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Callable, Awaitable

from .state import AgentState
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


def pick_next_after_cache_check(state: AgentState) -> str:
    if state.get("final_answer"):
        return END
    route = state.get("route", "both")
    if route == "market":    return "market_agent"
    elif route == "filings": return "filings_agent"
    elif route == "news":    return "news_agent"
    else:                    return "market_agent"


def pick_next_after_market(state: AgentState) -> str:
    route = state.get("route")
    if route in ("both", "comprehensive"): return "filings_agent"
    return "synthesizer"


def pick_next_after_filings(state: AgentState) -> str:
    if state.get("route") == "comprehensive": return "news_agent"
    return "synthesizer"


def build_graph(
    db:          AsyncSession,
    checkpointer=None,
    on_token:    Callable[[str], Awaitable[None]] | None = None,
    cache=None,
):
    g = StateGraph(AgentState)

    g.add_node("router",        router_node)
    g.add_node("cache_check",   make_cache_check_node(cache, on_token))
    g.add_node("market_agent",  make_market_node())
    g.add_node("filings_agent", make_filings_node(db))
    g.add_node("news_agent",    make_news_node())
    g.add_node("synthesizer",   make_synthesizer_node(on_token))

    g.set_entry_point("router")

    g.add_edge("router", "cache_check")
    g.add_conditional_edges(
        "cache_check",
        pick_next_after_cache_check,
        {
            "market_agent":  "market_agent",
            "filings_agent": "filings_agent",
            "news_agent":    "news_agent",
            END:             END,
        },
    )
    g.add_conditional_edges(
        "market_agent",
        pick_next_after_market,
        {"filings_agent": "filings_agent", "synthesizer": "synthesizer"},
    )
    g.add_conditional_edges(
        "filings_agent",
        pick_next_after_filings,
        {"news_agent": "news_agent", "synthesizer": "synthesizer"},
    )
    g.add_edge("news_agent",  "synthesizer")
    g.add_edge("synthesizer", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())