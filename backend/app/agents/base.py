"""
Shared utilities for LangGraph agent node wrappers.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def node_error(output_key: str, node_name: str, exc: Exception) -> dict:
    """
    Standard error return for a graph node.

    Logs the exception with traceback and returns a state dict whose
    output_key contains a human-readable message so the synthesizer can
    still produce a partial answer from whichever agents succeeded.

    For nodes that write multiple state keys (e.g. filings + citations),
    merge the result with defaults for the secondary keys:
        return {**node_error("filings_output", "filings_agent", exc), "citations": []}
    """
    logger.exception("[%s] unhandled error — %s", node_name, exc)
    return {output_key: f"[{node_name}] Agent error: {str(exc)[:300]}"}
