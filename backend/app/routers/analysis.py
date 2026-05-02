import asyncio
import logging

from fastapi import APIRouter, HTTPException, Path, Query

from app.agents.financial_agent import analyze_ticker
from app.models import AnalysisRequest, AnalysisResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Analysis"])


async def _run_analysis(ticker: str, include_raw: bool = False) -> AnalysisResponse:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, analyze_ticker, ticker, include_raw)


def _raise_if_failed(result: AnalysisResponse) -> None:
    if not result.success:
        error_lower = (result.error or "").lower()
        if any(w in error_lower for w in ("invalid", "no price data", "not found")):
            raise HTTPException(status_code=422, detail=result.error)
        raise HTTPException(status_code=503, detail=result.error)


@router.get("/analyze/{ticker}", response_model=AnalysisResponse, summary="Analyze a ticker (GET)")
async def analyze_get(
    ticker: str = Path(..., description="Stock ticker symbol", examples=["AAPL"]),
    include_raw: bool = Query(False, description="Include raw data for debugging"),
):
    ticker = ticker.upper().strip()
    result = await _run_analysis(ticker, include_raw)
    _raise_if_failed(result)
    return result


@router.post("/analyze", response_model=AnalysisResponse, summary="Analyze a ticker (POST)")
async def analyze_post(request: AnalysisRequest):
    ticker = request.ticker.upper().strip()
    result = await _run_analysis(ticker, request.include_raw_data)
    _raise_if_failed(result)
    return result


@router.post("/analyze/batch", response_model=list[AnalysisResponse], summary="Analyze multiple tickers")
async def analyze_batch(
    tickers: list[str],
    include_raw: bool = Query(False),
):
    if not tickers:
        raise HTTPException(status_code=422, detail="tickers list cannot be empty")
    if len(tickers) > 10:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum 10 tickers per batch — got {len(tickers)}"
        )

    results = []
    for ticker in tickers:
        ticker = ticker.upper().strip()
        result = await _run_analysis(ticker, include_raw)
        results.append(result)
        logger.info(
            f"[batch] {ticker} done — "
            f"{'✓' if result.success else '✗'} "
            f"{result.snapshot.signal.value if result.snapshot else result.error}"
        )
    return results
