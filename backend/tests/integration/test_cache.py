from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from app import state


@pytest.fixture
def cache_client(client, monkeypatch):
    mock_cache = AsyncMock()
    mock_cache.exists = AsyncMock(return_value=False)
    mock_cache.get = AsyncMock(return_value=None)
    mock_cache.delete = AsyncMock(return_value=True)
    mock_cache.health_check = AsyncMock(return_value=True)
    monkeypatch.setattr(state, "cache", mock_cache)
    return client


class TestCacheDebugGet:
    def test_key_not_found(self, cache_client):
        resp = cache_client.get("/cache/debug", params={"ticker": "AAPL", "question": "P/E ratio?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is False
        assert data["value"] is None

    def test_key_found(self, client, monkeypatch):
        mock_cache = AsyncMock()
        mock_cache.exists = AsyncMock(return_value=True)
        mock_cache.get = AsyncMock(return_value={"final_answer": "AAPL answer"})
        monkeypatch.setattr(state, "cache", mock_cache)

        resp = client.get("/cache/debug", params={"ticker": "AAPL", "question": "P/E?"})
        assert resp.json()["exists"] is True
        assert resp.json()["value"] is not None

    def test_missing_params_returns_422(self, cache_client):
        assert cache_client.get("/cache/debug", params={"ticker": "AAPL"}).status_code == 422

    def test_no_cache_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(state, "cache", None)
        resp = client.get("/cache/debug", params={"ticker": "AAPL", "question": "P/E?"})
        assert resp.status_code == 503


class TestCacheDebugDelete:
    def test_deletes_key(self, cache_client):
        resp = cache_client.delete("/cache/debug", params={"ticker": "AAPL", "question": "P/E?"})
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_no_cache_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(state, "cache", None)
        resp = client.delete("/cache/debug", params={"ticker": "AAPL", "question": "P/E?"})
        assert resp.status_code == 503


class TestCacheHealth:
    def test_redis_ok(self, cache_client):
        resp = cache_client.get("/cache/health")
        assert resp.status_code == 200
        assert resp.json()["redis_ok"] is True

    def test_no_cache_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(state, "cache", None)
        assert client.get("/cache/health").status_code == 503
