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
import logging
from pathlib import Path
from dataclasses import dataclass

from bs4 import BeautifulSoup
from sec_edgar_downloader import Downloader

logger = logging.getLogger(__name__)

@dataclass
class FilingSection:
    ticker:      str
    year:        int
    section:     str         
    text:        str         
    filing_type: str = "10-K"


# ---------------------------------------------------------------------------
# Sections we want to extract
# ---------------------------------------------------------------------------

# Maps a canonical name → list of patterns to search for in the HTML
# SEC filings aren't consistent — "Item 1A" might appear as
# "ITEM 1A", "Item 1A.", "ITEM 1A." etc. We handle all variants.


def download_10k(ticker: str, download_dir: Path, after: str = "2023-01-01") -> Path:
    """
    Download the most recent 10-K filing for a ticker.
    Returns the path to the downloaded filing subdirectory.
    """
    dl = Downloader(
        company_name="Investment Research Agentic Platform",
        email_address="vinodreddyjaggavarapu@gmail.com",
        download_folder=str(download_dir),
    )

    logger.info(f"Downloading most recent 10-K for {ticker} (after {after})...")
    dl.get("10-K", ticker, after=after, limit=1)

    filing_dir = download_dir / "sec-edgar-filings" / ticker / "10-K"
    filings = sorted(filing_dir.iterdir()) if filing_dir.exists() else []

    if not filings:
        raise FileNotFoundError(f"No 10-K found for {ticker} after {after}.")

    filing_subdir = filings[-1]
    logger.info(f"Filing saved to: {filing_subdir}")
    return filing_subdir


def find_html_file(filing_dir: Path, filing_type: str = "10-K") -> Path:
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
        return extract_html_from_submission(full_submission, filing_type)

    raise FileNotFoundError(f"No usable filing found in {filing_dir}")


def extract_html_from_submission(submission_path: Path, filing_type: str = "10-K") -> Path:
    """
    Extract the primary HTML document from a full-submission.txt file.
    Looks for a DOCUMENT block whose TYPE matches filing_type (e.g. 10-K, 10-Q, 8-K).
    """
    logger.info(f"Extracting HTML from full-submission.txt...")

    content = submission_path.read_text(encoding="utf-8", errors="ignore")
    doc_blocks = re.split(r"<DOCUMENT>", content, flags=re.IGNORECASE)

    # Accept exact match and amended variants (e.g. 10-K/A, 10-Q/A)
    accepted_types = {filing_type.upper(), f"{filing_type.upper()}/A"}

    for block in doc_blocks:
        type_match = re.search(r"<TYPE>\s*(\S+)", block, re.IGNORECASE)
        if not type_match:
            continue

        doc_type = type_match.group(1).strip().upper()
        if doc_type not in accepted_types:
            continue

        text_match = re.search(
            r"<TEXT>(.*?)</TEXT>",
            block,
            re.IGNORECASE | re.DOTALL,
        )
        if not text_match:
            continue

        extracted = text_match.group(1).strip()
        output_path = submission_path.parent / "primary-document.html"
        output_path.write_text(extracted, encoding="utf-8")
        logger.info(f"Extracted {len(extracted):,} chars → {output_path}")
        return output_path

    raise ValueError(
        f"Could not find {filing_type} document block in {submission_path}. "
        f"The filing format may be unusual."
    )

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
# Main entry point — one ticker, one year (10-K)
# ---------------------------------------------------------------------------

def ingest_filing(
    ticker: str,
    download_dir: Path,
    after: str = "2023-01-01",
) -> list[FilingSection]:
    """Download most recent 10-K → parse → return sections."""
    ticker = ticker.upper()

    filing_dir = download_10k(ticker, download_dir, after)
    year       = _year_from_dir(filing_dir)
    html_file  = find_html_file(filing_dir, "10-K")
    sections   = parse_10k(html_file, ticker, year)

    return sections


# ---------------------------------------------------------------------------
# 10-Q and 8-K support
# ---------------------------------------------------------------------------

# Most valuable 10-Q sections — quarterly update to the annual 10-K narrative
# 10-Q item numbers differ from 10-K, but we store them under the same
# structural names as 10-K so section names are consistent across filing types.
# 10-Q Item 1  (Financial Statements)  → "Item 8"
# 10-Q Item 2  (MD&A)                  → "Item 7"
# 10-Q Item 3  (Market Risk)           → "Item 7A"
# 10-Q Item 1A (Risk Factors)          → "Item 1A"
SECTION_PATTERNS_10Q: dict[str, str] = {
    "Item 8":  r"Item\s+1\.\s+Financial\s+Statements",
    "Item 7":  r"Item\s+2\.\s+Management",
    "Item 7A": r"Item\s+3\.\s+Quantitative",
    "Item 1A": r"Item\s+1A\.\s+Risk\s+Factors",
}

# 8-K event items — material events companies are required to disclose
SECTION_PATTERNS_8K: dict[str, str] = {
    "Results of Operations":      r"Item\s+2\.02",
    "Material Definitive Agreement": r"Item\s+1\.01",
    "Reg FD Disclosure":          r"Item\s+7\.01",
    "Other Events":               r"Item\s+8\.01",
    "Financial Statements":       r"Item\s+9\.01",
}


def _extract_sections_generic(
    text:             str,
    ticker:           str,
    year:             int,
    patterns:         dict[str, str],
    filing_type:      str,
    fallback_section: str,
) -> list[FilingSection]:
    """
    Generic section extractor.  Finds the LAST qualifying match per pattern
    (skips TOC entries which appear earlier in the doc with < 500 chars of
    following text), slices text between consecutive section starts, and
    falls back to the entire document when no patterns match.
    """
    candidates: dict[str, int] = {}

    for section_name, pattern in patterns.items():
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        for match in reversed(matches):
            pos = match.start()
            if len(text[pos : pos + 3000].strip()) > 500:
                candidates[section_name] = pos
                break

    if not candidates:
        clean = text.strip()
        if len(clean) > 300:
            return [FilingSection(
                ticker=ticker, year=year,
                section=fallback_section, text=clean,
                filing_type=filing_type,
            )]
        return []

    ordered = sorted(candidates.items(), key=lambda x: x[1])
    results: list[FilingSection] = []
    for i, (section_name, start_pos) in enumerate(ordered):
        end_pos      = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        section_text = text[start_pos:end_pos].strip()
        if len(section_text) >= 300:
            results.append(FilingSection(
                ticker=ticker, year=year,
                section=section_name, text=section_text,
                filing_type=filing_type,
            ))

    logger.info("  %s %d %s: %d sections extracted", ticker, year, filing_type, len(results))
    return results


def _parse_html_to_text(html_path: Path) -> str:
    """Shared HTML → plain-text extractor used by all filing parsers."""
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "ix:nonnumeric", "ix:nonfraction"]):
        tag.decompose()
    for table in soup.find_all("table"):
        table.replace_with(table.get_text(separator=" "))

    full_text = soup.get_text(separator="\n")
    full_text = re.sub(r"\n{3,}", "\n\n", full_text)
    full_text = re.sub(r"[ \t]+", " ", full_text)
    return full_text


def parse_10q(html_path: Path, ticker: str, year: int) -> list[FilingSection]:
    """Parse a 10-Q HTML file and extract the key quarterly sections."""
    logger.info("Parsing 10-Q: %s", html_path)
    text = _parse_html_to_text(html_path)
    logger.info("  Extracted %d chars", len(text))
    return _extract_sections_generic(
        text, ticker, year,
        SECTION_PATTERNS_10Q, "10-Q", "10-Q Filing",
    )


def parse_8k(html_path: Path, ticker: str, year: int) -> list[FilingSection]:
    """Parse an 8-K HTML file. Falls back to full text when no items are found."""
    logger.info("Parsing 8-K: %s", html_path)
    text = _parse_html_to_text(html_path)
    logger.info("  Extracted %d chars", len(text))
    return _extract_sections_generic(
        text, ticker, year,
        SECTION_PATTERNS_8K, "8-K", "8-K Filing",
    )


def _download_recent(
    ticker:       str,
    filing_type:  str,
    download_dir: Path,
    limit:        int,
    after:        str,
) -> list[Path]:
    """
    Download the N most recent filings of a given type.
    Returns the list of per-filing subdirectories, newest last.
    """
    dl = Downloader(
        company_name  = "Investment Research Agentic Platform",
        email_address = "vinodreddyjaggavarapu@gmail.com",
        download_folder = str(download_dir),
    )
    try:
        dl.get(filing_type, ticker, after=after, limit=limit)
    except Exception as e:
        logger.warning("  Could not download %s for %s: %s", filing_type, ticker, e)
        return []

    filing_dir = download_dir / "sec-edgar-filings" / ticker / filing_type
    if not filing_dir.exists():
        return []

    return sorted(filing_dir.iterdir())


def _year_from_dir(filing_dir: Path) -> int:
    """Extract fiscal year from an EDGAR directory name like '2024-11-05-edgar-html'."""
    try:
        return int(filing_dir.name[:4])
    except (ValueError, IndexError):
        import datetime
        return datetime.datetime.now().year


def ingest_recent_filings(
    ticker:      str,
    download_dir: Path,
    filing_type: str,
    limit:       int,
    after:       str = "2024-01-01",
) -> list[FilingSection]:
    """
    Download and parse the N most recent 10-Q or 8-K filings for a ticker.
    Returns all FilingSections across all downloaded filings.
    """
    ticker = ticker.upper()
    logger.info("Ingesting %d recent %s filings for %s...", limit, filing_type, ticker)

    filing_dirs = _download_recent(ticker, filing_type, download_dir, limit, after)
    if not filing_dirs:
        logger.warning("  No %s filings found for %s", filing_type, ticker)
        return []

    parse_fn = parse_10q if filing_type == "10-Q" else parse_8k
    all_sections: list[FilingSection] = []

    for filing_dir in filing_dirs:
        year = _year_from_dir(filing_dir)
        try:
            html_file = find_html_file(filing_dir, filing_type)
        except FileNotFoundError:
            logger.warning("  No HTML file in %s — skipping", filing_dir)
            continue
        try:
            sections = parse_fn(html_file, ticker, year)
            all_sections.extend(sections)
        except Exception as e:
            logger.error("  Failed to parse %s: %s", filing_dir, e)

    logger.info("  %s %s: %d total sections", ticker, filing_type, len(all_sections))
    return all_sections