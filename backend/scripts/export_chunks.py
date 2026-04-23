"""
Export all chunks to a text file so you can read them
and write good eval questions.

"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import AsyncSessionLocal, Chunk
from sqlalchemy import select


async def main():
    output = Path("data/chunks_export.txt")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Chunk).order_by(Chunk.ticker, Chunk.faiss_index)
        )
        chunks = result.scalars().all()

    lines = []
    for chunk in chunks:
        lines.append(
            f"{'='*60}\n"
            f"ID: {chunk.id} | FAISS: {chunk.faiss_index} | "
            f"{chunk.ticker} {chunk.year} | {chunk.section}\n"
            f"{'='*60}\n"
            f"{chunk.text}\n"
        )

    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported {len(chunks)} chunks to {output}")
    print(f"File size: {output.stat().st_size / 1024:.1f} KB")


asyncio.run(main())