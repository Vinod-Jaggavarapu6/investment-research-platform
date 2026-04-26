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

import logging
import os
from dataclasses import dataclass
from langsmith import traceable

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.retrieval import retrieve_chunks
from langsmith.wrappers import wrap_openai

from ..state import AgentState


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL              = os.getenv("FILINGS_AGENT_MODEL", "gpt-4o")
MAX_TOKENS         = int(os.getenv("FILINGS_AGENT_MAX_TOKENS", "2048"))
RETRIEVAL_K        = int(os.getenv("FILINGS_RETRIEVAL_K", "5"))
RETRIEVAL_K_RECENT = int(os.getenv("FILINGS_RETRIEVAL_K_RECENT", "8"))

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
    logger.info(
        "Retrieving chunks for: %r ticker=%r filing_types=%r k=%d",
        question, ticker, filing_types, k,
    )
    chunks = await retrieve_chunks(
        query=question,
        db=db,
        ticker=ticker,
        k=k,
        filing_types=filing_types,
    )

    if not chunks:
        msg = (
            "No recent quarterly or event filings (10-Q/8-K) were found for this company. "
            "Try asking about annual filings instead."
            if recent_mode else
            "I could not find relevant information in the SEC filings for this question."
        )
        return FilingsAnswer(question=question, answer=msg, ticker=ticker, sources=[], model=MODEL)

    logger.info("Retrieved %d chunks. Top score: %.3f", len(chunks), chunks[0]["score"])

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
    client = wrap_openai(AsyncOpenAI())

    logger.info("Generating answer with %s (recent_mode=%s)...", MODEL, recent_mode)
    response = await client.chat.completions.create(
        model=MODEL,
        max_completion_tokens=MAX_TOKENS,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    )

    answer = response.choices[0].message.content
    logger.info(
        "Answer generated. input_tokens=%d output_tokens=%d",
        response.usage.prompt_tokens, response.usage.completion_tokens,
    )

    return FilingsAnswer(question=question, answer=answer, ticker=ticker, sources=chunks, model=MODEL)


def make_filings_node(db: AsyncSession):
    """Factory that returns a filings_node with db captured in closure."""
    async def filings_node(state: AgentState) -> dict:
        route       = state.get("route", "")
        is_recent   = route == "filings_recent"
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
    return filings_node
