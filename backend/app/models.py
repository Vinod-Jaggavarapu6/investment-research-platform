from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional
class FilingsRequest(BaseModel):
    question: str
    ticker:   Optional[str] = None
    k:        int           = 5

class DataSource(str, Enum):
    YFINANCE = "yfinance"
    FALLBACK = "fallback"  


class SignalStrength(str, Enum):
    STRONG_BUY        = "STRONG_BUY"
    BUY               = "BUY"
    HOLD              = "HOLD"
    SELL              = "SELL"
    STRONG_SELL       = "STRONG_SELL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


# ─────────────────────────────────────────────
# MODEL 1: RawFinancialData
# What the yfinance tool returns, after cleaning.
# ─────────────────────────────────────────────

class RawFinancialData(BaseModel):
    """
    Intermediate model: cleaned API data, before LLM analysis.

    Keeping this separate from FinancialSnapshot matters for debugging.
    If the LLM's analysis is wrong, you can check:
      - Was the raw data bad? → fetcher bug
      - Was the raw data fine but analysis wrong? → prompt bug
    Those are different problems with different fixes.
    """
    ticker: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    current_price: float | None = None
    market_cap: float | None = None        
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    pe_ratio: float | None = None            
    forward_pe: float | None = None
    price_to_book: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None

    # Profitability (margins as decimals: 0.27 = 27%)
    revenue_ttm: float | None = None          # trailing 12-month revenue, raw USD
    revenue_growth_yoy: float | None = None   # 0.12 = 12% YoY growth
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    return_on_equity: float | None = None
    return_on_assets: float | None = None
    # Balance sheet
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    free_cash_flow: float | None = None       # TTM, raw USD

    # Dividends
    dividend_yield: float | None = None
    payout_ratio: float | None = None

    # Metadata — always populated
    source: DataSource = DataSource.YFINANCE
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fetch_errors: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal issues during fetch, e.g. 'pe_ratio: got NaN'. "
            "Passed to the LLM so it can acknowledge data gaps in its analysis."
        )
    )


# ─────────────────────────────────────────────
# MODEL 2: FinancialSnapshot
# What the LLM produces — the final analysis.
# ─────────────────────────────────────────────

class FinancialSnapshot(BaseModel):
    """
    The structured output of the agent. This is what your API returns.

    IMPORTANT: Every Field description here is read by the LLM when it
    fills in the field. Treat descriptions like mini-prompts.
    Vague description → vague LLM output.

    Note market_cap_billions vs raw market_cap in RawFinancialData:
    LLMs are bad at reasoning about 3000000000000. We convert to
    billions (3000.0) before passing data to the model.
    """
    ticker: str
    company_name: str | None = None
    sector: str | None = None

    # Price / size (human-readable)
    current_price: float | None = Field(None, description="Current stock price in USD")
    market_cap_billions: float | None = Field(
        None,
        description="Market cap in billions USD, e.g. 3000.0 for Apple"
    )

    # Growth
    revenue_growth_yoy: float | None = Field(
        None,
        description="YoY revenue growth as decimal. 0.15 means 15% growth."
    )

    # Margins (decimals)
    gross_margin: float | None = Field(None, description="Gross margin as decimal, e.g. 0.43")
    net_margin: float | None = Field(None, description="Net profit margin as decimal")

    # Valuation
    pe_ratio: float | None = Field(
        None,
        description="Trailing P/E ratio. Null if earnings are negative."
    )
    forward_pe: float | None = None
    ev_to_ebitda: float | None = None

    # Balance sheet
    debt_to_equity: float | None = Field(
        None,
        description="Debt/equity ratio. Above 2.0 is elevated for most sectors."
    )
    current_ratio: float | None = Field(
        None,
        description="Current ratio. Below 1.0 indicates liquidity pressure."
    )

    # LLM-generated fields — the core product
    signal: SignalStrength = Field(
        ...,
        description="Overall analyst signal. Must follow from the data, not brand recognition."
    )
    key_strengths: list[str] = Field(
        default_factory=list,
        description="2-4 specific strengths grounded in actual numbers."
    )
    key_risks: list[str] = Field(
        default_factory=list,
        description="2-4 specific risks grounded in actual numbers."
    )
    analysis_summary: str = Field(
        ...,
        description=(
            "2-3 sentence analyst summary. Lead with the most important insight. "
            "Cite specific numbers. Never say 'strong fundamentals' without backing it up."
        )
    )
    data_quality_warning: str | None = Field(
        None,
        description="Note critical missing data so the caller can discount the analysis."
    )

    # Provenance
    source: DataSource = DataSource.YFINANCE
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────
# MODEL 3: API envelope
# ─────────────────────────────────────────────

class AnalysisRequest(BaseModel):
    """Request body for POST /analyze"""
    ticker: str = Field(..., examples=["AAPL", "MSFT"])
    include_raw_data: bool = Field(
        False,
        description="Include raw fetched data in the response (for debugging)"
    )


class AnalysisResponse(BaseModel):
    """
    Top-level API response — always this shape, success or failure.

    WHY AN ENVELOPE?
    If you return a bare FinancialSnapshot, a failed request has no consistent
    shape. With an envelope, success=False always means error is populated.
    Your frontend and future agents always check success first, then read data.
    """
    success: bool
    snapshot: FinancialSnapshot | None = None
    raw_data: RawFinancialData | None = None    # only if include_raw_data=True
    error: str | None = None
    duration_ms: float | None = None



class ResearchRequest(BaseModel):
    question: str
    thread_id: Optional[str] = None     # pass same ID to resume a run

class ResearchResponse(BaseModel):
    thread_id: str
    route: str
    final_answer: str
    citations: list[dict]

class SentimentSignal(str, Enum):
    VERY_BULLISH  = "VERY_BULLISH"
    BULLISH       = "BULLISH"
    NEUTRAL       = "NEUTRAL"
    BEARISH       = "BEARISH"
    VERY_BEARISH  = "VERY_BEARISH"
    INSUFFICIENT  = "INSUFFICIENT_DATA"


class ArticleSentiment(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL  = "NEUTRAL"
    MIXED    = "MIXED"


class NewsArticle(BaseModel):
    """Per-article data from Finnhub + Claude scoring."""
    headline:      str
    source:        str
    url:           str
    published_at:  datetime
    summary:       Optional[str] = None

    # Claude-scored fields
    sentiment:     Optional[ArticleSentiment] = None
    score:         Optional[float] = None        # -1.0 to +1.0
    justification: Optional[str]  = None
    source_weight: float          = 0.6          # default weight


class NewsSentiment(BaseModel):
    """Aggregate sentiment across all articles for a ticker."""
    ticker:         str
    days_analyzed:  int
    article_count:  int
    scored_count:   int                          # articles Claude actually scored

    overall_score:  float                        # weighted average, -1.0 to +1.0
    signal:         SentimentSignal

    bull_catalysts: list[str] = Field(default_factory=list)   # top positive drivers
    bear_catalysts: list[str] = Field(default_factory=list)   # top negative drivers
    summary:        str                          # 2-3 sentence synthesis

    articles:       list[NewsArticle] = Field(default_factory=list)
    fetched_at:     datetime = Field(default_factory=datetime.utcnow)
    data_warning:   Optional[str] = None


class NewsRequest(BaseModel):
    ticker: str
    days:   int = Field(default=7, ge=1, le=30)


class NewsResponse(BaseModel):
    success:   bool
    sentiment: Optional[NewsSentiment] = None
    error:     Optional[str]           = None
    duration_ms: Optional[float]       = None

