"""
ingest.py — Download 10-K filings from SEC EDGAR and extract clean text

Flow:
  1. Use sec-edgar-downloader to fetch the 10-K HTML file
  2. Parse HTML with BeautifulSoup
  3. Extract only the sections we care about (Items 1, 1A, 7, 7A, 8)
  4. Return clean text per section, ready for chunking

Why only those sections?
  Item 1   — Business description (what the company does)
  Item 1A  — Risk factors (what could go wrong)
  Item 7   — MD&A (management's analysis of financial results)
  Item 7A  — Quantitative market risk (interest rate, FX exposure)
  Item 8   — Financial statements (the actual numbers)
"""

import re
import time
import logging
from pathlib import Path
from dataclasses import dataclass

from bs4 import BeautifulSoup
from sec_edgar_downloader import Downloader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structure for a parsed section
# ---------------------------------------------------------------------------

@dataclass
class FilingSection:
    ticker:  str
    year:    int
    section: str   # e.g. "Item 1A"
    text:    str   # clean extracted text


# ---------------------------------------------------------------------------
# Sections we want to extract
# ---------------------------------------------------------------------------

# Maps a canonical name → list of patterns to search for in the HTML
# SEC filings aren't consistent — "Item 1A" might appear as
# "ITEM 1A", "Item 1A.", "ITEM 1A." etc. We handle all variants.

# SECTIONS = {
#     "Item 1":  [r"item\s*1[\.\s]",         r"business"],
#     "Item 1A": [r"item\s*1a[\.\s]",        r"risk factors"],
#     "Item 7":  [r"item\s*7[\.\s]",         r"management.*discussion"],
#     "Item 7A": [r"item\s*7a[\.\s]",        r"quantitative.*qualitative"],
#     "Item 8":  [r"item\s*8[\.\s]",         r"financial statements"],
# }


# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------

def download_10k(ticker: str, year: int, download_dir: Path) -> Path:
    """
    Download the 10-K filing for a given ticker and fiscal year.
    Returns the path to the downloaded filing directory.

    sec-edgar-downloader saves files under:
      {download_dir}/sec-edgar-filings/{ticker}/10-K/...
    """
    dl = Downloader(
        company_name="Investment Research Agentic Platform",   # required by SEC fair-access policy
        email_address="vinodreddyjaggavarapu@gmail.com", # required by SEC — use any valid email
        download_folder=str(download_dir),
    )

    logger.info(f"Downloading 10-K for {ticker} year {year}...")

    # after=YYYY-01-01 and before=YYYY+1-01-01 narrows to that fiscal year
    dl.get(
        "10-K",
        ticker,
        after=f"{year}-01-01",
        before=f"{year + 1}-01-01",
        limit=1,                      # just the most recent filing in that window
    )

    # Find the downloaded file
    filing_dir = download_dir / "sec-edgar-filings" / ticker / "10-K"
    filings = sorted(filing_dir.iterdir())  # sorted = oldest first

    if not filings:
        raise FileNotFoundError(
            f"No 10-K found for {ticker} {year}. "
            f"Check the ticker and year are correct."
        )

    # The downloader creates one subdirectory per filing
    # Inside is either primary-document.html or full-submission.txt
    filing_subdir = filings[-1]  # take the most recent
    logger.info(f"Filing saved to: {filing_subdir}")
    return filing_subdir


def find_html_file(filing_dir: Path) -> Path:
    """
    Find or extract the main HTML document inside a filing directory.

    SEC EDGAR gives us two possible formats:
      1. primary-document.html  — clean, directly usable
      2. full-submission.txt    — raw EDGAR format, HTML embedded inside it

    For format 2, we extract the embedded HTML and write it to a new file.
    """
    # Case 1: clean HTML already exists
    primary = filing_dir / "primary-document.html"
    if primary.exists():
        return primary

    html_files = list(filing_dir.glob("*.html")) + list(filing_dir.glob("*.htm"))
    if html_files:
        return max(html_files, key=lambda f: f.stat().st_size)

    # Case 2: only full-submission.txt exists — extract HTML from it
    full_submission = filing_dir / "full-submission.txt"
    if full_submission.exists():
        return extract_html_from_submission(full_submission)

    raise FileNotFoundError(f"No usable filing found in {filing_dir}")


def extract_html_from_submission(submission_path: Path) -> Path:
    """
    Extract the primary HTML document from a full-submission.txt file.

    full-submission.txt structure:
      <SEC-DOCUMENT>
        <SEC-HEADER>...</SEC-HEADER>
        <DOCUMENT>
          <TYPE>10-K
          <FILENAME>aapl-20240928.htm
          <SEQUENCE>1
          <TEXT>
          <HTML>
            ...actual 10-K HTML content...
          </HTML>
          </TEXT>
        </DOCUMENT>
        <DOCUMENT>
          <TYPE>EX-21.1        ← exhibits we don't want
          ...
        </DOCUMENT>
      </SEC-DOCUMENT>

    Strategy: find the first <DOCUMENT> block where TYPE is 10-K,
    extract everything between <TEXT> and </TEXT>.
    """
    logger.info(f"Extracting HTML from full-submission.txt...")

    content = submission_path.read_text(encoding="utf-8", errors="ignore")

    # Split into individual document blocks
    # Each block starts with <DOCUMENT> and ends with </DOCUMENT>
    doc_blocks = re.split(r"<DOCUMENT>", content, flags=re.IGNORECASE)

    for block in doc_blocks:
        # Find the TYPE line — we want the 10-K document, not exhibits
        type_match = re.search(r"<TYPE>\s*(\S+)", block, re.IGNORECASE)
        if not type_match:
            continue

        doc_type = type_match.group(1).strip().upper()

        # Skip anything that isn't the primary 10-K
        # Exhibits look like: EX-21.1, EX-31.1, EX-32.1, R2.htm etc.
        if doc_type not in ("10-K", "10-K/A"):
            continue

        # Extract text between <TEXT> and </TEXT>
        text_match = re.search(
            r"<TEXT>(.*?)</TEXT>",
            block,
            re.IGNORECASE | re.DOTALL
        )
        if not text_match:
            continue

        extracted = text_match.group(1).strip()

        # Write to a new file next to full-submission.txt
        output_path = submission_path.parent / "primary-document.html"
        output_path.write_text(extracted, encoding="utf-8")

        logger.info(
            f"Extracted {len(extracted):,} chars → {output_path}"
        )
        return output_path

    raise ValueError(
        f"Could not find 10-K document block in {submission_path}. "
        f"The filing format may be unusual."
    )

# ---------------------------------------------------------------------------
# HTML Parser
# ---------------------------------------------------------------------------

def parse_10k(html_path: Path, ticker: str, year: int) -> list[FilingSection]:
    """
    Parse a 10-K HTML file and extract text for each target section.

    SEC 10-K HTML is notoriously messy:
      - Nested tables used for layout (not data)
      - Inline XBRL tags wrapping every number
      - Inconsistent heading formats
      - Some filings are scanned PDFs (we skip those)

    Strategy:
      1. Remove all tables (layout noise)
      2. Extract plain text
      3. Find section boundaries by searching for "Item X" headings
      4. Slice text between consecutive headings
    """
    logger.info(f"Parsing {html_path}")

    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")

    # --- Clean the HTML ---
    # Remove script, style, and XBRL inline tags (pure noise)
    for tag in soup(["script", "style", "ix:nonnumeric", "ix:nonfraction"]):
        tag.decompose()

    # Remove table tags but keep their text content
    # (tables in 10-Ks are mostly layout, not content we want to chunk)
    for table in soup.find_all("table"):
        table.replace_with(table.get_text(separator=" "))

    # Extract full plain text
    full_text = soup.get_text(separator="\n")

    # Collapse excessive whitespace
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)
    full_text = re.sub(r"[ \t]+", " ", full_text)

    logger.info(f"Extracted {len(full_text):,} characters of text")

    # --- Find section boundaries ---
    sections = extract_sections(full_text, ticker, year)
    logger.info(f"Extracted {len(sections)} sections for {ticker} {year}")

    return sections


# def extract_sections(
#     text: str,
#     ticker: str,
#     year: int
# ) -> list[FilingSection]:
#     """
#     Find where each Item starts in the text and slice out that section.

#     The challenge: "Item 1" appears multiple times in a 10-K —
#     once in the table of contents, and once as the actual section heading.
#     We skip table-of-contents occurrences by requiring a minimum text
#     length between sections.
#     """
#     # Find all positions of each section heading in the text
#     section_positions: dict[str, list[int]] = {}

#     for section_name, patterns in SECTIONS.items():
#         positions = []
#         for pattern in patterns:
#             for match in re.finditer(pattern, text, re.IGNORECASE):
#                 positions.append(match.start())
#         # Sort and deduplicate positions within 500 chars of each other
#         positions = sorted(set(positions))
#         section_positions[section_name] = positions

#     # Build a flat list of (position, section_name) sorted by position
#     all_hits: list[tuple[int, str]] = []
#     for section_name, positions in section_positions.items():
#         for pos in positions:
#             all_hits.append((pos, section_name))

#     all_hits.sort(key=lambda x: x[0])

#     if not all_hits:
#         logger.warning(f"No sections found for {ticker} {year}")
#         return []

#     # Slice text between consecutive section hits
#     # Skip hits that produce < 500 characters (table of contents entries)
#     results: list[FilingSection] = []
#     seen_sections: set[str] = set()

#     for i, (start_pos, section_name) in enumerate(all_hits):
#         if section_name in seen_sections:
#             continue  # already captured this section

#         # End of this section = start of next hit (or end of document)
#         end_pos = all_hits[i + 1][0] if i + 1 < len(all_hits) else len(text)
#         section_text = text[start_pos:end_pos].strip()

#         # Skip if too short — almost certainly a TOC entry
#         if len(section_text) < 500:
#             continue

#         seen_sections.add(section_name)
#         results.append(FilingSection(
#             ticker=ticker,
#             year=year,
#             section=section_name,
#             text=section_text,
#         ))
#         logger.info(
#             f"  {section_name}: {len(section_text):,} chars"
#         )

#     return results


def extract_sections(
    text: str,
    ticker: str,
    year: int,
) -> list[FilingSection]:
    """
    Find real section boundaries by:
    1. Matching Item headings including non-breaking spaces (\xa0)
    2. Requiring minimum text length to skip TOC hits
    3. Taking the LAST qualifying hit per section (always the real one,
       not the TOC reference which appears earlier in the document)
    """

    # Patterns that match how AAPL (and most filers) format headings:
    # "Item 1.\xa0\xa0\xa0\xa0Business" — \s+ matches both regular and non-breaking spaces
    SECTION_PATTERNS = {
        "Item 1":  r"Item\s+1\.\s+Business",
        "Item 1A": r"Item\s+1A\.\s+Risk\s+Factors",
        "Item 7":  r"Item\s+7\.\s+Management",
        "Item 7A": r"Item\s+7A\.\s+Quantitative",
        "Item 8":  r"Item\s+8\.\s+Financial\s+Statements",
    }

    # Find all matches for each section, pick the best one
    # "best" = last match that is followed by substantial text (> 2000 chars)
    # This reliably skips TOC entries which have very little text after them
    candidates: dict[str, int] = {}  # section_name -> position

    for section_name, pattern in SECTION_PATTERNS.items():
        matches = list(re.finditer(pattern, text, re.IGNORECASE))

        if not matches:
            logger.warning(f"No match found for {section_name} in {ticker} {year}")
            continue

        # Walk matches from last to first — real section is always later
        # in the document than TOC references
        for match in reversed(matches):
            pos = match.start()
            # Check how much text follows this match
            remaining = text[pos:pos + 3000]
            if len(remaining.strip()) > 2000:
                candidates[section_name] = pos
                break  # found the real section, stop looking

    if not candidates:
        logger.warning(f"No sections found for {ticker} {year}")
        return []

    # Sort found sections by their position in the document
    ordered = sorted(candidates.items(), key=lambda x: x[1])
    logger.info(f"Found {len(ordered)} sections: {[s for s,_ in ordered]}")

    # Slice text between consecutive section start positions
    results: list[FilingSection] = []
    for i, (section_name, start_pos) in enumerate(ordered):
        # End = start of next section, or end of document
        end_pos = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        section_text = text[start_pos:end_pos].strip()

        logger.info(f"  {section_name}: pos={start_pos:,} → {end_pos:,} "
                    f"({len(section_text):,} chars)")

        results.append(FilingSection(
            ticker=ticker,
            year=year,
            section=section_name,
            text=section_text,
        ))

    return results

# ---------------------------------------------------------------------------
# Main entry point — one ticker, one year
# ---------------------------------------------------------------------------

def ingest_filing(
    ticker: str,
    year: int,
    download_dir: Path,
) -> list[FilingSection]:
    """
    Full pipeline: download → parse → return sections.
    Call this from the build_index script.
    """
    ticker = ticker.upper()

    filing_dir  = download_10k(ticker, year, download_dir)
    html_file   = find_html_file(filing_dir)
    sections    = parse_10k(html_file, ticker, year)

    return sections