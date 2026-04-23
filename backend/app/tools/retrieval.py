"""
retrieval.py — FAISS retrieval wrapped as a tool the agent can call

Flow:
  1. Embed the user's query using the same model used at index time
  2. Search FAISS for the k most similar chunk vectors
  3. Look up the actual chunk text from PostgreSQL using faiss_index
  4. Return chunks with metadata and similarity scores

Two interfaces:
  - retrieve_chunks() — async function for direct use in FastAPI
  - retrieval_tool    — LangChain tool wrapper for the agent
"""

import logging
from typing import Optional

import numpy as np
from openai import OpenAI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Chunk
from app.rag.embedder import get_client, EMBEDDING_MODEL, EMBEDDING_DIM
from app.rag.index import search_index, load_index

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state — loaded once on FastAPI startup
# ---------------------------------------------------------------------------
# The FAISS index lives in RAM for the lifetime of the app.
# Loading it on every request would be unacceptably slow (disk read each time).

_index = None          # faiss.IndexFlatIP
_openai_client = None  # openai.OpenAI


def init_retrieval() -> None:
    """
    Load the FAISS index and OpenAI client into module-level variables.
    Call this once from FastAPI startup — not on every request.
    """
    global _index, _openai_client
    _index         = load_index()
    _openai_client = get_client()
    logger.info(f"Retrieval ready. Index has {_index.ntotal} vectors.")


# ---------------------------------------------------------------------------
# Core retrieval function
# ---------------------------------------------------------------------------

async def retrieve_chunks(
    query: str,
    db: AsyncSession,
    ticker: Optional[str] = None,   # filter to a specific company
    k: int = 5,                     # number of results to return
) -> list[dict]:
    """
    Embed query → search FAISS → fetch chunk text from PostgreSQL.

    Args:
        query:  The user's question in plain English
        db:     SQLAlchemy async session (injected by FastAPI)
        ticker: Optional — restrict results to one company e.g. "AAPL"
        k:      How many chunks to return (top-k by similarity)

    Returns:
        List of dicts, each containing:
          - text:        the chunk text
          - ticker:      company ticker
          - year:        filing year
          - section:     e.g. "Item 7"
          - score:       cosine similarity (0–1, higher = more relevant)
          - faiss_index: position in the FAISS index
    """
    if _index is None or _openai_client is None:
        raise RuntimeError(
            "Retrieval not initialized. Call init_retrieval() at startup."
        )

    # ------------------------------------------------------------------
    # Step 1: Embed the query
    # Same model, same normalization as at index time — critical.
    # If you embed with a different model, similarity scores are meaningless.
    # ------------------------------------------------------------------
    response = _openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
    )
    query_vector = np.array(
        response.data[0].embedding,
        dtype=np.float32
    )

    # Normalize — same as we did for chunk vectors at index time
    norm = np.linalg.norm(query_vector)
    if norm > 0:
        query_vector = query_vector / norm

    # ------------------------------------------------------------------
    # Step 2: Search FAISS
    # Returns list of {"faiss_index": int, "score": float}
    # We fetch more than k if ticker filtering is requested,
    # because some results may be filtered out by ticker.
    # ------------------------------------------------------------------
    fetch_k = k * 4 if ticker else k   # fetch extra to account for filtering
    faiss_results = search_index(_index, query_vector, k=fetch_k)

    if not faiss_results:
        logger.warning(f"FAISS returned no results for query: {query!r}")
        return []

    faiss_indices = [r["faiss_index"] for r in faiss_results]
    score_map     = {r["faiss_index"]: r["score"] for r in faiss_results}

    # ------------------------------------------------------------------
    # Step 3: Fetch chunk text from PostgreSQL
    # We use faiss_index as the bridge — it's unique per chunk and
    # matches the position in the FAISS index exactly.
    # ------------------------------------------------------------------
    stmt = select(Chunk).where(Chunk.faiss_index.in_(faiss_indices))

    # Apply optional ticker filter
    if ticker:
        stmt = stmt.where(Chunk.ticker == ticker.upper())

    result = await db.execute(stmt)
    db_chunks = result.scalars().all()

    if not db_chunks:
        logger.warning(
            f"No chunks found in PostgreSQL for faiss_indices={faiss_indices} "
            f"ticker={ticker}"
        )
        return []

    # ------------------------------------------------------------------
    # Step 4: Assemble results, sorted by similarity score
    # ------------------------------------------------------------------
    chunks_with_scores = [
        {
            "text":        chunk.text,
            "ticker":      chunk.ticker,
            "year":        chunk.year,
            "section":     chunk.section,
            "score":       round(score_map[chunk.faiss_index], 4),
            "faiss_index": chunk.faiss_index,
        }
        for chunk in db_chunks
    ]

    # Sort by score descending — highest similarity first
    chunks_with_scores.sort(key=lambda x: x["score"], reverse=True)

    # Return only the top k after filtering
    return chunks_with_scores[:k]


# ---------------------------------------------------------------------------
# FastAPI endpoint helper — formats results for the API response
# ---------------------------------------------------------------------------

def format_retrieval_response(chunks: list[dict]) -> dict:
    """
    Format retrieved chunks into a clean API response.
    Separates the text content from metadata for clarity.
    """
    return {
        "total":   len(chunks),
        "chunks": [
            {
                "rank":    i + 1,
                "score":   chunk["score"],
                "ticker":  chunk["ticker"],
                "year":    chunk["year"],
                "section": chunk["section"],
                "text":    chunk["text"][:500] + "..."   # preview only
                           if len(chunk["text"]) > 500
                           else chunk["text"],
            }
            for i, chunk in enumerate(chunks)
        ]
    }