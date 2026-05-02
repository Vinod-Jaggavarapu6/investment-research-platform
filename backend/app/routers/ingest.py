from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.rag.background_ingest import is_ingesting, trigger_ingest
from app.tools.retrieval import ticker_has_data

router = APIRouter(tags=["Ingest"])


@router.get("/ingest/status/{ticker}", summary="Check ingest status for a ticker")
async def ingest_status(
    ticker: str = Path(..., description="Stock ticker, e.g. SNOW"),
    db: AsyncSession = Depends(get_db),
):
    t = ticker.upper().strip()
    has_data = await ticker_has_data(t, db)
    if has_data:
        return {"ticker": t, "status": "ready"}
    if is_ingesting(t):
        return {"ticker": t, "status": "ingesting"}
    return {"ticker": t, "status": "not_found"}


@router.post("/ingest/trigger/{ticker}", summary="Manually trigger ingest for a ticker")
async def ingest_trigger(
    ticker: str = Path(..., description="Stock ticker, e.g. SNOW"),
    db: AsyncSession = Depends(get_db),
):
    t = ticker.upper().strip()
    if is_ingesting(t):
        return {"ticker": t, "status": "already_ingesting"}
    has_data = await ticker_has_data(t, db)
    if has_data:
        return {"ticker": t, "status": "already_ready"}
    trigger_ingest(t)
    return {"ticker": t, "status": "ingesting_started"}
