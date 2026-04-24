import hashlib
from typing import Optional


# Cache TTLs — different data has different staleness tolerance
CACHE_TTL = {
    # Market data changes every minute during trading hours.
    # 5-minute TTL is a reasonable balance.
    "market_data": 300,          # 5 minutes

    # News is relatively fresh, but not second-by-second.
    "news_sentiment": 1800,      # 30 minutes

    # SEC filings don't change. 24h TTL is fine.
    "rag_answer": 86400,         # 24 hours

    # Full research report — most expensive, longest TTL.
    "full_report": 3600,         # 1 hour
}


def market_data_key(ticker: str) -> str:
    """
    Key: research:market:{TICKER}:v1
    
    We include 'v1' so that if we change the data structure,
    we can bump to 'v2' and old keys naturally expire without
    needing explicit invalidation. This is called 'key versioning'.
    """
    return f"research:market:{ticker.upper()}:v1"


def news_sentiment_key(ticker: str) -> str:
    return f"research:news:{ticker.upper()}:v1"


def rag_answer_key(ticker: str, query: str) -> str:
    """
    For RAG, the cache key must encode the query, not just the ticker.
    We hash the query so long queries don't produce huge keys.
    
    MD5 is fine here — this is not a security context, just a 
    deterministic fingerprint.
    """
    query_hash = hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]
    return f"research:rag:{ticker.upper()}:{query_hash}:v1"


def full_report_key(ticker: str, query: Optional[str] = None) -> str:
    if query:
        query_hash = hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]
        return f"research:report:{ticker.upper()}:{query_hash}:v1"
    return f"research:report:{ticker.upper()}:default:v1"