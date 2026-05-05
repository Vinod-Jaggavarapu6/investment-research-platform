"""Async PostgreSQL setup — engine, session factory, ORM models."""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/investment_research"
)

engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    """FastAPI dependency — yields a session, closes it after the request."""
    async with AsyncSessionLocal() as session:
        yield session

class Base(DeclarativeBase):
    pass


class Chunk(Base):
    __tablename__ = "chunks"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    text         = Column(Text,        nullable=False)
    ticker       = Column(String(10),  nullable=False, index=True)
    year         = Column(Integer,     nullable=False, index=True)
    section      = Column(String(100), nullable=False)
    filing_type  = Column(String(10),  nullable=False, index=True, server_default="10-K")
    embedding    = Column(Vector(1536), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return (
            f"<Chunk ticker={self.ticker} year={self.year} "
            f"filing_type={self.filing_type} section={self.section}>"
        )


class Conversation(Base):
    __tablename__ = "conversations"

    id         = Column(String(36), primary_key=True)
    session_id = Column(String(36), nullable=False, index=True)
    title      = Column(String(200), nullable=False)
    ticker     = Column(String(10),  nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"

    id              = Column(String(36), primary_key=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role            = Column(String(10),  nullable=False)   # "user" | "assistant"
    content         = Column(Text,        nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())


async def create_tables():
    """Create all tables if they don't exist. Safe to call on every startup."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        
        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
            ON chunks USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """))


async def reset_embedding_column(dim: int) -> None:
    """Drops and recreates the embedding column — use when switching embedding models."""
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS chunks_embedding_hnsw"))
        await conn.execute(text("ALTER TABLE chunks DROP COLUMN IF EXISTS embedding"))
        await conn.execute(text(f"ALTER TABLE chunks ADD COLUMN embedding vector({dim})"))
        await conn.execute(text(f"""
            CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
            ON chunks USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """))


def get_checkpointer_url() -> str:
    """Strips the asyncpg driver prefix so LangGraph's psycopg3 checkpointer can connect."""
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

