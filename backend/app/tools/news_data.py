"""
 Fetch company news from Finnhub
Responsibilities:
  - Fetch last N days of news for a ticker
  - Normalize raw Finnhub response into NewsArticle objects
  - Apply source quality weights
  - Retry on failure (same pattern as market_data.py)
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import finnhub
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.models import NewsArticle

logger = logging.getLogger(__name__)

NEWS_MAX_ARTICLES = int(os.getenv("NEWS_MAX_ARTICLES", "30"))

# ---------------------------------------------------------------------------
# Source quality weights
# ---------------------------------------------------------------------------

SOURCE_WEIGHTS: dict[str, float] = {
    "reuters":          1.0,
    "bloomberg":        1.0,
    "wall street journal": 0.95,
    "wsj":              0.95,
    "financial times":  0.95,
    "ft":               0.95,
    "cnbc":             0.80,
    "marketwatch":      0.75,
    "barrons":          0.80,
    "yahoo finance":    0.70,
    "benzinga":         0.65,
    "seeking alpha":    0.50,
    "motley fool":      0.50,
    "investopedia":     0.55,
}

DEFAULT_WEIGHT = 0.60


def get_source_weight(source: str) -> float:
    """Return quality weight for a news source."""
    source_lower = source.lower().strip()
    for key, weight in SOURCE_WEIGHTS.items():
        if key in source_lower:
            return weight
    return DEFAULT_WEIGHT


# ---------------------------------------------------------------------------
# Finnhub client
# ---------------------------------------------------------------------------

def get_finnhub_client() -> finnhub.Client:
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        raise ValueError("FINNHUB_API_KEY not set in environment")
    return finnhub.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Fetch news
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda retry_state: logger.warning(
        f"Finnhub fetch failed, retrying "
        f"(attempt {retry_state.attempt_number})..."
    ),
)
def _fetch_raw_news(
    client: finnhub.Client,
    ticker: str,
    from_date: str,
    to_date: str,
) -> list[dict]:
    """Raw Finnhub API call with retry."""
    return client.company_news(ticker, _from=from_date, to=to_date)


def fetch_news(ticker: str, days: int = 7, max_articles: int = NEWS_MAX_ARTICLES) -> list[NewsArticle]:
    """
    Fetch and normalize company news from Finnhub.

    Args:
        ticker:       Stock ticker symbol (e.g. "AAPL")
        days:         Number of days to look back
        max_articles: Maximum number of articles to return (newest first)

    Returns:
        List of NewsArticle objects, sorted newest first.
        Empty list if no news found — never raises.
    """
    client = get_finnhub_client()

    to_dt   = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days)

    from_date = from_dt.strftime("%Y-%m-%d")
    to_date   = to_dt.strftime("%Y-%m-%d")

    logger.info(f"[{ticker}] fetching news {from_date} → {to_date}")

    try:
        raw_articles = _fetch_raw_news(client, ticker, from_date, to_date)
    except Exception as e:
        logger.error(f"[{ticker}] Finnhub fetch failed after retries: {e}")
        return []

    if not raw_articles:
        logger.info(f"[{ticker}] no news found for period")
        return []

    articles = []
    for raw in raw_articles:
        try:
            source   = raw.get("source") or "unknown"
            headline = raw.get("headline") or ""
            url      = raw.get("url") or ""
            summary  = raw.get("summary") or None

            # Finnhub returns Unix timestamp
            published_at = datetime.fromtimestamp(
                raw.get("datetime", 0),
                tz=timezone.utc,
            )

            if not headline:        # skip empty headlines
                continue

            articles.append(NewsArticle(
                headline     = headline,
                source       = source,
                url          = url,
                published_at = published_at,
                summary      = summary,
                source_weight= get_source_weight(source),
            ))

        except Exception as e:
            logger.warning(f"[{ticker}] skipping malformed article: {e}")
            continue

    # Sort newest first, then cap
    articles.sort(key=lambda a: a.published_at, reverse=True)
    articles = articles[:max_articles]

    logger.info(f"[{ticker}] fetched {len(articles)} articles")
    return articles