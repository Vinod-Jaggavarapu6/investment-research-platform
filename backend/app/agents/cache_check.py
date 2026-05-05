from typing import Callable, Awaitable

import structlog

from ..cache.cache_keys import full_report_key
from ..metrics import cache_hit_total
from ..state import AgentState

logger = structlog.get_logger(__name__)


def make_cache_check_node(cache, on_token: Callable[[str], Awaitable[None]] | None):
    async def cache_check_node(state: AgentState) -> dict:
        try:
            ticker   = state.get("ticker") or ""
            question = state.get("question", "")
            if not cache or not ticker:
                return {}

            cached = await cache.get(full_report_key(ticker, question))
            if not cached:
                cache_hit_total.labels(result="miss").inc()
                logger.info("cache.miss", ticker=ticker)
                return {}

            final_answer = cached.get("final_answer", "")
            if on_token and final_answer:
                await on_token(final_answer)

            cache_hit_total.labels(result="hit").inc()
            logger.info("cache.hit", ticker=ticker, route=cached.get("route"))
            return {
                "final_answer": final_answer,
                "route":        cached.get("route", state.get("route", "comprehensive")),
                "citations":    cached.get("citations") or [],
            }
        except Exception:
            logger.exception("cache_check.error")
            return {}

    return cache_check_node
