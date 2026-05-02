from dataclasses import dataclass


@dataclass
class FilingSection:
    ticker:      str
    year:        int
    section:     str
    text:        str
    filing_type: str = "10-K"
