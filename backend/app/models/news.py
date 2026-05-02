from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class SentimentSignal(str, Enum):
    VERY_BULLISH = "VERY_BULLISH"
    BULLISH      = "BULLISH"
    NEUTRAL      = "NEUTRAL"
    BEARISH      = "BEARISH"
    VERY_BEARISH = "VERY_BEARISH"
    INSUFFICIENT = "INSUFFICIENT_DATA"


class ArticleSentiment(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL  = "NEUTRAL"
    MIXED    = "MIXED"


class NewsArticle(BaseModel):
    headline:      str
    source:        str
    url:           str
    published_at:  datetime
    summary:       str | None = None
    sentiment:     ArticleSentiment | None = None
    score:         float | None = None        # -1.0 to +1.0
    justification: str | None = None
    source_weight: float = 0.6


class NewsSentiment(BaseModel):
    ticker:         str
    days_analyzed:  int
    article_count:  int
    scored_count:   int
    overall_score:  float
    signal:         SentimentSignal
    bull_catalysts: list[str] = Field(default_factory=list)
    bear_catalysts: list[str] = Field(default_factory=list)
    summary:        str
    articles:       list[NewsArticle] = Field(default_factory=list)
    fetched_at:     datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data_warning:   str | None = None


class NewsRequest(BaseModel):
    ticker: str
    days:   int = Field(default=7, ge=1, le=30)


class NewsResponse(BaseModel):
    success:     bool
    sentiment:   NewsSentiment | None = None
    error:       str | None = None
    duration_ms: float | None = None
