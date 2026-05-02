"""
parser.py — Parse SEC filing HTML into labelled text sections.

Handles all three filing types:
  10-K  — annual report       (Items 1, 1A, 7, 7A, 8)
  10-Q  — quarterly report    (Items 1, 1A, 2, 3 mapped to 10-K names)
  8-K   — current event       (Items 1.01, 2.02, 7.01, 8.01, 9.01)

Strategy for section extraction:
  1. Strip script/style/XBRL tags and flatten tables to plain text
  2. Scan for Item headings using regexes
  3. Take the LAST qualifying match per heading (skips TOC entries
     which appear earlier with < 500 chars of following text)
  4. Slice document text between consecutive heading positions
"""

import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .types import FilingSection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Section heading patterns per filing type
# ---------------------------------------------------------------------------

# 10-K — annual report sections
# Inline because extract_sections() is 10-K-specific and these don't need
# to be shared elsewhere.
_PATTERNS_10K: dict[str, str] = {
    "Item 1":  r"Item\s+1\.\s+Business",
    "Item 1A": r"Item\s+1A\.\s+Risk\s+Factors",
    "Item 7":  r"Item\s+7\.\s+Management",
    "Item 7A": r"Item\s+7A\.\s+Quantitative",
    "Item 8":  r"Item\s+8\.\s+Financial\s+Statements",
}

# 10-Q — quarterly report sections mapped to equivalent 10-K names
# so section names are consistent across filing types in the DB.
# 10-Q Item 1  → "Item 8"  (Financial Statements)
# 10-Q Item 2  → "Item 7"  (MD&A)
# 10-Q Item 3  → "Item 7A" (Market Risk)
# 10-Q Item 1A → "Item 1A" (Risk Factors)
SECTION_PATTERNS_10Q: dict[str, str] = {
    "Item 8":  r"Item\s+1\.\s+Financial\s+Statements",
    "Item 7":  r"Item\s+2\.\s+Management",
    "Item 7A": r"Item\s+3\.\s+Quantitative",
    "Item 1A": r"Item\s+1A\.\s+Risk\s+Factors",
}

# 8-K — material event sections
SECTION_PATTERNS_8K: dict[str, str] = {
    "Results of Operations":         r"Item\s+2\.02",
    "Material Definitive Agreement":  r"Item\s+1\.01",
    "Reg FD Disclosure":              r"Item\s+7\.01",
    "Other Events":                   r"Item\s+8\.01",
    "Financial Statements":           r"Item\s+9\.01",
}


# ---------------------------------------------------------------------------
# Shared HTML → plain-text extractor
# ---------------------------------------------------------------------------

def _html_to_text(html_path: Path) -> str:
    """Strip noise tags, flatten tables, and return normalized plain text."""
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "ix:nonnumeric", "ix:nonfraction"]):
        tag.decompose()
    for table in soup.find_all("table"):
        table.replace_with(table.get_text(separator=" "))

    text = soup.get_text(separator="\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


# ---------------------------------------------------------------------------
# Generic section extractor (10-Q and 8-K)
# ---------------------------------------------------------------------------

def _extract_sections_generic(
    text:             str,
    ticker:           str,
    year:             int,
    patterns:         dict[str, str],
    filing_type:      str,
    fallback_section: str,
) -> list[FilingSection]:
    """
    Finds the last qualifying match per pattern (skips TOC entries with
    < 500 chars of following text), slices between consecutive positions,
    and falls back to the entire document when no patterns match.
    """
    candidates: dict[str, int] = {}

    for section_name, pattern in patterns.items():
        for match in reversed(list(re.finditer(pattern, text, re.IGNORECASE))):
            pos = match.start()
            if len(text[pos: pos + 3000].strip()) > 500:
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

    logger.info("%s %d %s: %d sections extracted", ticker, year, filing_type, len(results))
    return results


# ---------------------------------------------------------------------------
# 10-K parser
# ---------------------------------------------------------------------------

def extract_sections(text: str, ticker: str, year: int) -> list[FilingSection]:
    """
    Find 10-K section boundaries.

    Takes the LAST qualifying match per heading (> 2000 chars of following
    text) to reliably skip TOC references that appear earlier in the document.
    """
    candidates: dict[str, int] = {}

    for section_name, pattern in _PATTERNS_10K.items():
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if not matches:
            logger.warning("No match for %s in %s %d", section_name, ticker, year)
            continue
        for match in reversed(matches):
            pos = match.start()
            if len(text[pos: pos + 3000].strip()) > 2000:
                candidates[section_name] = pos
                break

    if not candidates:
        logger.warning("No sections found for %s %d", ticker, year)
        return []

    ordered = sorted(candidates.items(), key=lambda x: x[1])
    logger.info("Found %d sections: %s", len(ordered), [s for s, _ in ordered])

    results: list[FilingSection] = []
    for i, (section_name, start_pos) in enumerate(ordered):
        end_pos      = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        section_text = text[start_pos:end_pos].strip()
        logger.info("  %s: pos=%d → %d (%d chars)", section_name, start_pos, end_pos, len(section_text))
        results.append(FilingSection(ticker=ticker, year=year, section=section_name, text=section_text))

    return results


def parse_10k(html_path: Path, ticker: str, year: int) -> list[FilingSection]:
    """Parse a 10-K HTML file and extract text for each target section."""
    logger.info("Parsing 10-K: %s", html_path)
    text = _html_to_text(html_path)
    logger.info("Extracted %d chars", len(text))
    sections = extract_sections(text, ticker, year)
    logger.info("Extracted %d sections for %s %d", len(sections), ticker, year)
    return sections


# ---------------------------------------------------------------------------
# 10-Q and 8-K parsers
# ---------------------------------------------------------------------------

def parse_10q(html_path: Path, ticker: str, year: int) -> list[FilingSection]:
    """Parse a 10-Q HTML file and extract the key quarterly sections."""
    logger.info("Parsing 10-Q: %s", html_path)
    text = _html_to_text(html_path)
    logger.info("  Extracted %d chars", len(text))
    return _extract_sections_generic(text, ticker, year, SECTION_PATTERNS_10Q, "10-Q", "10-Q Filing")


def parse_8k(html_path: Path, ticker: str, year: int) -> list[FilingSection]:
    """Parse an 8-K HTML file. Falls back to full text when no items are found."""
    logger.info("Parsing 8-K: %s", html_path)
    text = _html_to_text(html_path)
    logger.info("  Extracted %d chars", len(text))
    return _extract_sections_generic(text, ticker, year, SECTION_PATTERNS_8K, "8-K", "8-K Filing")
