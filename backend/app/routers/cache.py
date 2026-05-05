import logging

from fastapi import APIRouter, HTTPException, Query

from app import state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Cache"])


@router.get("/cache/debug", summary="Inspect a cache entry")
async def cache_debug_get(
    ticker:   str = Query(..., description="Ticker symbol, e.g. AAPL"),
    question: str = Query(..., description="Research question"),
):
    from app.cache.cache_keys import full_report_key
    if not state.cache:
        raise HTTPException(status_code=503, detail="Cache not initialised")
    key = full_report_key(ticker.upper().strip(), question)
    exists = await state.cache.exists(key)
    value  = await state.cache.get(key) if exists else None
    return {"key": key, "exists": exists, "value": value}


@router.delete("/cache/debug", summary="Evict a cache entry")
async def cache_debug_delete(
    ticker:   str = Query(..., description="Ticker symbol"),
    question: str = Query(..., description="Research question"),
):
    from app.cache.cache_keys import full_report_key
    if not state.cache:
        raise HTTPException(status_code=503, detail="Cache not initialised")
    key = full_report_key(ticker.upper().strip(), question)
    ok  = await state.cache.delete(key)
    logger.info("cache EVICT key=%r ok=%s", key, ok)
    return {"key": key, "deleted": ok}


@router.get("/cache/health", summary="Redis ping")
async def cache_health():
    if not state.cache:
        raise HTTPException(status_code=503, detail="Cache not initialised")
    ok = await state.cache.health_check()
    return {"redis_ok": ok}
