"""
background_ingest.py — On-demand ingest for tickers not yet in the database.

Triggered when the filings agent receives a query for an unknown ticker.
The download + embed work runs in a thread pool (all sync) so it never
blocks the async event loop.  The DB write is async.
"""

import asyncio
import logging
from pathlib import Path

import numpy as np

from ..database import AsyncSessionLocal, Chunk
from .chunker import chunk_filing
from .embedder import embed_chunks, get_client
from .ingest import ingest_filing, ingest_recent_filings

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = Path("data/raw")
RECENT_AFTER = "2024-01-01"

_ingesting: set[str] = set()


def is_ingesting(ticker: str) -> bool:
    return ticker.upper() in _ingesting


def trigger_ingest(ticker: str) -> None:
    """Fire-and-forget: start background ingest if not already running."""
    ticker = ticker.upper()
    if ticker in _ingesting:
        logger.info("[bg-ingest] Already ingesting %s — skipping duplicate trigger", ticker)
        return
    # Mark as ingesting synchronously before creating the task so that
    # is_ingesting() returns True immediately — no race window.
    _ingesting.add(ticker)
    asyncio.create_task(_ingest_task(ticker))
    logger.info("[bg-ingest] Triggered ingest for %s (task queued)", ticker)


async def _ingest_task(ticker: str) -> None:
    # _ingesting already contains ticker (added synchronously in trigger_ingest)
    logger.info("[bg-ingest] Task started for %s", ticker)
    try:
        chunks, vectors = await asyncio.to_thread(_sync_ingest, ticker)
        if not chunks:
            logger.warning("[bg-ingest] No chunks produced for %s — ingest may have failed", ticker)
            return
        await _store(chunks, vectors)
        logger.info("[bg-ingest] Completed for %s — %d chunks stored", ticker, len(chunks))
    except Exception as e:
        logger.error("[bg-ingest] Failed for %s: %s", ticker, e, exc_info=True)
    finally:
        _ingesting.discard(ticker)
        logger.info("[bg-ingest] Removed %s from in-progress set", ticker)


def _sync_ingest(ticker: str) -> tuple:
    """Download + chunk + embed — runs in a thread pool to avoid blocking the loop."""
    all_sections = []

    try:
        sections = ingest_filing(ticker, DOWNLOAD_DIR)
        all_sections.extend(sections)
        logger.info("[bg-ingest] %s 10-K: %d sections", ticker, len(sections))
    except Exception as e:
        logger.error("[bg-ingest] %s 10-K failed: %s", ticker, e)

    try:
        sections = ingest_recent_filings(ticker, DOWNLOAD_DIR, "10-Q", limit=4, after=RECENT_AFTER)
        all_sections.extend(sections or [])
        logger.info("[bg-ingest] %s 10-Q: %d sections", ticker, len(sections or []))
    except Exception as e:
        logger.error("[bg-ingest] %s 10-Q failed: %s", ticker, e)

    try:
        sections = ingest_recent_filings(ticker, DOWNLOAD_DIR, "8-K", limit=6, after=RECENT_AFTER)
        all_sections.extend(sections or [])
        logger.info("[bg-ingest] %s 8-K: %d sections", ticker, len(sections or []))
    except Exception as e:
        logger.error("[bg-ingest] %s 8-K failed: %s", ticker, e)

    if not all_sections:
        return [], None

    chunks = chunk_filing(all_sections)
    if not chunks:
        return [], None

    vectors = embed_chunks(chunks, get_client())
    return chunks, vectors


async def _store(chunks: list, vectors: np.ndarray) -> None:
    async with AsyncSessionLocal() as session:
        session.add_all([
            Chunk(
                text=c.text,
                ticker=c.ticker,
                year=c.year,
                section=c.section,
                filing_type=c.filing_type,
                embedding=v.tolist(),
            )
            for c, v in zip(chunks, vectors)
        ])
        await session.commit()
