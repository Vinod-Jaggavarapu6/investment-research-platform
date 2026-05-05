import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, Conversation, Message
from app.models import ConversationResponse, MessageResponse

router = APIRouter(tags=["Conversations"])


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    session_id: str = Query(..., description="Browser session UUID"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.session_id == session_id)
        .order_by(Conversation.updated_at.desc())
    )
    return [ConversationResponse.model_validate(c) for c in result.scalars().all()]


@router.post("/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    session_id: str           = Query(...),
    ticker:     Optional[str] = Query(None),
    title:      Optional[str] = Query(None),
    db:         AsyncSession  = Depends(get_db),
):
    ticker_clean = ticker.upper() if ticker else None
    conv = Conversation(
        id         = str(uuid.uuid4()),
        session_id = session_id,
        title      = title or (f"{ticker_clean} — New Research" if ticker_clean else "New Research"),
        ticker     = ticker_clean,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return ConversationResponse.model_validate(conv)


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str         = Path(...),
    db:              AsyncSession = Depends(get_db),
):
    conv_result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msgs_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return {
        "conversation": ConversationResponse.model_validate(conv),
        "messages":     [MessageResponse.model_validate(m) for m in msgs_result.scalars().all()],
    }


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str         = Path(...),
    db:              AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.execute(sa_delete(Message).where(Message.conversation_id == conversation_id))
    await db.execute(sa_delete(Conversation).where(Conversation.id == conversation_id))
    await db.commit()
