from __future__ import annotations

from pydantic import BaseModel


class FilingsRequest(BaseModel):
    question: str
    ticker:   str | None = None
    k:        int = 5


class ResearchRequest(BaseModel):
    question: str
    thread_id: str | None = None


class ResearchResponse(BaseModel):
    thread_id: str
    route: str
    final_answer: str
    citations: list[dict]
