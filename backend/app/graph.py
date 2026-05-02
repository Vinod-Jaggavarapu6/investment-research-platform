import logging
from typing import Callable, Awaitable

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from .state import AgentState
from .agents.router_agent import router_node
from .agents.cache_check import make_cache_check_node
from .agents.preflight import make_data_preflight_node
from .agents.financial_agent import make_market_node
from .agents.filings_agent import make_filings_node
from .agents.news_agent import make_news_node
from .agents.synthesizer import make_synthesizer_node
from .agents.compare_agent import make_compare_node

logger = logging.getLogger(__name__)


def pick_agents_for_route(state: AgentState) -> list[str]:
    if state.get("final_answer") or state.get("ingest_pending"):
        return [END]
    route = state.get("route", "both")
    if route == "compare":                return ["compare_agent"]
    elif route == "market":               return ["market_agent"]
    elif route in ("filings",
                   "filings_recent"):     return ["filings_agent"]
    elif route == "news":                 return ["news_agent"]
    elif route == "both":                 return ["market_agent", "filings_agent"]
    else:                                 return ["market_agent", "filings_agent", "news_agent"]


def build_graph(
    db:          AsyncSession,
    checkpointer = None,
    on_token:    Callable[[str], Awaitable[None]] | None = None,
    cache        = None,
):
    g = StateGraph(AgentState)

    g.add_node("router",         router_node)
    g.add_node("cache_check",    make_cache_check_node(cache, on_token))
    g.add_node("data_preflight", make_data_preflight_node(db))
    g.add_node("market_agent",   make_market_node())
    g.add_node("filings_agent",  make_filings_node(db))
    g.add_node("news_agent",     make_news_node())
    g.add_node("synthesizer",    make_synthesizer_node(on_token))
    g.add_node("compare_agent",  make_compare_node(db))

    g.set_entry_point("router")

    g.add_edge("router",       "cache_check")
    g.add_edge("cache_check",  "data_preflight")
    g.add_conditional_edges(
        "data_preflight",
        pick_agents_for_route,
        ["market_agent", "filings_agent", "news_agent", "compare_agent", END],
    )
    g.add_edge("market_agent",  "synthesizer")
    g.add_edge("filings_agent", "synthesizer")
    g.add_edge("news_agent",    "synthesizer")
    g.add_edge("synthesizer",   END)
    g.add_edge("compare_agent", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())
