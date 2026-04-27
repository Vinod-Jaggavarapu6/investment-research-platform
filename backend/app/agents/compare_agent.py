"""
compare_agent.py — Multi-ticker comparison using parallel SEC filing retrieval

Flow:
  1. Extract tickers list from state (set by router for "compare" route)
  2. Retrieve filing chunks for each ticker in parallel (asyncio.gather)
  3. Format per-ticker context blocks
  4. Call Claude to produce a structured comparison with citations
"""

import asyncio
import logging
import os

import anthropic
from langsmith.wrappers import wrap_anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.retrieval import retrieve_chunks
from ..state import AgentState

logger = logging.getLogger(__name__)

MODEL       = os.getenv("COMPARE_AGENT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS  = 2048
RETRIEVAL_K = 5   # chunks per ticker

COMPARE_SYSTEM = """You are a senior investment analyst specializing in comparative company analysis.

You compare companies strictly using excerpts from their SEC filings (10-K annual, 10-Q quarterly, 8-K event).

Your comparison must:
1. Be grounded in the provided filing excerpts — do not use outside knowledge
2. Open with a brief framing of what is being compared
3. Present key findings per company (one section each)
4. Follow with a head-to-head comparison table or bullet list on the specific dimension asked
5. Close with a 2-3 sentence bottom-line summary of the most important difference

Citation format: [TICKER YEAR FILING-TYPE, Section] after each factual claim.
Example: [AAPL 2025 10-K, Item 1A] or [NVDA 2024 10-Q, MD&A]

If a company's filing data is absent or thin, say so explicitly.
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


def _build_context(per_ticker: dict[str, list[dict]]) -> str:
    """Format per-ticker chunks into a context block the LLM can read."""
    parts = []
    for ticker, chunks in per_ticker.items():
        if not chunks:
            parts.append(f"=== {ticker} ===\n[No filing data available for {ticker}]\n")
            continue
        lines = [f"=== {ticker} ==="]
        for i, c in enumerate(chunks, 1):
            filing_type = c.get("filing_type", "10-K")
            label = f"[{c['ticker']} {c['year']} {filing_type}, {c['section']}]"
            lines.append(
                f"--- Source {i}: {label} (relevance: {c['score']:.2f}) ---\n{c['text']}"
            )
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


async def compare_companies(
    question: str,
    tickers:  list[str],
    db:       AsyncSession,
    k:        int = RETRIEVAL_K,
) -> tuple[str, list[dict]]:
    """
    Run retrieval for each ticker in parallel, then call Claude to compare.
    Returns (answer_text, all_citations).
    """
    # Parallel retrieval — one pgvector query per ticker
    tasks = [_retrieve_for_ticker(question, t, db, k) for t in tickers]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    per_ticker: dict[str, list[dict]] = {}
    all_citations: list[dict] = []

    for item in raw_results:
        if isinstance(item, Exception):
            logger.error("[compare] retrieval error: %s", item)
            continue
        ticker, chunks = item
        per_ticker[ticker] = chunks
        all_citations.extend(chunks)

    # Ensure every requested ticker appears (even if empty)
    for t in tickers:
        per_ticker.setdefault(t, [])

    context = _build_context(per_ticker)

    tickers_str = " vs ".join(tickers)
    user_message = (
        f"Compare {tickers_str} based on the question below.\n\n"
        f"Question: {question}\n\n"
        f"SEC Filing Excerpts (grouped by company):\n{context}\n\n"
        f"Provide:\n"
        f"1. Key findings per company (separate section for each)\n"
        f"2. Head-to-head comparison on the specific dimension asked\n"
        f"3. Bottom-line summary (2-3 sentences)\n\n"
        f"Cite every factual claim with [TICKER YEAR FILING-TYPE, Section]."
    )

    client = wrap_anthropic(anthropic.AsyncAnthropic())
    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=COMPARE_SYSTEM,
        messages=[{"role": "user", "content": user_message}],
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
