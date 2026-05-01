"""
embedder.py — Convert text chunks into vectors using OpenAI's embedding API

Key concepts implemented here:
  1. Batching    — embed many chunks in one API call, not one-by-one
  2. Retry logic — embedding API can rate-limit; we back off and retry
  3. Normalization — divide each vector by its length so cosine similarity
                     works correctly with FAISS IndexFlatIP
"""

import logging
import time
import random

import numpy as np
from openai import OpenAI

from app.rag.chunker import ChunkRecord
from langsmith.wrappers import wrap_openai
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()
# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIM   = 1536       # projected via Matryoshka — fits pgvector HNSW 2000-dim limit
MAX_RETRIES  = 6
RETRY_DELAY  = 60    # seconds — OpenAI rate limits reset every 60 seconds
BATCH_SIZE   = 50    # reduce from 100 to 50 — smaller batches = less likely to hit TPM limit


# ---------------------------------------------------------------------------
# Client — one instance shared across calls
# ---------------------------------------------------------------------------

def get_client() -> OpenAI:
    """
    Returns an OpenAI client. Reads OPENAI_API_KEY from environment
    automatically — no need to pass it explicitly.
    """
    return wrap_openai(OpenAI())


# ---------------------------------------------------------------------------
# Core embedding function
# ---------------------------------------------------------------------------

def embed_texts(texts: list[str], client: OpenAI) -> np.ndarray:
    """
    Embed a list of texts and return a 2D numpy array of shape:
      (len(texts), EMBEDDING_DIM)

    Each row is one embedding vector, L2-normalized to unit length.

    Why batching matters:
      - One API call with 100 texts = ~200ms
      - 100 separate API calls      = ~20 seconds + 100x the overhead
    """
    all_vectors = []

    for batch_start in range(0, len(texts), BATCH_SIZE):
        batch = texts[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE

        logger.info(f"  Embedding batch {batch_num}/{total_batches} "
                    f"({len(batch)} texts)...")

        for attempt in range(MAX_RETRIES):
            try:
                response = client.embeddings.create(
                    model=EMBEDDING_MODEL,
                    input=batch,
                    dimensions=EMBEDDING_DIM,
                )
                batch_vectors = [item.embedding for item in response.data]
                all_vectors.extend(batch_vectors)

                # Small delay between every batch — proactive throttling
                # prevents hitting the rate limit in the first place
                time.sleep(1.5)
                break

            except Exception as e:
                error_str = str(e).lower()

                # Rate limit — wait longer, then retry
                if "rate limit" in error_str or "429" in error_str:
                    # Exponential backoff with jitter
                    # attempt 0 → 60s, attempt 1 → 120s, attempt 2 → 240s
                    wait = RETRY_DELAY * (2 ** attempt) + random.uniform(0, 5)
                    logger.warning(
                        f"  Rate limited on batch {batch_num}. "
                        f"Waiting {wait:.0f}s before retry "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})..."
                    )
                    time.sleep(wait)

                # Transient server error — short wait, retry
                elif "500" in error_str or "503" in error_str:
                    wait = 10 * (attempt + 1)
                    logger.warning(
                        f"  Server error on batch {batch_num}. "
                        f"Waiting {wait}s... (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(wait)

                # Unknown error — log and retry with short wait
                else:
                    if attempt == MAX_RETRIES - 1:
                        logger.error(f"  Batch {batch_num} failed after {MAX_RETRIES} attempts: {e}")
                        raise
                    wait = 10
                    logger.warning(f"  Unknown error: {e}. Waiting {wait}s...")
                    time.sleep(wait)

    # Convert to numpy array: shape (N, 1536)
    vectors = np.array(all_vectors, dtype=np.float32)

    # L2 normalize — each vector divided by its own length
    # After this, every vector has length exactly 1.0
    # This makes IndexFlatIP equivalent to cosine similarity
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)  # shape (N, 1)
    norms = np.where(norms == 0, 1, norms)  # avoid division by zero
    vectors = vectors / norms

    logger.info(f"  Normalized {len(vectors)} vectors, "
                f"shape={vectors.shape}, dtype={vectors.dtype}")

    return vectors


# ---------------------------------------------------------------------------
# Embed a list of ChunkRecords
# ---------------------------------------------------------------------------

def embed_chunks(chunks: list[ChunkRecord], client: OpenAI) -> np.ndarray:
    """
    Embed all chunks and return vectors in the same order as the input.

    Returns np.ndarray of shape (len(chunks), EMBEDDING_DIM).
    The i-th row corresponds to chunks[i].
    """
    logger.info(f"Embedding {len(chunks)} chunks...")

    texts = [chunk.text for chunk in chunks]
    vectors = embed_texts(texts, client)

    logger.info(f"Done. Vector matrix shape: {vectors.shape}")
    return vectors