from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from dotenv import load_dotenv


@pytest.fixture(scope="session", autouse=True)
def load_env():
    load_dotenv(Path(__file__).parent.parent / ".env")


def _make_mock_db() -> AsyncMock:
    """AsyncSession mock with sensible defaults for the execute → scalars → all chain."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    return db


@pytest.fixture
def client(monkeypatch):
    """
    FastAPI TestClient with all external connections mocked.
    Runs without a live DB, Redis, or LLM API keys.
    """
    monkeypatch.setattr("app.main.init_clients", MagicMock())
    monkeypatch.setattr("app.main.create_tables", AsyncMock())

    mock_cache = AsyncMock()
    mock_cache.health_check = AsyncMock(return_value=True)
    mock_cache.close = AsyncMock()
    monkeypatch.setattr("app.main.ResearchCacheClient", MagicMock(return_value=mock_cache))

    mock_checkpointer = MagicMock()
    mock_checkpointer.setup = AsyncMock()
    mock_saver_cm = AsyncMock()
    mock_saver_cm.__aenter__ = AsyncMock(return_value=mock_checkpointer)
    mock_saver_cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "app.main.AsyncPostgresSaver",
        MagicMock(from_conn_string=MagicMock(return_value=mock_saver_cm)),
    )

    from app.database import get_db
    from app.main import app
    from fastapi.testclient import TestClient

    async def override_db():
        yield _make_mock_db()

    app.dependency_overrides[get_db] = override_db

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
