"""
retrieval.py — pgvector-based semantic search over SEC filing chunks

Flow:
  1. Embed the user's query using the same model used at ingest time
  2. Run a cosine-distance query against the chunks table via pgvector
  3. Return the top-k chunks with metadata and similarity scores

Ticker filtering is a native WHERE clause — no overfetch or post-filtering needed.
"""

import logging
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import Chunk
from ..rag.embedder import EMBEDDING_MODEL, EMBEDDING_DIM
from ..clients import get_openai_sync

logger = logging.getLogger(__name__)


async def retrieve_chunks(
    query:           str,
    db:              AsyncSession,
    ticker:          Optional[str] = None,
    k:               int = 5,
    filing_types:    list[str] | None = None,
    score_threshold: float = 0.65,
) -> list[dict]:
    """
    Embed query → pgvector cosine search → return top-k chunks above threshold.

    Args:
        query:           The user's question in plain English
        db:              SQLAlchemy async session
        ticker:          Optional — restrict results to one company e.g. "AAPL"
        k:               How many chunks to return (before threshold filtering)
        score_threshold: Discard chunks with cosine similarity below this value

    Returns:
        List of dicts with text, ticker, year, section, score (0–1).
    """
    response = get_openai_sync().embeddings.create(model=EMBEDDING_MODEL, input=query, dimensions=EMBEDDING_DIM)
    query_vector = response.data[0].embedding

    distance_expr = Chunk.embedding.cosine_distance(query_vector)
    stmt = (
        select(Chunk, distance_expr.label("distance"))
        .order_by(distance_expr)
        .limit(k)
    )
    if ticker:
        stmt = stmt.where(Chunk.ticker == ticker.upper())
    if filing_types:
        stmt = stmt.where(Chunk.filing_type.in_(filing_types))

    result = await db.execute(stmt)
    rows   = result.all()

    if not rows:
        logger.warning("pgvector returned no results for query=%r ticker=%r", query, ticker)
        return []

    chunks = [
        {
            "text":         row.Chunk.text,
            "ticker":       row.Chunk.ticker,
            "year":         row.Chunk.year,
            "section":      row.Chunk.section,
            "filing_type":  row.Chunk.filing_type,
            "score":        round(1 - float(row.distance), 4),
        }
        for row in rows
    ]

    filtered = [c for c in chunks if c["score"] >= score_threshold]
    n_dropped = len(chunks) - len(filtered)
    if n_dropped:
        logger.info(
            "[retrieval] dropped %d low-quality chunk(s) below threshold=%.2f (kept %d/%d)",
            n_dropped, score_threshold, len(filtered), len(chunks),
        )

    logger.info(
        "[retrieval] query=%r ticker=%r k=%d threshold=%.2f top_score=%.3f returned=%d",
        query[:60], ticker, k, score_threshold,
        filtered[0]["score"] if filtered else 0, len(filtered),
    )
    return filtered


async def ticker_has_data(ticker: str, db: AsyncSession) -> bool:
    """Return True if the DB has any chunks for this ticker."""
    result = await db.execute(
        select(func.count()).select_from(Chunk).where(Chunk.ticker == ticker.upper())
    )
    return (result.scalar() or 0) > 0


async def ticker_has_recent_data(ticker: str, db: AsyncSession) -> bool:
    """Return True if the DB has 10-Q or 8-K chunks for this ticker."""
    result = await db.execute(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.ticker == ticker.upper())
        .where(Chunk.filing_type.in_(["10-Q", "8-K"]))
    )
    return (result.scalar() or 0) > 0


def format_retrieval_response(chunks: list[dict]) -> dict:
    return {
        "total":  len(chunks),
        "chunks": [
            {
                "rank":    i + 1,
                "score":   chunk["score"],
                "ticker":  chunk["ticker"],
                "year":    chunk["year"],
                "section": chunk["section"],
                "text":    chunk["text"][:500] + "..."
                           if len(chunk["text"]) > 500 else chunk["text"],
            }
            for i, chunk in enumerate(chunks)
        ],
    }
