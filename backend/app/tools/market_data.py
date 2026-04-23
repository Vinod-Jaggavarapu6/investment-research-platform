# THE KEY DISCIPLINE HERE:
#   Never return raw API output directly to the LLM.
#   - Validate everything
#   - Convert bad values (NaN, Inf, "N/A") to None
#   - Log non-fatal issues in fetch_errors
#   - Raise only on truly fatal conditions (invalid ticker)

from __future__ import annotations

import logging
from datetime import datetime, timezone

import yfinance as yf
from tenacity import (
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.models import DataSource, RawFinancialData

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HELPER: safe float conversion
# ─────────────────────────────────────────────

def _safe_float(value: object, field_name: str, errors: list[str]) -> float | None:
    """
    Convert any value to float, returning None on failure.

    WHY THIS EXISTS:
    yfinance returns these for "missing" data depending on field/security type:
        None, float('nan'), float('inf'), "N/A", "Infinity", 0 (sometimes)
    A bare float(value) call blows up on most of these.
    This function handles all cases and logs the issue in errors[].

    The errors list is passed by reference — we append to it in-place.
    The caller collects all errors and attaches them to RawFinancialData.
    """
    if value is None:
        return None

    try:
        result = float(value)
    except (ValueError, TypeError):
        errors.append(f"{field_name}: could not convert '{value}' to float")
        return None

    # float('nan') != float('nan') is True — only way to check for nan
    if result != result:
        errors.append(f"{field_name}: got NaN, treating as missing")
        return None

    if abs(result) == float("inf"):
        errors.append(f"{field_name}: got Inf, treating as missing")
        return None

    return result


# ─────────────────────────────────────────────
# INNER FETCHER: decorated with retry
# ─────────────────────────────────────────────

@retry(
    # Only retry on transient network errors, not on logic errors like ValueError
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    # Double wait time each attempt: 1s → 2s → 4s (capped at 8s)
    wait=wait_exponential(multiplier=1, min=1, max=8),
    # Stop after 3 total attempts
    stop=stop_after_attempt(3),
    # Log each retry as a WARNING so you can see it in your server logs
    before_sleep=before_sleep_log(logger, logging.WARNING),
    # Re-raise the final exception (don't swallow it)
    reraise=True,
)
def _fetch_from_yfinance(ticker: str) -> dict:
    """
    Raw yfinance fetch. Private — only called by fetch_financial_data().

    Decorated with retry for transient network failures.
    Raises ValueError for definitively invalid tickers (no retry needed).
    """
    stock = yf.Ticker(ticker)
    info = stock.info

    # yfinance returns a sparse dict (not an error) for invalid/delisted tickers.
    # The most reliable validity signal is the presence of a price field.
    has_price = any(
        info.get(k) for k in ("currentPrice", "regularMarketPrice", "previousClose")
    )

    if not info or not has_price:
        # Don't retry this — it's not a network error, it's a bad ticker
        raise ValueError(
            f"yfinance returned no price data for '{ticker}'. "
            "The ticker may be invalid, delisted, or a non-equity instrument."
        )

    return info


# ─────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────

def fetch_financial_data(ticker: str) -> RawFinancialData:
    """
    Fetch and clean financial data for a ticker. Returns RawFinancialData.

    This is the function the agent calls as its "tool."
    It never returns raw yfinance output — always a validated model.

    NON-FATAL vs FATAL errors:
        Non-fatal: a field is missing/NaN → logged in fetch_errors, 
                   model still returned, LLM told to acknowledge the gap
        Fatal: invalid ticker, or network failure after all retries →
               raises exception, caller returns an error response

    Args:
        ticker: Stock ticker symbol (case-insensitive, we uppercase it)

    Returns:
        RawFinancialData with all available fields populated

    Raises:
        ValueError: ticker is definitively invalid
        Exception: network failure after retry exhaustion
    """
    ticker = ticker.upper().strip()
    errors: list[str] = []

    # ── Attempt primary source ──────────────────
    try:
        info = _fetch_from_yfinance(ticker)
        logger.info(f"[{ticker}] yfinance fetch successful")

    except ValueError:
        # Invalid ticker — surface immediately, don't try fallback
        raise

    except (RetryError, Exception) as e:
        # Network failed after all retries.
        # Return a minimal shell so the LLM can acknowledge unavailability
        # rather than hallucinating data.
        logger.error(f"[{ticker}] yfinance failed after retries: {e}")
        return RawFinancialData(
            ticker=ticker,
            source=DataSource.FALLBACK,
            fetched_at=datetime.now(timezone.utc),
            fetch_errors=[
                f"Primary source (yfinance) failed: {str(e)[:200]}",
                "All financial metrics unavailable.",
            ],
        )

    # ── Map yfinance fields → RawFinancialData ──
    #
    # We map fields EXPLICITLY (not **info) because:
    # 1. yfinance field names are inconsistent camelCase
    # 2. Silent field mismatches are worse than verbose mapping
    # 3. You want to know exactly what you're reading

    # Revenue growth: yfinance provides 'revenueGrowth' (YoY quarterly).
    rev_growth = _safe_float(info.get("revenueGrowth"), "revenue_growth_yoy", errors)

    # Note when only earnings growth is available but not revenue growth
    if rev_growth is None and info.get("earningsGrowth") is not None:
        errors.append(
            "revenue_growth_yoy not available; earningsGrowth exists but was not substituted"
        )

    raw = RawFinancialData(
        ticker=ticker,
        company_name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),

        # Price
        current_price=_safe_float(
            info.get("currentPrice") or info.get("regularMarketPrice"),
            "current_price",
            errors,
        ),
        market_cap=_safe_float(info.get("marketCap"), "market_cap", errors),
        fifty_two_week_high=_safe_float(info.get("fiftyTwoWeekHigh"), "52w_high", errors),
        fifty_two_week_low=_safe_float(info.get("fiftyTwoWeekLow"), "52w_low", errors),

        # Valuation
        pe_ratio=_safe_float(info.get("trailingPE"), "pe_ratio", errors),
        forward_pe=_safe_float(info.get("forwardPE"), "forward_pe", errors),
        price_to_book=_safe_float(info.get("priceToBook"), "price_to_book", errors),
        price_to_sales=_safe_float(
            info.get("priceToSalesTrailing12Months"), "price_to_sales", errors
        ),
        ev_to_ebitda=_safe_float(info.get("enterpriseToEbitda"), "ev_to_ebitda", errors),

        # Profitability
        revenue_ttm=_safe_float(info.get("totalRevenue"), "revenue_ttm", errors),
        revenue_growth_yoy=rev_growth,
        gross_margin=_safe_float(info.get("grossMargins"), "gross_margin", errors),
        operating_margin=_safe_float(info.get("operatingMargins"), "operating_margin", errors),
        net_margin=_safe_float(info.get("profitMargins"), "net_margin", errors),
        return_on_equity=_safe_float(info.get("returnOnEquity"), "roe", errors),
        return_on_assets=_safe_float(info.get("returnOnAssets"), "roa", errors),

        # Balance sheet
        debt_to_equity=_safe_float(info.get("debtToEquity"), "debt_to_equity", errors),
        current_ratio=_safe_float(info.get("currentRatio"), "current_ratio", errors),
        free_cash_flow=_safe_float(info.get("freeCashflow"), "free_cash_flow", errors),

        # Dividends
        dividend_yield=_safe_float(info.get("dividendYield"), "dividend_yield", errors),
        payout_ratio=_safe_float(info.get("payoutRatio"), "payout_ratio", errors),

        source=DataSource.YFINANCE,
        fetched_at=datetime.now(timezone.utc),
        fetch_errors=errors,
    )

    if errors:
        logger.warning(f"[{ticker}] {len(errors)} non-fatal data warnings")

    return raw