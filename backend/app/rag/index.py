"""
index.py — Build, persist, and query the FAISS vector index

Responsibilities:
  1. Build — take normalized vectors, add to IndexFlatIP, save to disk
  2. Load  — read index from disk on FastAPI startup
  3. Query — embed a question, search the index, return matching chunks
             from PostgreSQL
"""

import logging
from pathlib import Path

import faiss
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 1536                          # must match embedder.py
INDEX_PATH    = Path("data/faiss/sec_filings.index")


# ---------------------------------------------------------------------------
# Build — called once from the build_index script
# ---------------------------------------------------------------------------

def build_index(vectors: np.ndarray) -> faiss.IndexFlatIP:
    """
    Create a FAISS IndexFlatIP and add all vectors to it.

    IndexFlatIP:
      Flat = stores every vector exactly as-is, no compression
      IP   = Inner Product search

    Since vectors are L2-normalized coming in, IP = cosine similarity.
    Flat = exact search — always finds the true nearest neighbors.
    At 500-1000 vectors this is instant. No approximation needed.

    Args:
        vectors: np.ndarray of shape (N, 1536), already L2-normalized

    Returns:
        A populated FAISS index, ready to search
    """
    n_vectors, dim = vectors.shape
    logger.info(f"Building FAISS index: {n_vectors} vectors, dim={dim}")

    # Sanity check — vectors must be float32, FAISS will crash otherwise
    if vectors.dtype != np.float32:
        logger.warning("Casting vectors to float32")
        vectors = vectors.astype(np.float32)

    # Verify normalization — all norms should be ~1.0
    # If they're not, cosine similarity won't work correctly
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-5):
        logger.warning(
            f"Vectors are not normalized! "
            f"min_norm={norms.min():.4f}, max_norm={norms.max():.4f}. "
            f"Re-normalizing..."
        )
        vectors = vectors / norms[:, np.newaxis]

    # Create the index
    index = faiss.IndexFlatIP(dim)

    # Add all vectors in one shot
    # After this, index.ntotal == n_vectors
    index.add(vectors)

    logger.info(f"Index built. Total vectors: {index.ntotal}")
    return index


def save_index(index: faiss.IndexFlatIP, path: Path = INDEX_PATH) -> None:
    """Persist the FAISS index to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(path))
    logger.info(f"Index saved to {path} ({path.stat().st_size / 1024:.1f} KB)")


def load_index(path: Path = INDEX_PATH) -> faiss.IndexFlatIP:
    """
    Load the FAISS index from disk.
    Called once on FastAPI startup — index lives in RAM for the app lifetime.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"FAISS index not found at {path}. "
            f"Run scripts/build_index.py first."
        )
    index = faiss.read_index(str(path))
    logger.info(f"Loaded FAISS index from {path}. Vectors: {index.ntotal}")
    return index


# ---------------------------------------------------------------------------
# Query — called on every user request
# ---------------------------------------------------------------------------

def search_index(
    index: faiss.IndexFlatIP,
    query_vector: np.ndarray,    # shape (1536,) — single query
    k: int = 5,                  # how many results to return
) -> list[dict]:
    """
    Search the index for the k most similar vectors to query_vector.

    Returns a list of dicts:
      [
        {"faiss_index": 42, "score": 0.91},
        {"faiss_index": 7,  "score": 0.87},
        ...
      ]

    The faiss_index values are used to look up the actual chunk text
    from PostgreSQL. The score is cosine similarity (0 to 1, higher = better).
    """
    # query_vector comes in as shape (1536,) — FAISS needs (1, 1536)
    if query_vector.ndim == 1:
        query_vector = query_vector.reshape(1, -1)

    # Ensure float32
    if query_vector.dtype != np.float32:
        query_vector = query_vector.astype(np.float32)

    # Normalize the query vector too — critical, easy to forget
    norm = np.linalg.norm(query_vector)
    if norm > 0:
        query_vector = query_vector / norm

    # The actual search
    # scores shape: (1, k) — similarity scores, highest first
    # indices shape: (1, k) — positions in the index
    scores, indices = index.search(query_vector, k)

    # Flatten from (1, k) to (k,) since we only have one query
    scores  = scores[0]
    indices = indices[0]

    results = []
    for faiss_idx, score in zip(indices, scores):
        # FAISS returns -1 for empty slots when k > index.ntotal
        if faiss_idx == -1:
            continue
        results.append({
            "faiss_index": int(faiss_idx),
            "score":       float(score),
        })

    return results