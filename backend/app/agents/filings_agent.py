"""
filings_agent.py — RAG agent that answers questions about SEC filings

Flow:
  1. User asks a question (optionally specifying a ticker)
  2. Agent calls retrieve_chunks() to get relevant 10-K sections
  3. Agent passes retrieved chunks + question to Claude
  4. Claude synthesizes a grounded answer with citations

This is deliberately simple — one retrieval call, one generation call.
No multi-hop, no tool loop. That complexity comes in Phase 3 with LangGraph.
"""

import logging
import os
from dataclasses import dataclass
from langsmith import traceable

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.retrieval import retrieve_chunks
from langsmith.wrappers import wrap_anthropic

from ..state import AgentState


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL           = os.getenv("FILINGS_AGENT_MODEL", "claude-opus-4-5")
MAX_TOKENS      = int(os.getenv("FILINGS_AGENT_MAX_TOKENS", "1024"))
RETRIEVAL_K     = int(os.getenv("FILINGS_RETRIEVAL_K", "5"))

SYSTEM_PROMPT = """You are a financial research analyst specializing in SEC filings.

You answer questions based strictly on the provided 10-K excerpts.
Your answers must:
  1. Be grounded in the provided context — do not use outside knowledge
  2. Cite sources using [Ticker YEAR, Section] format after each claim
  3. Be concise and direct — lead with the answer, then supporting detail
  4. Acknowledge if the context does not contain enough information to answer

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
        label = f"[{chunk['ticker']} {chunk['year']}, {chunk['section']}]"
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
    question: str,
    db: AsyncSession,
    ticker: str | None = None,
    k: int = 5,
) -> FilingsAnswer:
    """
    Retrieve relevant chunks and generate a grounded answer.

    Args:
        question: User's question in plain English
        db:       SQLAlchemy async session
        ticker:   Optional ticker to restrict search to one company
        k:        Number of chunks to retrieve

    Returns:
        FilingsAnswer with the answer text and source chunks used
    """
    # ------------------------------------------------------------------
    # Step 1: Retrieve relevant chunks
    # ------------------------------------------------------------------
    logger.info(f"Retrieving chunks for: {question!r} ticker={ticker}")
    chunks = await retrieve_chunks(
        query=question,
        db=db,
        ticker=ticker,
        k=k,
    )

    if not chunks:
        return FilingsAnswer(
            question=question,
            answer="I could not find relevant information in the SEC filings for this question.",
            ticker=ticker,
            sources=[],
            model=MODEL,
        )

    logger.info(f"Retrieved {len(chunks)} chunks. Top score: {chunks[0]['score']:.3f}")

    # ------------------------------------------------------------------
    # Step 2: Build prompt
    # ------------------------------------------------------------------
    context = build_context(chunks)

    user_message = f"""Based on the following SEC 10-K excerpts, answer this question:

Question: {question}

Retrieved Context:
{context}

Provide a clear, concise answer citing the specific sources above.
Use the format [Ticker YEAR, Section] for citations.
If the context is insufficient, say so explicitly."""

    # ------------------------------------------------------------------
    # Step 3: Generate answer with Claude
    # ------------------------------------------------------------------
    client = wrap_anthropic(anthropic.Anthropic())

    logger.info(f"Generating answer with {MODEL}...")
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_message}
        ],
    )

    answer = response.content[0].text
    logger.info(f"Answer generated. Input tokens: {response.usage.input_tokens}, "
                f"Output tokens: {response.usage.output_tokens}")

    return FilingsAnswer(
        question=question,
        answer=answer,
        ticker=ticker,
        sources=chunks,
        model=MODEL,
    )

def make_filings_node(db: AsyncSession):
    """Factory that returns a filings_node with db captured in closure."""
    async def filings_node(state: AgentState) -> dict:
        result = await answer_filing_question(
            question=state["question"],
            db=db,
            ticker=state.get("ticker"),
            k=RETRIEVAL_K,
        )
        return {
            "filings_output": result.answer,
            "citations": result.sources,
        }
    return filings_node