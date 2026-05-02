"""
compare_agent.py — Multi-ticker comparison using parallel market data + SEC filing retrieval

Flow:
  1. Extract tickers list from state (set by router for "compare" route)
  2. Fetch live market data AND filing chunks for each ticker in parallel
  3. Format per-ticker context blocks (market data section + filing section)
  4. Call Claude to produce a structured comparison with citations
"""

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.retrieval import retrieve_chunks
from app.tools.market_data import fetch_financial_data
from .financial_agent import _format_for_prompt
from ..clients import get_anthropic_async
from ..state import AgentState

logger = logging.getLogger(__name__)

MODEL       = os.getenv("COMPARE_AGENT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS  = 2048
RETRIEVAL_K = 5   # chunks per ticker

COMPARE_SYSTEM = """You are a senior investment analyst specializing in comparative company analysis.

You have access to two data sources per company:
1. **Live market data** — current prices, valuation ratios, margins, and balance sheet metrics
2. **SEC filing excerpts** — 10-K annual, 10-Q quarterly, 8-K event disclosures

## Core rules
- Ground every claim in the data provided. Do not use outside knowledge or training data.
- If a company's filing data is absent or thin, say so explicitly in its section.
- Live market metrics (price, P/E, margins) require no citation — state them as current figures.
- Every claim drawn from a filing must end with a citation: [TICKER YEAR FILING-TYPE, Section].
  Examples: [AAPL 2025 10-K, Item 1A] · [NVDA 2024 10-Q, MD&A] · [TSLA 2024 8-K, Item 8.01]

## Required output structure
1. **Framing** (1–2 sentences): what dimension is being compared and which companies.
2. **Per-company snapshot** (one section per ticker, in the order provided):
   - Lead with the most relevant live market metric for the question (e.g., P/E, growth, margin).
   - Follow with any filing-based context that adds depth.
   - Cite every filing claim. State market metrics inline without citation.
   - Note if data is sparse: "Limited filing data available for [TICKER]."
3. **Head-to-head comparison**: a markdown table comparing the companies on the specific metric or dimension asked. Include both live market figures and filing-derived figures where relevant.
4. **Bottom-line summary** (2–3 sentences): the single most important difference, stated plainly.
   - If the question asks which is better to buy, give a relative valuation verdict grounded in the metrics (e.g., which appears more attractively valued, which has stronger fundamentals). This is not personalized investment advice — it is a data-driven relative assessment.

## Tone and constraints
- Write for a sophisticated investor.
- Avoid filler phrases: "it's worth noting", "it's important to consider", "based on the above".
- Do not give personalized investment advice. Relative valuation assessments based on metrics are acceptable.
- Keep the total response under 600 words unless the question explicitly requires deeper analysis.
- Use **bold** for company tickers on first mention in each section.

## Edge cases
- If market data fetch failed for a company, note it and rely on filing data only for that company.
- If only one company has filing data, produce the structure above and flag the missing company.
- If the filings span different years for different companies, note the year mismatch when it could affect the comparison.
- Quantitative metrics should be stated as exact figures, not rounded estimates.
"""


async def _retrieve_for_ticker(
    query: str,
    ticker: str,
    db: AsyncSession,
    k: int,
) -> tuple[str, list[dict]]:
    """Wrapper so gather errors are easy to attribute."""
    chunks = await retrieve_chunks(query=query, db=db, ticker=ticker, k=k)
    return ticker, chunks


async def _fetch_market_for_ticker(ticker: str) -> tuple[str, str | None]:
    """Fetch live market data for a ticker in a thread executor (sync → async)."""
    loop = asyncio.get_event_loop()
    try:
        raw = await loop.run_in_executor(None, fetch_financial_data, ticker)
        return ticker, _format_for_prompt(raw)
    except Exception as e:
        logger.warning("[compare] market data fetch failed for %s: %s", ticker, e)
        return ticker, None


def _build_context(
    per_ticker: dict[str, list[dict]],
    market_data: dict[str, str | None],
) -> str:
    """Format per-ticker market data + filing chunks into a context block."""
    # ── Live market data section ──────────────────────────────────────────
    market_parts = []
    for ticker in per_ticker:
        md = market_data.get(ticker)
        if md:
            market_parts.append(md)
        else:
            market_parts.append(f"=== {ticker} ===\n[Live market data unavailable for {ticker}]\n")

    # ── SEC filing excerpts section ───────────────────────────────────────
    filing_parts = []
    for ticker, chunks in per_ticker.items():
        if not chunks:
            filing_parts.append(f"=== {ticker} ===\n[No filing data available for {ticker}]\n")
            continue
        lines = [f"=== {ticker} ==="]
        for i, c in enumerate(chunks, 1):
            filing_type = c.get("filing_type", "10-K")
            label = f"[{c['ticker']} {c['year']} {filing_type}, {c['section']}]"
            lines.append(
                f"--- Source {i}: {label} (relevance: {c['score']:.2f}) ---\n{c['text']}"
            )
        filing_parts.append("\n".join(lines))

    market_block  = "\n\n".join(market_parts)
    filings_block = "\n\n".join(filing_parts)
    return (
        f"## Live Market Data\n\n{market_block}\n\n"
        f"## SEC Filing Excerpts\n\n{filings_block}"
    )


async def compare_companies(
    question: str,
    tickers:  list[str],
    db:       AsyncSession,
    k:        int = RETRIEVAL_K,
) -> tuple[str, list[dict]]:
    """
    Run market data fetch + filing retrieval for each ticker in parallel, then call Claude.
    Returns (answer_text, all_citations).
    """
    # Parallel fetch: live market data + pgvector retrieval for every ticker simultaneously
    filing_tasks = [_retrieve_for_ticker(question, t, db, k) for t in tickers]
    market_tasks = [_fetch_market_for_ticker(t) for t in tickers]
    all_tasks = filing_tasks + market_tasks
    all_results = await asyncio.gather(*all_tasks, return_exceptions=True)

    filing_results = all_results[:len(tickers)]
    market_results = all_results[len(tickers):]

    per_ticker: dict[str, list[dict]] = {}
    all_citations: list[dict] = []
    market_data: dict[str, str | None] = {}

    for item in filing_results:
        if isinstance(item, Exception):
            logger.error("[compare] retrieval error: %s", item)
            continue
        ticker, chunks = item
        per_ticker[ticker] = chunks
        all_citations.extend(chunks)

    for item in market_results:
        if isinstance(item, Exception):
            logger.error("[compare] market fetch error: %s", item)
            continue
        ticker, formatted = item
        market_data[ticker] = formatted

    # Ensure every requested ticker appears (even if empty)
    for t in tickers:
        per_ticker.setdefault(t, [])
        market_data.setdefault(t, None)

    context = _build_context(per_ticker, market_data)

    tickers_str = " vs ".join(tickers)

    response = await get_anthropic_async().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=COMPARE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        # Cache the data context — reusable across different questions on the same ticker pair
                        "text": (
                            f"Compare {tickers_str} based on the question below.\n\n"
                            f"Data (live market metrics + SEC filing excerpts, grouped by company):\n{context}"
                        ),
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"\n\nQuestion: {question}\n\n"
                            f"Provide:\n"
                            f"1. Per-company snapshot (lead with market metrics, add filing context)\n"
                            f"2. Head-to-head comparison table on the specific dimension asked\n"
                            f"3. Bottom-line summary (2-3 sentences) — if asked which is better to buy, give a relative valuation verdict\n\n"
                            f"Cite filing claims with [TICKER YEAR FILING-TYPE, Section]. State market metrics inline without citation."
                        ),
                    },
                ],
            }
        ],
    )

    answer = response.content[0].text
    return answer, all_citations


def make_compare_node(db: AsyncSession):
    """Factory returning a LangGraph-compatible compare node with db in closure."""

    async def compare_node(state: AgentState) -> dict:
        tickers = state.get("tickers") or []

        # Graceful fallback if router gave a single ticker instead of a list
        if not tickers:
            single = state.get("ticker")
            tickers = [single] if single else []

        if len(tickers) < 2:
            return {
                "final_answer": (
                    "I need at least two company tickers to run a comparison. "
                    "Try asking something like: 'Compare AAPL vs MSFT on risk factors'."
                ),
                "citations": [],
            }

        logger.info(
            "[compare] tickers=%r question=%r", tickers, state["question"][:60]
        )

        answer, citations = await compare_companies(
            question=state["question"],
            tickers=tickers,
            db=db,
        )

        logger.info(
            "[compare] done tickers=%r answer_len=%d citations=%d",
            tickers, len(answer), len(citations),
        )

        return {
            "final_answer": answer,
            "citations":    citations,
        }

    return compare_node
