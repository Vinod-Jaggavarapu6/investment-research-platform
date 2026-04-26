from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import openai
from dotenv import load_dotenv
from langsmith.wrappers import wrap_openai

from ..state import AgentState

from app.models import (
    AnalysisResponse,
    DataSource,
    FinancialSnapshot,
    RawFinancialData,
    SignalStrength,
)
from app.tools.market_data import fetch_financial_data

logger = logging.getLogger(__name__)

load_dotenv()

client = wrap_openai(openai.OpenAI())

MODEL      = os.getenv("FINANCIAL_AGENT_MODEL", "gpt-4o")
MAX_TOKENS = int(os.getenv("FINANCIAL_AGENT_MAX_TOKENS", "1500"))


# ─────────────────────────────────────────────
# THE TOOL DEFINITION  (OpenAI function-calling format)
# ─────────────────────────────────────────────

SUBMIT_ANALYSIS_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_analysis",
        "description": (
            "Submit your completed financial analysis as structured data. "
            "Call this exactly once after reasoning through the financial data provided. "
            "Every number you include must appear in the source data — do not invent values."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Uppercase ticker symbol",
                },
                "company_name": {"type": ["string", "null"]},
                "sector":       {"type": ["string", "null"]},
                "current_price": {
                    "type": ["number", "null"],
                    "description": "Current stock price in USD",
                },
                "market_cap_billions": {
                    "type": ["number", "null"],
                    "description": "Market cap in billions USD, rounded to 2 decimal places",
                },
                "revenue_growth_yoy": {
                    "type": ["number", "null"],
                    "description": "YoY revenue growth as decimal. 0.12 = 12% growth.",
                },
                "gross_margin": {
                    "type": ["number", "null"],
                    "description": "Gross margin as decimal, e.g. 0.43",
                },
                "net_margin": {
                    "type": ["number", "null"],
                    "description": "Net profit margin as decimal",
                },
                "pe_ratio": {
                    "type": ["number", "null"],
                    "description": "Trailing P/E. Set null if earnings are negative.",
                },
                "forward_pe":    {"type": ["number", "null"]},
                "ev_to_ebitda":  {"type": ["number", "null"]},
                "debt_to_equity": {
                    "type": ["number", "null"],
                    "description": "Debt/equity ratio. Above 2.0 is elevated for most sectors.",
                },
                "current_ratio": {
                    "type": ["number", "null"],
                    "description": "Current ratio. Below 1.0 suggests liquidity pressure.",
                },
                "signal": {
                    "type": "string",
                    "enum": [
                        "STRONG_BUY", "BUY", "HOLD",
                        "SELL", "STRONG_SELL", "INSUFFICIENT_DATA",
                    ],
                    "description": (
                        "Overall analyst signal. Must follow from the fundamentals, "
                        "not from brand recognition or general reputation."
                    ),
                },
                "key_strengths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "2-4 strengths grounded in specific numbers from the data. "
                        "Example: 'Gross margin of 72% is exceptional for enterprise software'"
                    ),
                },
                "key_risks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "2-4 risks grounded in specific numbers from the data. "
                        "Example: 'Debt/equity of 1.8x leaves limited room for further leverage'"
                    ),
                },
                "analysis_summary": {
                    "type": "string",
                    "description": (
                        "2-3 sentence analyst summary. Lead with the single most important insight. "
                        "Cite specific numbers. Never write vague phrases like 'solid fundamentals'."
                    ),
                },
                "data_quality_warning": {
                    "type": ["string", "null"],
                    "description": (
                        "If critical metrics were missing, note it here so the caller "
                        "knows to discount the analysis. Null if data was complete."
                    ),
                },
            },
            "required": [
                "ticker",
                "signal",
                "analysis_summary",
                "key_strengths",
                "key_risks",
            ],
        },
    },
}


# ─────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a senior equity research analyst with CFA designation.

Your task: analyze the financial data provided and call submit_analysis with your structured assessment.

Rules:
- Be specific. "Revenue grew 15% YoY to $95B" is useful. "Strong growth" is not.
- Sector context matters. A 60% gross margin is excellent for SaaS, poor for retail.
- Your signal must follow from the metrics — not from brand recognition or general reputation.
- If a metric is null, do not invent a value. Acknowledge the gap in data_quality_warning if it's critical.
- Call submit_analysis exactly once. Put your reasoning inside the structured fields, not before the call.\
"""


def _format_for_prompt(raw: RawFinancialData) -> str:
    def pct(v: float | None) -> str:
        return f"{v * 100:.1f}%" if v is not None else "N/A"

    def num(v: float | None, dp: int = 2) -> str:
        return f"{v:.{dp}f}" if v is not None else "N/A"

    def billions(v: float | None) -> str:
        return f"${v / 1e9:.2f}B" if v is not None else "N/A"

    lines = [
        f"=== {raw.ticker} — {raw.company_name or 'Unknown Company'} ===",
        f"Sector: {raw.sector or 'N/A'}  |  Industry: {raw.industry or 'N/A'}",
        f"Data source: {raw.source.value}  |  Fetched: {raw.fetched_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "PRICE & SIZE",
        f"  Current price:      {num(raw.current_price)}",
        f"  Market cap:         {billions(raw.market_cap)}",
        f"  52-week range:      {num(raw.fifty_two_week_low)} – {num(raw.fifty_two_week_high)}",
        "",
        "VALUATION",
        f"  P/E (trailing):     {num(raw.pe_ratio)}",
        f"  P/E (forward):      {num(raw.forward_pe)}",
        f"  Price/Book:         {num(raw.price_to_book)}",
        f"  Price/Sales:        {num(raw.price_to_sales)}",
        f"  EV/EBITDA:          {num(raw.ev_to_ebitda)}",
        "",
        "PROFITABILITY",
        f"  Revenue (TTM):      {billions(raw.revenue_ttm)}",
        f"  Revenue growth YoY: {pct(raw.revenue_growth_yoy)}",
        f"  Gross margin:       {pct(raw.gross_margin)}",
        f"  Operating margin:   {pct(raw.operating_margin)}",
        f"  Net margin:         {pct(raw.net_margin)}",
        f"  Return on equity:   {pct(raw.return_on_equity)}",
        f"  Return on assets:   {pct(raw.return_on_assets)}",
        "",
        "BALANCE SHEET",
        f"  Debt/Equity:        {num(raw.debt_to_equity)}",
        f"  Current ratio:      {num(raw.current_ratio)}",
        f"  Free cash flow:     {billions(raw.free_cash_flow)}",
        "",
        "DIVIDENDS",
        f"  Dividend yield:     {pct(raw.dividend_yield)}",
        f"  Payout ratio:       {pct(raw.payout_ratio)}",
    ]

    if raw.fetch_errors:
        lines += [
            "",
            "DATA WARNINGS (fields that were missing or invalid during fetch):",
        ]
        for err in raw.fetch_errors:
            lines.append(f"  ⚠ {err}")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# HELPER: parse OpenAI tool call → FinancialSnapshot
# ─────────────────────────────────────────────

def _parse_tool_call(response, raw: RawFinancialData) -> FinancialSnapshot:
    tool_calls = (response.choices[0].message.tool_calls or []) if response.choices else []
    for tc in tool_calls:
        if tc.function.name == "submit_analysis":
            data = json.loads(tc.function.arguments)

            market_cap_b = data.get("market_cap_billions")
            if market_cap_b is None and raw.market_cap is not None:
                market_cap_b = round(raw.market_cap / 1e9, 2)

            return FinancialSnapshot(
                ticker=data["ticker"],
                company_name=data.get("company_name"),
                sector=data.get("sector"),
                current_price=data.get("current_price"),
                market_cap_billions=market_cap_b,
                revenue_growth_yoy=data.get("revenue_growth_yoy"),
                gross_margin=data.get("gross_margin"),
                net_margin=data.get("net_margin"),
                pe_ratio=data.get("pe_ratio"),
                forward_pe=data.get("forward_pe"),
                ev_to_ebitda=data.get("ev_to_ebitda"),
                debt_to_equity=data.get("debt_to_equity"),
                current_ratio=data.get("current_ratio"),
                signal=SignalStrength(data["signal"]),
                key_strengths=data.get("key_strengths", []),
                key_risks=data.get("key_risks", []),
                analysis_summary=data["analysis_summary"],
                data_quality_warning=data.get("data_quality_warning"),
                source=raw.source,
            )

    raise RuntimeError(
        f"Model did not call submit_analysis. "
        f"Tool calls: {[tc.function.name for tc in tool_calls]}"
    )


# ─────────────────────────────────────────────
# THE AGENT — public entry point
# ─────────────────────────────────────────────

def analyze_ticker(ticker: str, include_raw: bool = False) -> AnalysisResponse:
    start  = time.perf_counter()
    ticker = ticker.upper().strip()

    # ── Step A: Fetch raw data ──────────────────────────────────────────
    try:
        raw = fetch_financial_data(ticker)
        logger.info(f"[{ticker}] raw data fetched — {len(raw.fetch_errors)} warnings")
    except ValueError as e:
        return AnalysisResponse(success=False, error=str(e), duration_ms=_elapsed(start))
    except Exception as e:
        logger.exception(f"[{ticker}] unexpected fetch error")
        return AnalysisResponse(
            success=False,
            error=f"Data fetch failed: {str(e)[:300]}",
            duration_ms=_elapsed(start),
        )

    # ── Step B: Build the prompt ────────────────────────────────────────
    user_message = (
        f"Analyze the following financial data for {ticker} "
        f"and call submit_analysis with your structured assessment.\n\n"
        f"{_format_for_prompt(raw)}"
    )

    # ── Step C: Call OpenAI ─────────────────────────────────────────────
    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_completion_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            tools=[SUBMIT_ANALYSIS_TOOL],
            tool_choice={"type": "function", "function": {"name": "submit_analysis"}},
        )
        logger.info(
            f"[{ticker}] LLM done — "
            f"finish_reason={response.choices[0].finish_reason} "
            f"in={response.usage.prompt_tokens}tok "
            f"out={response.usage.completion_tokens}tok"
        )
    except openai.APIConnectionError as e:
        return AnalysisResponse(
            success=False, error=f"Could not reach OpenAI API: {e}", duration_ms=_elapsed(start),
        )
    except openai.RateLimitError:
        return AnalysisResponse(
            success=False, error="OpenAI rate limit hit — wait a moment and retry", duration_ms=_elapsed(start),
        )
    except openai.APIStatusError as e:
        return AnalysisResponse(
            success=False, error=f"OpenAI API error {e.status_code}: {e.message}", duration_ms=_elapsed(start),
        )

    # ── Step D: Parse tool call → FinancialSnapshot ─────────────────────
    try:
        snapshot = _parse_tool_call(response, raw)
    except (RuntimeError, KeyError, ValueError) as e:
        logger.exception(f"[{ticker}] failed to parse LLM output")
        return AnalysisResponse(
            success=False,
            error=f"Failed to parse LLM output: {str(e)[:300]}",
            duration_ms=_elapsed(start),
        )

    elapsed = _elapsed(start)
    logger.info(f"[{ticker}] complete in {elapsed}ms — signal={snapshot.signal.value}")

    return AnalysisResponse(
        success=True,
        snapshot=snapshot,
        raw_data=raw if include_raw else None,
        duration_ms=elapsed,
    )


def _elapsed(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def make_market_node():
    async def market_node(state: AgentState) -> dict:
        ticker = state.get("ticker")

        if not ticker:
            return {"market_output": "No ticker specified — cannot fetch market data."}

        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, analyze_ticker, ticker, False)

        if not result.success:
            return {"market_output": f"Market data fetch failed: {result.error}"}

        return {"market_output": result.model_dump_json(indent=2)}

    return market_node
