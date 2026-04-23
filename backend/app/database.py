"""
database.py — async PostgreSQL connection + Chunk table schema

Responsibilities:
  - Create async engine (one per app lifetime)
  - Provide session factory for FastAPI dependency injection
  - Define the Chunk ORM model
  - Create tables on startup
"""

from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.sql import func
import os
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

# ---------------------------------------------------------------------------
# Engine — one connection pool shared across the entire app
# ---------------------------------------------------------------------------

# DATABASE_URL = os.getenv(
#     "DATABASE_URL",
#     "postgresql+asyncpg://postgres:postgres@db:5432/investment_research"
# )

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/investment_research"
)

engine = create_async_engine(
    DATABASE_URL,
    echo=True,       # flip to True to print every SQL query while debugging
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


# ---------------------------------------------------------------------------
# ORM base + Chunk model
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class Chunk(Base):
    __tablename__ = "chunks"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    text        = Column(Text,        nullable=False)
    ticker      = Column(String(10),  nullable=False, index=True)
    year        = Column(Integer,     nullable=False, index=True)
    section     = Column(String(100), nullable=False)
    faiss_index = Column(Integer,     nullable=False, unique=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return (
            f"<Chunk ticker={self.ticker} year={self.year} "
            f"section={self.section} faiss_index={self.faiss_index}>"
        )


async def create_tables():
    """Create all tables if they don't exist. Safe to call on every startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_checkpointer_url() -> str:
    """
    Convert SQLAlchemy asyncpg URL → plain psycopg URL for LangGraph checkpointer.
    LangGraph uses psycopg3 directly, not SQLAlchemy.
    
    SQLAlchemy:  postgresql+asyncpg://user:pass@host:port/db
    psycopg3:    postgresql://user:pass@host:port/db
    """
    return DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def create_checkpointer() -> AsyncPostgresSaver:
    """
    Create and set up the LangGraph Postgres checkpointer.
    Uses async context manager correctly.
    """
    url = get_checkpointer_url()
    async with AsyncPostgresSaver.from_conn_string(url) as checkpointer:
        await checkpointer.setup()
    
    # Return a fresh instance for use throughout app lifetime
    # setup() only needs to run once to create tables
    conn = await AsyncPostgresSaver.from_conn_string(url).__aenter__()
    return conn