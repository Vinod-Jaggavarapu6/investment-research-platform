"""
Find the best chunks to write eval questions about.
Prints the top 3 longest chunks per ticker/section combo
since longer chunks have more specific answerable content.

"""

import asyncio
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import AsyncSessionLocal, Chunk
from sqlalchemy import select


async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Chunk).order_by(Chunk.ticker, Chunk.section, Chunk.id)
        )
        chunks = result.scalars().all()

    # Group by ticker + section
    groups = defaultdict(list)
    for chunk in chunks:
        key = (chunk.ticker, chunk.section)
        groups[key].append(chunk)

    # Print top 2 chunks per group — these are your eval candidates
    output_lines = []
    for (ticker, section), group_chunks in sorted(groups.items()):
        # Sort by text length — longer = more specific content
        group_chunks.sort(key=lambda c: len(c.text), reverse=True)
        top = group_chunks[:2]

        for chunk in top:
            output_lines.append(
                f"\n{'='*60}\n"
                f"CHUNK_ID: {chunk.id} | "
                f"{ticker} {chunk.year} | {section}\n"
                f"LENGTH: {len(chunk.text)} chars\n"
                f"{'='*60}\n"
                f"{chunk.text[:600]}\n"
                f"...[truncated]\n"
            )

    output = Path("data/eval_candidates.txt")
    output.write_text("\n".join(output_lines), encoding="utf-8")
    print(f"Written {len(output_lines)} candidate chunks to {output}")
    print(f"Open data/eval_candidates.txt and write your eval questions")


asyncio.run(main())