from __future__ import annotations

import uuid


class TestListConversations:
    def test_returns_empty_list_for_unknown_session(self, client):
        resp = client.get("/conversations", params={"session_id": str(uuid.uuid4())})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_missing_session_id_returns_422(self, client):
        assert client.get("/conversations").status_code == 422


class TestCreateConversation:
    def test_missing_session_id_returns_422(self, client):
        assert client.post("/conversations").status_code == 422


class TestGetConversationMessages:
    def test_unknown_conversation_returns_404(self, client):
        resp = client.get(f"/conversations/{uuid.uuid4()}/messages")
        assert resp.status_code == 404

    def test_invalid_id_format_returns_404(self, client):
        assert client.get("/conversations/nonexistent-id/messages").status_code == 404


class TestDeleteConversation:
    def test_unknown_conversation_returns_404(self, client):
        resp = client.delete(f"/conversations/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestHealthEndpoint:
    def test_returns_200(self, client):
        assert client.get("/health").status_code == 200

    def test_response_shape(self, client):
        data = client.get("/health").json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
