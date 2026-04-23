import logging
logging.basicConfig(level=logging.INFO)

from pathlib import Path
from app.rag.ingest import ingest_filing
from app.rag.chunker import chunk_filing
from app.rag.embedder import embed_chunks, get_client
import numpy as np

download_dir = Path("data/raw")
download_dir.mkdir(parents=True, exist_ok=True)

sections = ingest_filing("AAPL", 2024, download_dir)
print(f"\nExtracted {len(sections)} sections:")
for s in sections:
    print(f"  {s.section}: {len(s.text):,} chars")

chunks = chunk_filing(sections)
print(f"\nTotal chunks: {len(chunks)}")
print(f"\nFirst chunk:")
print(f"  section: {chunks[0].section}")
print(f"  index:   {chunks[0].chunk_index}")
print(f"  length:  {len(chunks[0].text)} chars")
print(f"  preview: {chunks[0].text[:300]}")
print(f"\nLast chunk:")
print(f"  section: {chunks[-1].section}")
print(f"  index:   {chunks[-1].chunk_index}")


client = get_client()
# Just test on first 3 chunks to save API cost
sample = chunks[:3]
vectors = embed_chunks(sample, client)

print(f"\nVector shape: {vectors.shape}")          # should be (3, 1536)
print(f"First vector norm: {np.linalg.norm(vectors[0]):.6f}")  # should be 1.000000
print(f"Sample values: {vectors[0][:5]}")          # 5 numbers between -1 and 1

# Sanity check — similarity of chunk 0 with itself should be 1.0
dot = np.dot(vectors[0], vectors[0])
print(f"Self-similarity of chunk 0: {dot:.6f}")    # should be 1.000000

# Similarity between chunk 0 and chunk 1
dot_01 = np.dot(vectors[0], vectors[1])
print(f"Similarity chunk 0 vs 1: {dot_01:.4f}")    # some value between 0 and 1