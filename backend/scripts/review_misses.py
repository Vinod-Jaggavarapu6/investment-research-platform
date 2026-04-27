"""
For each top-5 miss, print the retrieved chunks so you can decide
whether to add them to gold_faiss_indices.

Run: uv run python scripts/review_misses.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import AsyncSessionLocal, Chunk
from sqlalchemy import select


EVAL_RESULTS = Path("data/eval_results.json")
EVAL_SET     = Path("data/eval_set.json")


async def main():
    results = json.loads(EVAL_RESULTS.read_text())
    eval_set = json.loads(EVAL_SET.read_text())
    eval_map = {q["id"]: q for q in eval_set}

    misses = [r for r in results["per_question"] if not r["hit_at_5"]]
    print(f"{len(misses)} top-5 misses to review\n")

    async with AsyncSessionLocal() as session:
        for miss in misses:
            qid      = miss["id"]
            question = eval_map[qid]

            print(f"\n{'='*65}")
            print(f"Q{qid}: {miss['question']}")
            print(f"Gold indices: {miss['gold_indices']}")
            print(f"{'='*65}")

            # Fetch retrieved chunks so you can read them
            retrieved_indices = miss["retrieved"]
            # NOTE: retrieved_indices are now chunk.id values (not faiss positions).
            # Regenerate eval_set.json with build_index.py after migration.
            result = await session.execute(
                select(Chunk).where(Chunk.id.in_(retrieved_indices))
            )
            chunks = {c.id: c for c in result.scalars().all()}

            for rank, (idx, score) in enumerate(
                zip(retrieved_indices, miss["scores"]), start=1
            ):
                chunk = chunks.get(idx)
                if not chunk:
                    continue
                print(f"\n  Rank {rank} | FAISS {idx} | score={score:.3f} | "
                      f"{chunk.ticker} {chunk.year} | {chunk.section}")
                print(f"  {chunk.text[:400]}")
                print()

            print("  → Add any of these to gold_faiss_indices? (review manually)")


asyncio.run(main())