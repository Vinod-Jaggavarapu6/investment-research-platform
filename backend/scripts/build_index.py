"""
build_index.py — Ingest pipeline: download → chunk → embed → store in PostgreSQL

Run once (or whenever you add new tickers / want fresher data):
  uv run python scripts/build_index.py

What it does:
  1. Downloads 10-K (annual), 10-Q (last 4 quarters), 8-K (last 6 events)
     for each configured ticker
  2. Parses and chunks each filing
  3. Embeds all chunks via OpenAI (with rate-limit handling + checkpointing)
  4. Stores chunks with embeddings in PostgreSQL (pgvector)
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import AsyncSessionLocal, Chunk, create_tables
from app.rag.ingest import ingest_filing, ingest_recent_filings
from app.rag.chunker import chunk_filing, ChunkRecord
from app.rag.embedder import embed_chunks, get_client, EMBEDDING_DIM

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "ORCL", "ADBE", "AMD",
    "INTC", "QCOM", "JPM", "LLY", "UNH",
    "PFE", "NKE", "CAT", "RTX", "LMT",
]

ANNUAL_AFTER     = "2023-01-01"  # floor date — fetches the single most recent 10-K
RECENT_10Q_LIMIT = 4             # most recent quarterly reports per ticker
RECENT_8K_LIMIT  = 6      # most recent material event reports per ticker
RECENT_AFTER     = "2024-01-01"   # don't look further back than this

DOWNLOAD_DIR  = Path("data/raw")
VECTORS_CACHE = Path("data/embed_cache/vectors_cache.npy")
CHUNKS_CACHE  = Path("data/embed_cache/chunks_cache.json")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
Path("data/embed_cache").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def save_progress(chunks: list[ChunkRecord], vectors: np.ndarray) -> None:
    np.save(str(VECTORS_CACHE), vectors)
    CHUNKS_CACHE.write_text(json.dumps([
        {
            "text": c.text, "ticker": c.ticker, "year": c.year,
            "section": c.section, "filing_type": c.filing_type,
            "chunk_index": c.chunk_index,
        }
        for c in chunks
    ], ensure_ascii=False))
    logger.info("  Progress saved: %d chunks", len(chunks))


def load_progress() -> tuple[list[ChunkRecord], np.ndarray] | None:
    if not VECTORS_CACHE.exists() or not CHUNKS_CACHE.exists():
        return None
    vectors     = np.load(str(VECTORS_CACHE))
    chunks_data = json.loads(CHUNKS_CACHE.read_text())
    chunks      = [ChunkRecord(**d) for d in chunks_data]
    done = sorted(set((c.ticker, c.filing_type) for c in chunks))
    logger.info("Found existing cache: %d chunks, done: %s", len(chunks), done)
    return chunks, vectors


def clear_cache() -> None:
    VECTORS_CACHE.unlink(missing_ok=True)
    CHUNKS_CACHE.unlink(missing_ok=True)
    logger.info("Checkpoint cache cleared")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

async def clear_chunks_for_ticker(ticker: str) -> None:
    from sqlalchemy import text
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("DELETE FROM chunks WHERE ticker = :ticker"),
            {"ticker": ticker},
        )
        await session.commit()
        logger.info("  Cleared %d existing chunks for %s", result.rowcount, ticker)


async def store_chunks(chunks: list[ChunkRecord], vectors: np.ndarray) -> None:
    """Bulk insert all chunks with their embeddings."""
    async with AsyncSessionLocal() as session:
        session.add_all([
            Chunk(
                text        = c.text,
                ticker      = c.ticker,
                year        = c.year,
                section     = c.section,
                filing_type = c.filing_type,
                embedding   = v.tolist(),
            )
            for c, v in zip(chunks, vectors)
        ])
        await session.commit()
    logger.info("  Stored %d chunks in PostgreSQL", len(chunks))


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.info("=" * 60)
    logger.info("Investment Research — Ingest Pipeline (10-K + 10-Q + 8-K)")
    logger.info("=" * 60)

    await create_tables()
    openai_client = get_client()

    # Always clear the embed cache at the start of a full rebuild.
    # Stale cache entries would re-use embeddings computed with the old
    # chunk content (different size / no prefix) — silently wrong results.
    clear_cache()

    # -------------------------------------------------------------------
    # Phase 1: Ingest all filing types for all tickers
    # -------------------------------------------------------------------
    logger.info("\n--- Phase 1: Ingesting filings ---")

    all_chunks: list[ChunkRecord] = []

    for ticker in TICKERS:
        logger.info("\n[%s]", ticker)

        # 10-K — annual report
        try:
            sections = ingest_filing(ticker, DOWNLOAD_DIR, after=ANNUAL_AFTER)
            chunks   = chunk_filing(sections)
            _assign_global_indices(chunks, len(all_chunks))
            all_chunks.extend(chunks)
            logger.info("  10-K: %d chunks", len(chunks))
        except Exception as e:
            logger.error("  10-K failed: %s", e)

        # 10-Q — last N quarterly reports
        try:
            sections = ingest_recent_filings(
                ticker, DOWNLOAD_DIR, "10-Q",
                limit=RECENT_10Q_LIMIT, after=RECENT_AFTER,
            )
            chunks = chunk_filing(sections) if sections else []
            _assign_global_indices(chunks, len(all_chunks))
            all_chunks.extend(chunks)
            logger.info("  10-Q: %d chunks", len(chunks))
        except Exception as e:
            logger.error("  10-Q failed: %s", e)

        # 8-K — last N material event reports
        try:
            sections = ingest_recent_filings(
                ticker, DOWNLOAD_DIR, "8-K",
                limit=RECENT_8K_LIMIT, after=RECENT_AFTER,
            )
            chunks = chunk_filing(sections) if sections else []
            _assign_global_indices(chunks, len(all_chunks))
            all_chunks.extend(chunks)
            logger.info("  8-K: %d chunks", len(chunks))
        except Exception as e:
            logger.error("  8-K failed: %s", e)

    if not all_chunks:
        logger.error("No chunks produced. Exiting.")
        return

    type_counts = {}
    for c in all_chunks:
        type_counts[c.filing_type] = type_counts.get(c.filing_type, 0) + 1
    logger.info(
        "\nPhase 1 complete: %d total chunks — %s",
        len(all_chunks),
        " | ".join(f"{k}: {v}" for k, v in sorted(type_counts.items())),
    )

    # -------------------------------------------------------------------
    # Phase 2: Embed per (ticker, filing_type) with checkpointing
    # -------------------------------------------------------------------
    logger.info("\n--- Phase 2: Embedding chunks ---")

    cached = load_progress()
    if cached:
        completed_chunks, completed_vectors = cached
        done_keys = set((c.ticker, c.filing_type) for c in completed_chunks)
    else:
        completed_chunks  = []
        completed_vectors = np.empty((0, EMBEDDING_DIM), dtype=np.float32)
        done_keys         = set()

    for ticker in TICKERS:
        for filing_type in ("10-K", "10-Q", "8-K"):
            if (ticker, filing_type) in done_keys:
                logger.info("  Skipping %s %s — already in cache", ticker, filing_type)
                continue

            batch = [
                c for c in all_chunks
                if c.ticker == ticker and c.filing_type == filing_type
            ]
            if not batch:
                continue

            try:
                logger.info("  Embedding %d chunks for %s %s...", len(batch), ticker, filing_type)
                vectors = embed_chunks(batch, openai_client)
                completed_chunks.extend(batch)
                completed_vectors = np.vstack([completed_vectors, vectors])
                save_progress(completed_chunks, completed_vectors)
                time.sleep(2)   # courtesy delay between batches
            except Exception as e:
                logger.error("  Failed %s %s: %s\n  Re-run to resume.", ticker, filing_type, e)
                return

    all_chunks = completed_chunks
    vectors    = completed_vectors
    logger.info(
        "\nPhase 2 complete: %d chunks embedded, shape=%s",
        len(all_chunks), vectors.shape,
    )

    # -------------------------------------------------------------------
    # Phase 3: Store in PostgreSQL with embeddings
    # -------------------------------------------------------------------
    logger.info("\n--- Phase 3: Storing in PostgreSQL ---")

    for ticker in set(c.ticker for c in all_chunks):
        await clear_chunks_for_ticker(ticker)

    await store_chunks(all_chunks, vectors)
    clear_cache()

    logger.info("\n" + "=" * 60)
    logger.info("Build complete!")
    logger.info("  Tickers  : %d", len(set(c.ticker for c in all_chunks)))
    logger.info("  Chunks   : %d", len(all_chunks))
    for ft, count in sorted(type_counts.items()):
        logger.info("  %-6s : %d chunks", ft, count)
    logger.info("=" * 60)


def _assign_global_indices(chunks: list[ChunkRecord], offset: int) -> None:
    for i, chunk in enumerate(chunks):
        chunk.chunk_index = offset + i


if __name__ == "__main__":
    asyncio.run(main())
