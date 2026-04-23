"""
build_index.py — Full ingest pipeline: download → chunk → embed → index → store

Run once (or whenever you add new tickers):
  uv run python scripts/build_index.py

What it does:
  1. Downloads 10-K for each ticker/year pair
  2. Parses and chunks the filing
  3. Embeds all chunks via OpenAI (with rate limit handling + checkpointing)
  4. Builds a FAISS index and saves to disk
  5. Stores all chunk text + metadata in PostgreSQL
"""

import asyncio
import json
import logging
import random
import sys
import time
from pathlib import Path

import numpy as np
from openai import OpenAI

# make sure app/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import AsyncSessionLocal, Chunk, create_tables
from app.rag.ingest import ingest_filing
from app.rag.chunker import chunk_filing, ChunkRecord
from app.rag.embedder import embed_chunks, get_client, EMBEDDING_DIM
from app.rag.index import build_index, save_index

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TICKERS = [
    ("AAPL", 2025),
    ("MSFT", 2025),
    ("GOOGL", 2025),
    ("AMZN", 2025),
    ("NVDA", 2025),
    ("META", 2025),
    ("TSLA", 2025),
    ("ORCL", 2025),
    ("ADBE", 2025),
    ("AMD", 2025),
    ("INTC", 2025),
    ("QCOM", 2025),
    ("JPM", 2025),
    ("LLY", 2025),
    ("UNH", 2025),
    ("PFE", 2025),
    ("NKE", 2025),
    ("CAT", 2025),
    ("RTX", 2025),
    ("LMT", 2025),
]

DOWNLOAD_DIR  = Path("data/raw")
VECTORS_CACHE = Path("data/faiss/vectors_cache.npy")
CHUNKS_CACHE  = Path("data/faiss/chunks_cache.json")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path("data/faiss").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Checkpoint helpers — save/load progress so we can resume after interruption
# ---------------------------------------------------------------------------

def save_progress(chunks: list[ChunkRecord], vectors: np.ndarray) -> None:
    """
    Save chunks and vectors to disk after each ticker.
    If the script is interrupted, re-running it will resume from here
    instead of re-embedding everything from scratch.
    """
    np.save(str(VECTORS_CACHE), vectors)

    chunks_data = [
        {
            "text":        c.text,
            "ticker":      c.ticker,
            "year":        c.year,
            "section":     c.section,
            "chunk_index": c.chunk_index,
        }
        for c in chunks
    ]
    CHUNKS_CACHE.write_text(json.dumps(chunks_data, ensure_ascii=False))
    logger.info(f"  Progress saved: {len(chunks)} chunks, {vectors.shape} vectors")

def load_progress() -> tuple[list[ChunkRecord], np.ndarray] | None:
    """
    Load previously saved progress.
    Returns None if no cache exists (fresh run).
    """
    if not VECTORS_CACHE.exists() or not CHUNKS_CACHE.exists():
        return None

    vectors     = np.load(str(VECTORS_CACHE))
    chunks_data = json.loads(CHUNKS_CACHE.read_text())
    chunks      = [ChunkRecord(**d) for d in chunks_data]

    logger.info(
        f"Found existing cache: {len(chunks)} chunks already embedded "
        f"across tickers: {sorted(set(c.ticker for c in chunks))}"
    )
    return chunks, vectors

def clear_cache() -> None:
    """Delete checkpoint files after a successful full build."""
    VECTORS_CACHE.unlink(missing_ok=True)
    CHUNKS_CACHE.unlink(missing_ok=True)
    logger.info("Checkpoint cache cleared")


# ---------------------------------------------------------------------------
# Embed one ticker at a time with rate limit handling
# ---------------------------------------------------------------------------

def embed_ticker_chunks(
    chunks: list[ChunkRecord],
    client: OpenAI,
    ticker: str,
) -> np.ndarray:
    """
    Embed all chunks for a single ticker.

    Wraps embed_chunks with extra logging so you can see
    per-ticker progress clearly in the logs.
    """
    logger.info(f"  Embedding {len(chunks)} chunks for {ticker}...")
    vectors = embed_chunks(chunks, client)
    logger.info(f"  {ticker} embedding complete. Shape: {vectors.shape}")
    return vectors


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

async def clear_existing_chunks(ticker: str, year: int) -> None:
    """
    Delete existing rows for this ticker/year before re-inserting.
    Makes the script safe to re-run without creating duplicates.
    """
    from sqlalchemy import text
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("DELETE FROM chunks WHERE ticker = :ticker AND year = :year"),
            {"ticker": ticker, "year": year}
        )
        await session.commit()
        logger.info(
            f"  Cleared {result.rowcount} existing chunks "
            f"for {ticker} {year}"
        )
async def store_chunks(chunks: list[ChunkRecord]) -> None:
    """
    Bulk insert all chunks into PostgreSQL.

    faiss_index stores the position in the FAISS index so we can
    look up the full chunk text after a FAISS search returns indices.
    """
    async with AsyncSessionLocal() as session:
        db_chunks = [
            Chunk(
                text=chunk.text,
                ticker=chunk.ticker,
                year=chunk.year,
                section=chunk.section,
                faiss_index=chunk.chunk_index,
            )
            for chunk in chunks
        ]
        session.add_all(db_chunks)
        await session.commit()
    logger.info(f"  Stored {len(db_chunks)} chunks in PostgreSQL")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.info("=" * 60)
    logger.info("Investment Research — RAG Index Builder")
    logger.info("=" * 60)

    # Ensure chunks table exists
    await create_tables()

    openai_client = get_client()

    # -------------------------------------------------------------------
    # Phase 1: Ingest + chunk all tickers
    # -------------------------------------------------------------------
    logger.info("\n--- Phase 1: Ingesting filings ---")

    all_chunks: list[ChunkRecord] = []

    for ticker, year in TICKERS:
        logger.info(f"\nIngesting {ticker} {year}...")
        try:
            sections = ingest_filing(ticker, year, DOWNLOAD_DIR)
            if not sections:
                logger.warning(f"  No sections found for {ticker} {year} — skipping")
                continue

            chunks = chunk_filing(sections)
            if not chunks:
                logger.warning(f"  No chunks for {ticker} {year} — skipping")
                continue

            # Assign globally unique chunk_index values across all tickers
            # so every chunk maps to a unique FAISS index position
            offset = len(all_chunks)
            for chunk in chunks:
                chunk.chunk_index = offset + chunk.chunk_index

            all_chunks.extend(chunks)
            logger.info(
                f"  {ticker} {year}: {len(chunks)} chunks "
                f"(global indices {offset}–{len(all_chunks) - 1})"
            )

        except Exception as e:
            logger.error(f"  Failed to ingest {ticker} {year}: {e}")
            continue

    if not all_chunks:
        logger.error("No chunks produced across any ticker. Exiting.")
        return

    total_tickers = len(set(c.ticker for c in all_chunks))
    logger.info(
        f"\nPhase 1 complete: {len(all_chunks)} total chunks "
        f"across {total_tickers} tickers"
    )

    # -------------------------------------------------------------------
    # Phase 2: Embed per ticker with checkpointing
    # -------------------------------------------------------------------
    logger.info("\n--- Phase 2: Embedding chunks ---")

    # Check for existing progress from a previous interrupted run
    cached = load_progress()
    if cached:
        completed_chunks, completed_vectors = cached
        done_tickers = set(c.ticker for c in completed_chunks)
        logger.info(f"Resuming. Already done: {done_tickers}")
    else:
        completed_chunks  = []
        completed_vectors = np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        done_tickers      = set()

    # Embed one ticker at a time — save after each so we can resume
    for ticker, year in TICKERS:
        if ticker in done_tickers:
            logger.info(f"Skipping {ticker} — already in checkpoint cache")
            continue

        ticker_chunks = [
            c for c in all_chunks
            if c.ticker == ticker and c.year == year
        ]
        if not ticker_chunks:
            continue

        try:
            ticker_vectors = embed_ticker_chunks(
                ticker_chunks, openai_client, ticker
            )

            completed_chunks.extend(ticker_chunks)
            completed_vectors = np.vstack([completed_vectors, ticker_vectors])

            # Save progress immediately after each ticker
            save_progress(completed_chunks, completed_vectors)
            logger.info(
                f"  Checkpoint saved. Running total: "
                f"{len(completed_chunks)} chunks"
            )

            # Courtesy delay between tickers to stay under rate limits
            time.sleep(2)

        except Exception as e:
            logger.error(
                f"  Failed to embed {ticker}: {e}\n"
                f"  Progress saved up to this point. "
                f"Re-run the script to resume from {ticker}."
            )
            return   # exit — re-running will resume from checkpoint

    # All tickers embedded successfully
    vectors    = completed_vectors
    all_chunks = completed_chunks

    logger.info(
        f"\nPhase 2 complete: {len(all_chunks)} chunks embedded, "
        f"vectors shape={vectors.shape}"
    )

    # -------------------------------------------------------------------
    # Phase 3: Build and save FAISS index
    # -------------------------------------------------------------------
    logger.info("\n--- Phase 3: Building FAISS index ---")

    index = build_index(vectors)
    save_index(index)

    logger.info(f"  FAISS index: {index.ntotal} vectors")

    # -------------------------------------------------------------------
    # Phase 4: Store in PostgreSQL
    # -------------------------------------------------------------------
    logger.info("\n--- Phase 4: Storing chunks in PostgreSQL ---")

    # Clear old data for each ticker before inserting fresh
    for ticker, year in TICKERS:
        ticker_in_chunks = any(
            c.ticker == ticker for c in all_chunks
        )
        if ticker_in_chunks:
            await clear_existing_chunks(ticker, year)

    await store_chunks(all_chunks)

    # -------------------------------------------------------------------
    # Cleanup + summary
    # -------------------------------------------------------------------
    clear_cache()

    logger.info("\n" + "=" * 60)
    logger.info("Build complete!")
    logger.info(f"  Tickers processed : {total_tickers}")
    logger.info(f"  Total chunks      : {len(all_chunks)}")
    logger.info(f"  FAISS vectors     : {index.ntotal}")
    logger.info(f"  Index saved to    : data/faiss/sec_filings.index")
    logger.info(f"  Chunks in Postgres: {len(all_chunks)}")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())