import re
from typing import Optional

# Matches standard exchange tickers: 1-5 uppercase letters, optional .XX suffix (e.g. BRK.B)
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z]{1,2})?$")


class InvalidTickerError(ValueError):
    def __init__(self, raw: str, reason: str) -> None:
        super().__init__(f"Invalid ticker {raw!r}: {reason}")
        self.raw = raw
        self.reason = reason


def validate_ticker(raw: Optional[str]) -> str:
    """Normalize and validate a single ticker symbol.

    Returns the uppercased ticker on success.
    Raises InvalidTickerError if the value is empty or does not match the
    expected format (1-5 uppercase letters, optional .XX suffix).
    """
    if not raw or not raw.strip():
        raise InvalidTickerError(str(raw), "ticker cannot be empty")
    normalized = raw.strip().upper()
    if not _TICKER_RE.match(normalized):
        raise InvalidTickerError(
            raw,
            f"expected 1-5 letters with optional .XX suffix, got {normalized!r}",
        )
    return normalized


def validate_tickers(raw: Optional[list[str]]) -> list[str]:
    """Normalize and validate a list of ticker symbols.

    Raises InvalidTickerError on the first invalid entry.
    """
    if not raw:
        raise InvalidTickerError("[]", "tickers list cannot be empty")
    return [validate_ticker(t) for t in raw]
