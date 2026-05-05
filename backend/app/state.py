from typing import Optional, Literal
from typing_extensions import TypedDict, Required, NotRequired

RouteType = Literal[
    "market", "filings", "filings_recent", "both",
    "news", "comprehensive", "compare", "general",
]


class AgentState(TypedDict, total=False):
    question:       Required[str]
    route:          NotRequired[Optional[RouteType]]
    ticker:         NotRequired[Optional[str]]
    tickers:        NotRequired[Optional[list[str]]]
    market_output:  NotRequired[Optional[str]]
    filings_output: NotRequired[Optional[str]]
    news_output:    NotRequired[Optional[str]]
    citations:      NotRequired[Optional[list[dict]]]
    final_answer:   NotRequired[Optional[str]]
    skip_cache:     NotRequired[Optional[bool]]
    ingest_pending: NotRequired[Optional[bool]]
    messages:       NotRequired[Optional[list[dict]]]  # [{role, content}, ...]
    # Keyed by node name (e.g. "market_agent"). Set by node_error() so the
    # synthesizer can surface failures to the user instead of silently degrading.
    agent_errors:       NotRequired[Optional[dict[str, str]]]
    # Preserved research text from synthesizer — reused on follow-up turns so the
    # cached block sent to Claude is byte-for-byte identical, enabling cache reads.
    research_context:        NotRequired[Optional[str]]
    research_context_ticker: NotRequired[Optional[str]]


# App-lifetime singletons set during lifespan startup
checkpointer = None
cache = None