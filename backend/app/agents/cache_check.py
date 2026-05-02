import logging
from typing import Callable, Awaitable

from ..cache.cache_keys import full_report_key
from ..state import AgentState

logger = logging.getLogger(__name__)


def make_cache_check_node(cache, on_token: Callable[[str], Awaitable[None]] | None):
    async def cache_check_node(state: AgentState) -> dict:
        try:
            ticker   = (state.get("ticker") or "").upper()
            question = state.get("question", "")
            if not cache or not ticker:
                return {}

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
        except Exception:
            logger.exception("[cache_check] error reading cache — passing through")
            return {}

    return cache_check_node
