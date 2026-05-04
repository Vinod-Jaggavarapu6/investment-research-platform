import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from ..state import AgentState
from ..tools.retrieval import ticker_has_data, ticker_has_recent_data
from ..rag.background_ingest import is_ingesting, trigger_ingest

logger = structlog.get_logger(__name__)

# Routes that require SEC filing chunks to be present in the DB.
# For all other routes the preflight check is skipped entirely.
FILINGS_ROUTES = {"filings", "filings_recent", "both", "comprehensive"}


def make_data_preflight_node(db: AsyncSession):
    async def data_preflight_node(state: AgentState) -> dict:
        try:
            if state.get("final_answer"):
                logger.info("preflight.skipped", reason="cache_hit")
                return {}

            route  = state.get("route", "comprehensive")
            ticker = (state.get("ticker") or "").upper()

            if route not in FILINGS_ROUTES or not ticker:
                logger.info("preflight.passthrough", route=route, ticker=ticker, reason="no_filings_needed")
                return {}

            # filings_recent needs 10-Q/8-K chunks specifically — a ticker
            # indexed only via a prior "filings" (10-K) run would pass the
            # generic check but return empty results at retrieval time.
            if route == "filings_recent":
                has_data = await ticker_has_recent_data(ticker, db)
            else:
                has_data = await ticker_has_data(ticker, db)

            logger.info("preflight.checked", ticker=ticker, route=route, has_data=has_data, is_ingesting=is_ingesting(ticker))

            if has_data:
                return {}

            already_ingesting = is_ingesting(ticker)
            if not already_ingesting:
                trigger_ingest(ticker)
                logger.info("preflight.ingest_triggered", ticker=ticker)
            else:
                logger.info("preflight.ingest_already_running", ticker=ticker)

            return {"ingest_pending": True, "skip_cache": True}

        except Exception:
            logger.exception("preflight.error")
            return {}

    return data_preflight_node
