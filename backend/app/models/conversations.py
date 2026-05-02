from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ConversationResponse(BaseModel):
    id: str
    session_id: str
    title: str
    ticker: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}
