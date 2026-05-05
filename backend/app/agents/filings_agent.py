"""
filings_agent.py — RAG agent that answers questions about SEC filings

Flow:
  1. User asks a question (optionally specifying a ticker)
  2. Agent calls retrieve_chunks() to get relevant 10-K/10-Q/8-K sections
  3. Agent passes retrieved chunks + question to Claude
  4. Claude synthesizes a grounded answer with citations

Routes:
  filings        → all filing types, standard prompt
  filings_recent → 10-Q/8-K only, higher K, recent-focused prompt
"""

import asyncio
import os
from dataclasses import dataclass
from langsmith import traceable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.retrieval import retrieve_chunks
from ..clients import get_openai_async
from ..metrics import llm_tokens_total
from ..state import AgentState
from .base import node_error


logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL              = os.getenv("FILINGS_AGENT_MODEL", "gpt-4o")
MAX_TOKENS         = int(os.getenv("FILINGS_AGENT_MAX_TOKENS", "2048"))
RETRIEVAL_K        = int(os.getenv("FILINGS_RETRIEVAL_K", "10"))
RETRIEVAL_K_RECENT = int(os.getenv("FILINGS_RETRIEVAL_K_RECENT", "12"))
HYDE_MODEL         = "gpt-4o-mini"

SYSTEM_PROMPT = """You are a financial research analyst specializing in SEC filings.

You answer questions based strictly on the provided excerpts from SEC filings
(10-K annual reports, 10-Q quarterly reports, and 8-K current event reports).
Your answers must:
  1. Be grounded in the provided context — do not use outside knowledge
  2. Cite sources using [Ticker YEAR Filing-Type, Section] format after each claim
     e.g. [AAPL 2025 10-K, Item 7] or [MSFT 2024 10-Q, MD&A]
  3. Be concise and direct — lead with the answer, then supporting detail
  4. Prefer more recent filings (10-Q, 8-K) over annual data when both are present
  5. Acknowledge if the context does not contain enough information to answer

If the retrieved context does not answer the question, say so explicitly
rather than guessing or using general knowledge.
"""

RECENT_SYSTEM_PROMPT = """You are a financial research analyst specializing in recent SEC filings.

You answer questions based strictly on the provided excerpts from quarterly (10-Q)
and current event (8-K) SEC filings. These represent the company's most recent
reported performance and material disclosures.
Your answers must:
  1. Be grounded in the provided context — do not use outside knowledge
  2. Cite sources using [Ticker YEAR Filing-Type, Section] format after each claim
     e.g. [AAPL 2025 10-Q, MD&A] or [MSFT 2024 8-K, Item 2.02]
  3. Lead with the most recent data point available, then add context or trend
  4. Note the specific quarter or event date when it matters
  5. If the context covers multiple quarters, highlight the most recent figures
     and note any quarter-over-quarter changes visible in the data

If the retrieved context does not answer the question, say so explicitly
rather than guessing or using general knowledge.
"""


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

@dataclass
class FilingsAnswer:
    question:    str
    answer:      str
    ticker:      str | None
    sources:     list[dict]   # the chunks used as context
    model:       str


# ---------------------------------------------------------------------------
# Build context string from retrieved chunks
# ---------------------------------------------------------------------------

def build_context(chunks: list[dict]) -> str:
    """
    Format retrieved chunks into a context block for Claude.

    Each chunk is labeled with its source so Claude can cite it.
    The label format matches what we ask Claude to use in citations.
    """
    if not chunks:
        return "No relevant context found."

    parts = []
    for i, chunk in enumerate(chunks, start=1):
        filing_type = chunk.get("filing_type", "10-K")
        label = f"[{chunk['ticker']} {chunk['year']} {filing_type}, {chunk['section']}]"
        parts.append(
            f"--- Source {i}: {label} (relevance: {chunk['score']:.2f}) ---\n"
            f"{chunk['text']}\n"
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Search query rewriter
# ---------------------------------------------------------------------------

async def _make_search_query(question: str, ticker: str | None) -> str:
    """
    Converts a natural-language (possibly pronoun-heavy) question into a
    compact retrieval query suitable for embedding search.

    Example:
      "How does that compare to their gross margin trend?" + ticker=META
      → "META gross margin trend"
    """
    if not ticker:
        return question

    rewriter_model = "gpt-4o-mini"
    response = await get_openai_async().chat.completions.create(
        model=rewriter_model,
        max_completion_tokens=30,
        messages=[{
            "role": "user",
            "content": (
                f"Convert this financial question into a short search query (5-10 words) "
                f"for SEC filing retrieval.\n"
                f"Replace pronouns (it, they, their, that, this) with the ticker: {ticker}\n"
                f"Strip question words. Keep key financial terms.\n\n"
                f"Question: {question}\n"
                f"Search query:"
            ),
        }],
    )
    if response.usage:
        llm_tokens_total.labels(model=rewriter_model, token_type="input").inc(response.usage.prompt_tokens)
        llm_tokens_total.labels(model=rewriter_model, token_type="output").inc(response.usage.completion_tokens)
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# HyDE helpers
# ---------------------------------------------------------------------------

async def _hyde_query(question: str, ticker: str | None, recent_mode: bool) -> str | None:
    """
    Generate a short hypothetical SEC filing excerpt that would answer the question.
    Embedding this bridges the gap between question embeddings and declarative financial text.
    Returns None on any failure so callers fall back to the original query.
    """
    filing_hint = "recent 10-Q or 8-K quarterly report" if recent_mode else "SEC filing (10-K, 10-Q, or 8-K)"
    try:
        response = await get_openai_async().chat.completions.create(
            model=HYDE_MODEL,
            max_completion_tokens=80,
            messages=[{
                "role": "user",
                "content": (
                    f"Write 1-2 sentences from a {filing_hint} that would directly answer this question. "
                    f"Use realistic financial language and terminology. Do not invent specific numbers.\n\n"
                    f"Question: {question}\n"
                    + (f"Company ticker: {ticker}\n" if ticker else "")
                    + "Excerpt:"
                ),
            }],
        )
        if response.usage:
            llm_tokens_total.labels(model=HYDE_MODEL, token_type="input").inc(response.usage.prompt_tokens)
            llm_tokens_total.labels(model=HYDE_MODEL, token_type="output").inc(response.usage.completion_tokens)
        return response.choices[0].message.content.strip()
    except Exception:
        logger.warning("hyde_query failed, falling back to original search query", exc_info=True)
        return None


async def _retrieve_with_hyde(
    search_query: str,
    hyde_text:    str | None,
    db:           AsyncSession,
    ticker:       str | None,
    k:            int,
    filing_types: list[str] | None,
) -> list[dict]:
    """
    Retrieve chunks using both the rewritten search query and the HyDE text in parallel.
    Deduplicates by (ticker, year, section, text prefix), keeping the highest score per chunk.
    Falls back to plain retrieval when hyde_text is None.
    """
    if not hyde_text:
        return await retrieve_chunks(query=search_query, db=db, ticker=ticker, k=k, filing_types=filing_types)

    base_chunks, hyde_chunks = await asyncio.gather(
        retrieve_chunks(query=search_query, db=db, ticker=ticker, k=k, filing_types=filing_types),
        retrieve_chunks(query=hyde_text,    db=db, ticker=ticker, k=k, filing_types=filing_types),
    )

    seen: dict[tuple, dict] = {}
    for chunk in base_chunks + hyde_chunks:
        key = (chunk["ticker"], chunk["year"], chunk["section"], chunk["text"][:100])
        if key not in seen or chunk["score"] > seen[key]["score"]:
            seen[key] = chunk

    merged = sorted(seen.values(), key=lambda c: c["score"], reverse=True)[:k]
    logger.info(
        "filings_agent.hyde_merged",
        base=len(base_chunks), hyde=len(hyde_chunks), merged=len(merged),
    )
    return merged


# ---------------------------------------------------------------------------
# Core agent function
# ---------------------------------------------------------------------------
@traceable(name="filings-agent")
async def answer_filing_question(
    question:     str,
    db:           AsyncSession,
    ticker:       str | None = None,
    k:            int = 5,
    filing_types: list[str] | None = None,
    recent_mode:  bool = False,
) -> FilingsAnswer:
    """
    Retrieve relevant chunks and generate a grounded answer.

    Args:
        question:     User's question in plain English
        db:           SQLAlchemy async session
        ticker:       Optional ticker to restrict search to one company
        k:            Number of chunks to retrieve
        filing_types: Optional filter e.g. ["10-Q", "8-K"]
        recent_mode:  When True, uses a prompt focused on quarterly/event data
    """
    # ------------------------------------------------------------------
    # Step 1: Retrieve relevant chunks
    # ------------------------------------------------------------------
    search_query, hyde_text = await asyncio.gather(
        _make_search_query(question, ticker),
        _hyde_query(question, ticker, recent_mode),
    )
    logger.info(
        "filings_agent.retrieving",
        ticker=ticker,
        search_query=search_query,
        hyde_used=hyde_text is not None,
        filing_types=filing_types,
        k=k,
        recent_mode=recent_mode,
    )
    chunks = await _retrieve_with_hyde(
        search_query=search_query,
        hyde_text=hyde_text,
        db=db,
        ticker=ticker,
        k=k,
        filing_types=filing_types,
    )

    if not chunks:
        logger.info("filings_agent.no_chunks", ticker=ticker, recent_mode=recent_mode)
        msg = (
            "No recent quarterly or event filings (10-Q/8-K) were found for this company. "
            "Try asking about annual filings instead."
            if recent_mode else
            "I could not find relevant information in the SEC filings for this question."
        )
        return FilingsAnswer(question=question, answer=msg, ticker=ticker, sources=[], model=MODEL)

    logger.info("filings_agent.chunks_retrieved", ticker=ticker, chunk_count=len(chunks), top_score=round(chunks[0]["score"], 3))

    # ------------------------------------------------------------------
    # Step 2: Build prompt
    # ------------------------------------------------------------------
    context = build_context(chunks)
    system  = RECENT_SYSTEM_PROMPT if recent_mode else SYSTEM_PROMPT

    if recent_mode:
        user_message = (
            f"Based on the following recent SEC filing excerpts (10-Q quarterly reports "
            f"and 8-K current event reports), answer this question:\n\n"
            f"Question: {question}\n\n"
            f"Retrieved Context:\n{context}\n\n"
            f"Focus on the most recent data available. Cite each claim with "
            f"[Ticker YEAR Filing-Type, Section]. If the context is insufficient, say so explicitly."
        )
    else:
        user_message = (
            f"Based on the following SEC filing excerpts (10-K, 10-Q, 8-K), answer this question:\n\n"
            f"Question: {question}\n\n"
            f"Retrieved Context:\n{context}\n\n"
            f"Provide a clear, concise answer citing the specific sources above.\n"
            f"Use the format [Ticker YEAR Filing-Type, Section] for citations.\n"
            f"Prefer more recent quarterly (10-Q) or event (8-K) data over annual (10-K) data when relevant.\n"
            f"If the context is insufficient, say so explicitly."
        )

    # ------------------------------------------------------------------
    # Step 3: Generate answer with OpenAI
    # ------------------------------------------------------------------
    logger.info("filings_agent.generating", ticker=ticker, model=MODEL, recent_mode=recent_mode)
    response = await get_openai_async().chat.completions.create(
        model=MODEL,
        max_completion_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    )

    answer = response.choices[0].message.content
    llm_tokens_total.labels(model=MODEL, token_type="input").inc(response.usage.prompt_tokens)
    llm_tokens_total.labels(model=MODEL, token_type="output").inc(response.usage.completion_tokens)
    logger.info(
        "filings_agent.completed",
        ticker=ticker,
        tokens_in=response.usage.prompt_tokens,
        tokens_out=response.usage.completion_tokens,
    )

    return FilingsAnswer(question=question, answer=answer, ticker=ticker, sources=chunks, model=MODEL)


def make_filings_node(db: AsyncSession):
    """Factory that returns a filings_node with db captured in closure."""
    async def filings_node(state: AgentState) -> dict:
        try:
            route        = state.get("route", "")
            is_recent    = route == "filings_recent"
            filing_types = ["10-Q", "8-K"] if is_recent else None
            k            = RETRIEVAL_K_RECENT if is_recent else RETRIEVAL_K

            result = await answer_filing_question(
                question=state["question"],
                db=db,
                ticker=state.get("ticker"),
                k=k,
                filing_types=filing_types,
                recent_mode=is_recent,
            )
            return {
                "filings_output": result.answer,
                "citations":      result.sources,
            }
        except Exception as exc:
            return {**node_error("filings_output", "filings_agent", exc), "citations": []}

    return filings_node
