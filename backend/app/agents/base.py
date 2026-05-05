"""
Shared utilities for LangGraph agent node wrappers.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def node_error(output_key: str, node_name: str, exc: Exception) -> dict:
    """Standard error return for a graph node.

    Sets output_key to None so the synthesizer's truthiness check skips it,
    and records the failure in agent_errors so the synthesizer can surface a
    clear note to the user rather than silently producing a degraded answer.

    For nodes that write multiple state keys (e.g. filings + citations),
    merge the secondary keys after spreading this result:
        return {**node_error("filings_output", "filings_agent", exc), "citations": []}
    """
    logger.exception("[%s] unhandled error — %s", node_name, exc)
    return {
        output_key:    None,
        "agent_errors": {node_name: str(exc)[:300]},
    }
