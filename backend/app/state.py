from typing import Optional, Literal
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    question:       str
    route:          Optional[Literal["market", "filings", "filings_recent", "both", "news", "comprehensive"]]
    ticker:         Optional[str] 
    market_output:  Optional[str]
    filings_output: Optional[str]
    news_output:    Optional[str]
    citations:      Optional[list[dict]]
    final_answer:   Optional[str]
    skip_cache:     Optional[bool]
    ingest_pending: Optional[bool]