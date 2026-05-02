from typing import Optional, Literal
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    question:       str
    route:          Optional[Literal["market", "filings", "filings_recent", "both", "news", "comprehensive", "compare"]]
    ticker:         Optional[str]
    tickers:        Optional[list[str]]
    market_output:  Optional[str]
    filings_output: Optional[str]
    news_output:    Optional[str]
    citations:      Optional[list[dict]]
    final_answer:   Optional[str]
    skip_cache:     Optional[bool]
    ingest_pending: Optional[bool]
    messages:       Optional[list[dict]]  #   [{role,                                                                                                                                                
# App-lifetime singletons set during lifespan startup
checkpointer = None
cache = None