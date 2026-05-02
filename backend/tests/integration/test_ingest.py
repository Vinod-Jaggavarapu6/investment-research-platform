from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def ingest_client(client, monkeypatch):
    monkeypatch.setattr("app.routers.ingest.ticker_has_data", AsyncMock(return_value=False))
    monkeypatch.setattr("app.routers.ingest.is_ingesting", MagicMock(return_value=False))
    monkeypatch.setattr("app.routers.ingest.trigger_ingest", MagicMock())
    return client


class TestIngestStatus:
    def test_not_found(self, ingest_client):
        resp = ingest_client.get("/ingest/status/SNOW")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"

    def test_ready_when_data_exists(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.ingest.ticker_has_data", AsyncMock(return_value=True))
        monkeypatch.setattr("app.routers.ingest.is_ingesting", MagicMock(return_value=False))
        resp = client.get("/ingest/status/AAPL")
        assert resp.json()["status"] == "ready"

    def test_ingesting_when_in_progress(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.ingest.ticker_has_data", AsyncMock(return_value=False))
        monkeypatch.setattr("app.routers.ingest.is_ingesting", MagicMock(return_value=True))
        resp = client.get("/ingest/status/SNOW")
        assert resp.json()["status"] == "ingesting"

    def test_ticker_uppercased(self, ingest_client):
        resp = ingest_client.get("/ingest/status/snow")
        assert resp.json()["ticker"] == "SNOW"


class TestIngestTrigger:
    def test_starts_ingest(self, ingest_client):
        resp = ingest_client.post("/ingest/trigger/SNOW")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ingesting_started"

    def test_already_ingesting(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.ingest.ticker_has_data", AsyncMock(return_value=False))
        monkeypatch.setattr("app.routers.ingest.is_ingesting", MagicMock(return_value=True))
        assert client.post("/ingest/trigger/SNOW").json()["status"] == "already_ingesting"

    def test_already_ready(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.ingest.ticker_has_data", AsyncMock(return_value=True))
        monkeypatch.setattr("app.routers.ingest.is_ingesting", MagicMock(return_value=False))
        assert client.post("/ingest/trigger/AAPL").json()["status"] == "already_ready"
