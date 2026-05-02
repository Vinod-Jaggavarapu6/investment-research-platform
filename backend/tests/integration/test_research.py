from __future__ import annotations

import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def research_client(client, monkeypatch):
    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value={
        "route": "both",
        "ticker": "AAPL",
        "final_answer": "AAPL trades at 28x forward P/E with 15% revenue growth.",
        "citations": [{"ticker": "AAPL", "year": 2024, "section": "Item 1", "score": 0.91}],
    })

    import app.graph
    monkeypatch.setattr(app.graph, "build_graph", MagicMock(return_value=mock_graph))

    return client


class TestResearchEndpoint:
    def test_returns_200(self, research_client):
        resp = research_client.post("/research", json={"question": "Is AAPL fairly valued?"})
        assert resp.status_code == 200

    def test_response_shape(self, research_client):
        data = research_client.post("/research", json={"question": "Is AAPL fairly valued?"}).json()
        assert "thread_id" in data
        assert "route" in data
        assert "final_answer" in data
        assert "citations" in data

    def test_thread_id_is_uuid(self, research_client):
        thread_id = research_client.post("/research", json={"question": "AAPL P/E?"}).json()["thread_id"]
        uuid.UUID(thread_id)

    def test_provided_thread_id_is_reused(self, research_client):
        tid = "test-thread-abc-123"
        data = research_client.post("/research", json={"question": "AAPL?", "thread_id": tid}).json()
        assert data["thread_id"] == tid

    def test_missing_question_returns_422(self, research_client):
        assert research_client.post("/research", json={}).status_code == 422

    def test_citations_is_list(self, research_client):
        data = research_client.post("/research", json={"question": "AAPL risk factors?"}).json()
        assert isinstance(data["citations"], list)

    def test_no_final_answer_returns_500(self, client, monkeypatch):
        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={"route": "market", "final_answer": None})
        import app.graph
        monkeypatch.setattr(app.graph, "build_graph", MagicMock(return_value=mock_graph))

        resp = client.post("/research", json={"question": "anything?"})
        assert resp.status_code == 500
