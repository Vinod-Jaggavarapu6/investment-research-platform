from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


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


class RawFinancialData(BaseModel):
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

    source: DataSource = DataSource.YFINANCE
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    fetch_errors: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal issues during fetch, e.g. 'pe_ratio: got NaN'. "
            "Passed to the LLM so it can acknowledge data gaps in its analysis."
        ),
    )


class FinancialSnapshot(BaseModel):
    ticker: str
    company_name: str | None = None
    sector: str | None = None

    current_price: float | None = Field(None, description="Current stock price in USD")
    market_cap_billions: float | None = Field(
        None, description="Market cap in billions USD, e.g. 3000.0 for Apple"
    )
    revenue_growth_yoy: float | None = Field(
        None, description="YoY revenue growth as decimal. 0.15 means 15% growth."
    )
    gross_margin: float | None = Field(None, description="Gross margin as decimal, e.g. 0.43")
    net_margin: float | None = Field(None, description="Net profit margin as decimal")
    pe_ratio: float | None = Field(
        None, description="Trailing P/E ratio. Null if earnings are negative."
    )
    forward_pe: float | None = None
    ev_to_ebitda: float | None = None
    debt_to_equity: float | None = Field(
        None, description="Debt/equity ratio. Above 2.0 is elevated for most sectors."
    )
    current_ratio: float | None = Field(
        None, description="Current ratio. Below 1.0 indicates liquidity pressure."
    )

    signal: SignalStrength = Field(
        ..., description="Overall analyst signal. Must follow from the data, not brand recognition."
    )
    key_strengths: list[str] = Field(
        default_factory=list, description="2-4 specific strengths grounded in actual numbers."
    )
    key_risks: list[str] = Field(
        default_factory=list, description="2-4 specific risks grounded in actual numbers."
    )
    analysis_summary: str = Field(
        ...,
        description=(
            "2-3 sentence analyst summary. Lead with the most important insight. "
            "Cite specific numbers. Never say 'strong fundamentals' without backing it up."
        ),
    )
    data_quality_warning: str | None = Field(
        None, description="Note critical missing data so the caller can discount the analysis."
    )

    source: DataSource = DataSource.YFINANCE
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AnalysisRequest(BaseModel):
    ticker: str = Field(..., examples=["AAPL", "MSFT"])
    include_raw_data: bool = Field(
        False, description="Include raw fetched data in the response (for debugging)"
    )


class AnalysisResponse(BaseModel):
    success: bool
    snapshot: FinancialSnapshot | None = None
    raw_data: RawFinancialData | None = None
    error: str | None = None
    duration_ms: float | None = None
