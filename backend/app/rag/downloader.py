"""
downloader.py — Fetch SEC filings from EDGAR and locate the primary HTML document.

Responsibilities:
  - Download 10-K / 10-Q / 8-K filings via sec-edgar-downloader
  - Locate or extract the primary HTML file from a downloaded filing directory
  - Parse fiscal year from EDGAR directory names
"""

import datetime
import logging
import re
from pathlib import Path

from sec_edgar_downloader import Downloader

logger = logging.getLogger(__name__)

_EDGAR_COMPANY = "Investment Research Agentic Platform"
_EDGAR_EMAIL   = "vinodreddyjaggavarapu@gmail.com"


def _make_downloader(download_dir: Path) -> Downloader:
    return Downloader(
        company_name    = _EDGAR_COMPANY,
        email_address   = _EDGAR_EMAIL,
        download_folder = str(download_dir),
    )


def download_10k(ticker: str, download_dir: Path, after: str = "2023-01-01") -> Path:
    """
    Download the most recent 10-K filing for a ticker.
    Returns the path to the per-filing subdirectory.
    """
    dl = _make_downloader(download_dir)
    logger.info("Downloading most recent 10-K for %s (after %s)...", ticker, after)
    dl.get("10-K", ticker, after=after, limit=1)

    filing_dir = download_dir / "sec-edgar-filings" / ticker / "10-K"
    filings = sorted(filing_dir.iterdir()) if filing_dir.exists() else []

    if not filings:
        raise FileNotFoundError(f"No 10-K found for {ticker} after {after}.")

    filing_subdir = filings[-1]
    logger.info("Filing saved to: %s", filing_subdir)
    return filing_subdir


def download_recent(
    ticker:       str,
    filing_type:  str,
    download_dir: Path,
    limit:        int,
    after:        str,
) -> list[Path]:
    """
    Download the N most recent filings of a given type.
    Returns per-filing subdirectories sorted oldest-first.
    """
    dl = _make_downloader(download_dir)
    try:
        dl.get(filing_type, ticker, after=after, limit=limit)
    except Exception as e:
        logger.warning("Could not download %s for %s: %s", filing_type, ticker, e)
        return []

    filing_dir = download_dir / "sec-edgar-filings" / ticker / filing_type
    if not filing_dir.exists():
        return []

    return sorted(filing_dir.iterdir())


def find_html_file(filing_dir: Path, filing_type: str = "10-K") -> Path:
    """
    Find or extract the primary HTML document inside a filing directory.

    SEC EDGAR provides two formats:
      1. primary-document.html — clean, directly usable
      2. full-submission.txt   — raw EDGAR format with embedded HTML

    For format 2, the HTML is extracted and written to primary-document.html.
    """
    primary = filing_dir / "primary-document.html"
    if primary.exists():
        return primary

    html_files = list(filing_dir.glob("*.html")) + list(filing_dir.glob("*.htm"))
    if html_files:
        return max(html_files, key=lambda f: f.stat().st_size)

    full_submission = filing_dir / "full-submission.txt"
    if full_submission.exists():
        return extract_html_from_submission(full_submission, filing_type)

    raise FileNotFoundError(f"No usable filing found in {filing_dir}")


def extract_html_from_submission(submission_path: Path, filing_type: str = "10-K") -> Path:
    """
    Extract the primary HTML document from a full-submission.txt file.
    Looks for a DOCUMENT block whose TYPE matches filing_type (e.g. 10-K, 10-Q, 8-K).
    """
    logger.info("Extracting HTML from full-submission.txt...")

    content   = submission_path.read_text(encoding="utf-8", errors="ignore")
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

        text_match = re.search(r"<TEXT>(.*?)</TEXT>", block, re.IGNORECASE | re.DOTALL)
        if not text_match:
            continue

        extracted   = text_match.group(1).strip()
        output_path = submission_path.parent / "primary-document.html"
        output_path.write_text(extracted, encoding="utf-8")
        logger.info("Extracted %d chars → %s", len(extracted), output_path)
        return output_path

    raise ValueError(
        f"Could not find {filing_type} document block in {submission_path}. "
        "The filing format may be unusual."
    )


def year_from_dir(filing_dir: Path) -> int:
    """Extract fiscal year from an EDGAR directory name like '2024-11-05-edgar-html'."""
    try:
        return int(filing_dir.name[:4])
    except (ValueError, IndexError):
        return datetime.datetime.now().year
