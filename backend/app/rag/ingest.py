"""
ingest.py — Orchestrate download + parse for one ticker.

This module is the public entry point for the RAG pipeline.
Implementation details live in the sibling modules:
  downloader.py — SEC EDGAR download and file-finding
  parser.py     — HTML → section text extraction
  types.py      — FilingSection dataclass

FilingSection is re-exported here so existing callers
(chunker.py, background_ingest.py) don't need import changes.
"""

import logging
from pathlib import Path

from .types import FilingSection  # re-exported for backward compatibility
from .downloader import download_10k, download_recent, find_html_file, year_from_dir
from .parser import parse_10k, parse_10q, parse_8k

__all__ = ["FilingSection", "ingest_filing", "ingest_recent_filings"]

logger = logging.getLogger(__name__)


def ingest_filing(
    ticker:       str,
    download_dir: Path,
    after:        str = "2023-01-01",
) -> list[FilingSection]:
    """Download the most recent 10-K → parse → return sections."""
    ticker      = ticker.upper()
    filing_dir  = download_10k(ticker, download_dir, after)
    year        = year_from_dir(filing_dir)
    html_file   = find_html_file(filing_dir, "10-K")
    return parse_10k(html_file, ticker, year)


def ingest_recent_filings(
    ticker:       str,
    download_dir: Path,
    filing_type:  str,
    limit:        int,
    after:        str = "2024-01-01",
) -> list[FilingSection]:
    """
    Download and parse the N most recent 10-Q or 8-K filings for a ticker.
    Returns all FilingSections across all downloaded filings.
    """
    ticker = ticker.upper()
    logger.info("Ingesting %d recent %s filings for %s...", limit, filing_type, ticker)

    filing_dirs = download_recent(ticker, filing_type, download_dir, limit, after)
    if not filing_dirs:
        logger.warning("No %s filings found for %s", filing_type, ticker)
        return []

    parse_fn     = parse_10q if filing_type == "10-Q" else parse_8k
    all_sections: list[FilingSection] = []

    for filing_dir in filing_dirs:
        year = year_from_dir(filing_dir)
        try:
            html_file = find_html_file(filing_dir, filing_type)
        except FileNotFoundError:
            logger.warning("No HTML file in %s — skipping", filing_dir)
            continue
        try:
            all_sections.extend(parse_fn(html_file, ticker, year))
        except Exception as e:
            logger.error("Failed to parse %s: %s", filing_dir, e)

    logger.info("%s %s: %d total sections", ticker, filing_type, len(all_sections))
    return all_sections
