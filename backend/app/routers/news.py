import asyncio

from fastapi import APIRouter, HTTPException

from app.models import NewsRequest, NewsResponse

router = APIRouter(tags=["News"])


@router.post(
    "/news/sentiment",
    response_model=NewsResponse,
    summary="Analyze news sentiment for a ticker",
)
async def news_sentiment(request: NewsRequest):
    from app.agents.news_agent import analyze_news_sentiment

    loop   = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        analyze_news_sentiment,
        request.ticker.upper().strip(),
        request.days,
    )

    if not result.success:
        raise HTTPException(status_code=503, detail=result.error)

    return result
