"""
SSE event shapes — single source of truth for the frontend/backend wire contract.

Every event emitted by streaming.py must be built through one of the models here.
Frontend TypeScript types in frontend/src/types.ts must mirror these exactly.

When adding a new event type:
  1. Add a Pydantic model below
  2. Add a builder function in streaming.py
  3. Update the SSEEvent union in frontend/src/types.ts
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class CitationOut(BaseModel):
    """A single SEC filing chunk. Mirrors frontend Citation interface."""
    ticker: str
    year: int
    section: str
    filing_type: str
    score: float
    text: str


class NodeStartEvent(BaseModel):
    type: Literal["node_start"] = "node_start"
    node: str


class NodeCompleteEvent(BaseModel):
    type: Literal["node_complete"] = "node_complete"
    node: str
    # Flexible per-node summary dict — see _extract_node_output in streaming.py
    data: dict[str, Any]


class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    text: str


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    report: str | None
    ingesting_ticker: str | None = None
    citations: list[CitationOut] = []
    conversation_id: str | None = None


class ConversationReadyEvent(BaseModel):
    type: Literal["conversation_ready"] = "conversation_ready"
    conversation_id: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


SSEEvent = (
    NodeStartEvent
    | NodeCompleteEvent
    | TokenEvent
    | DoneEvent
    | ConversationReadyEvent
    | ErrorEvent
)
