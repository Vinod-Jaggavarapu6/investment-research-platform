"""
chunker.py — Split FilingSection text into chunks ready for embedding

Input:  list[FilingSection]  (from ingest.py)
Output: list[ChunkRecord]    (text + metadata, ready to embed + store)
"""

import logging
from dataclasses import dataclass

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.ingest import FilingSection

logger = logging.getLogger(__name__)

@dataclass
class ChunkRecord:
    text:        str    
    ticker:      str   
    year:        int   
    section:     str   
    filing_type: str    # "10-K", "10-Q", or "8-K"
    chunk_index: int    # position within this filing (0, 1, 2, ...)


# ---------------------------------------------------------------------------
# Splitter config
# ---------------------------------------------------------------------------

# These numbers are the defaults we discussed:
#   chunk_size=800   — large enough for a complete financial thought
#   chunk_overlap=100 — insurance against bad boundary cuts
#
# One important note: LangChain's RecursiveCharacterTextSplitter measures
# in CHARACTERS not tokens by default. At ~4 chars per token:
#   800 tokens  ≈ 3200 chars
#   100 tokens  ≈  400 chars
#
# We use character counts here for simplicity. If you switch to a
# tiktoken-based splitter later, update these numbers directly to
# token counts (800 and 100).

CHUNK_SIZE    = 1600   # characters (~400 tokens) — tighter focus per chunk
CHUNK_OVERLAP =  200   # characters (~50 tokens)

# Separator priority — RecursiveCharacterTextSplitter tries these in order:
#   \n\n  paragraph break   (most preferred — keeps paragraphs together)
#   \n    line break
#   .     sentence boundary
#   " "   word boundary     (last resort)
SEPARATORS = ["\n\n", "\n", ".", " "]

# Prepended to every chunk so the embedding carries section identity.
# Shorter chunks lose the "where am I?" signal without this prefix.
SECTION_LABELS: dict[str, str] = {
    "Item 1":  "Business Description",
    "Item 1A": "Risk Factors",
    "Item 7":  "Management Discussion and Analysis",
    "Item 7A": "Quantitative Market Risk",
    "Item 8":  "Financial Statements",
}


def _section_prefix(section: "FilingSection") -> str:
    label = SECTION_LABELS.get(section.section, section.section)
    return f"[{section.ticker} {section.year} {section.filing_type} — {section.section}: {label}]\n\n"


def make_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=SEPARATORS,
        length_function=len,          # measure by character count
        is_separator_regex=False,     # treat separators as literal strings
    )


# ---------------------------------------------------------------------------
# Chunk a single section
# ---------------------------------------------------------------------------

def chunk_section(
    section: FilingSection,
    splitter: RecursiveCharacterTextSplitter,
    start_index: int,                 # global chunk counter offset
) -> list[ChunkRecord]:
    """
    Split one FilingSection into ChunkRecords.

    start_index is the global position counter — if AAPL Item 1
    produced chunks 0-12, then Item 1A starts at 13. This gives
    every chunk a unique position across the entire index, which
    is what faiss_index in Postgres stores.
    """
    raw_chunks = splitter.split_text(section.text)
    prefix = _section_prefix(section)

    records = []
    for i, text in enumerate(raw_chunks):
        # Skip chunks that are just whitespace or very short
        # (can happen at section boundaries)
        if len(text.strip()) < 100:
            logger.debug(f"Skipping short chunk ({len(text)} chars)")
            continue

        records.append(ChunkRecord(
            text=prefix + text.strip(),   # prefix after split — doesn't skew chunk boundaries
            ticker=section.ticker,
            year=section.year,
            section=section.section,
            filing_type=section.filing_type,
            chunk_index=start_index + len(records),
        ))

    logger.info(
        f"  {section.ticker} {section.year} {section.section}: "
        f"{len(raw_chunks)} raw → {len(records)} chunks"
    )
    return records


# ---------------------------------------------------------------------------
# Chunk an entire filing (all sections)
# ---------------------------------------------------------------------------

def chunk_filing(sections: list[FilingSection]) -> list[ChunkRecord]:
    """
    Chunk all sections of a filing, maintaining a global chunk counter
    so every ChunkRecord has a unique chunk_index across the full index.
    """

    splitter = make_splitter()
    all_chunks: list[ChunkRecord] = []

    for section in sections:
        section_chunks = chunk_section(
            section=section,
            splitter=splitter,
            start_index=len(all_chunks),  
        )
        all_chunks.extend(section_chunks)

    logger.info(
        f"Total chunks for {sections[0].ticker} {sections[0].year}: "
        f"{len(all_chunks)}"
    )
    return all_chunks