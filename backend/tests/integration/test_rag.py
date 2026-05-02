from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def rag_client(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.rag.retrieve_chunks",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "app.routers.rag.format_retrieval_response",
        MagicMock(return_value={"chunks": [], "total": 0}),
    )

    mock_result = MagicMock()
    mock_result.question = "What are the risk factors?"
    mock_result.ticker = "AAPL"
    mock_result.answer = "Apple disclosed the following risks..."
    mock_result.model = "claude-sonnet-4-6"
    mock_result.sources = []
    monkeypatch.setattr(
        "app.routers.rag.answer_filing_question",
        AsyncMock(return_value=mock_result),
    )
    return client


class TestRetrieveEndpoint:
    def test_returns_200(self, rag_client):
        resp = rag_client.get("/retrieve", params={"query": "what are the risk factors"})
        assert resp.status_code == 200

    def test_missing_query_returns_422(self, rag_client):
        assert rag_client.get("/retrieve").status_code == 422

    def test_k_below_min_returns_422(self, rag_client):
        assert rag_client.get("/retrieve", params={"query": "risks", "k": 0}).status_code == 422

    def test_k_above_max_returns_422(self, rag_client):
        assert rag_client.get("/retrieve", params={"query": "risks", "k": 21}).status_code == 422

    def test_ticker_filter_accepted(self, rag_client):
        resp = rag_client.get("/retrieve", params={"query": "risks", "ticker": "AAPL"})
        assert resp.status_code == 200


class TestFilingsAskEndpoint:
    def test_returns_200(self, rag_client):
        resp = rag_client.post("/filings/ask", json={"question": "What are Apple's risk factors?"})
        assert resp.status_code == 200

    def test_response_shape(self, rag_client):
        data = rag_client.post("/filings/ask", json={"question": "risks?", "ticker": "AAPL"}).json()
        assert "question" in data
        assert "answer" in data
        assert "sources" in data

    def test_missing_question_returns_422(self, rag_client):
        assert rag_client.post("/filings/ask", json={}).status_code == 422
