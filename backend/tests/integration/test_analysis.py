from __future__ import annotations

import pytest

from app.models.financial import AnalysisResponse, FinancialSnapshot, SignalStrength


def _success(ticker: str) -> AnalysisResponse:
    snap = FinancialSnapshot(
        ticker=ticker,
        signal=SignalStrength.BUY,
        analysis_summary=f"{ticker}: 22% net margin, 15% YoY growth.",
        key_strengths=["22% net margin"],
        key_risks=["Elevated valuation"],
        current_price=150.0,
        market_cap_billions=2400.0,
    )
    return AnalysisResponse(success=True, snapshot=snap, duration_ms=100.0)


def _failure(ticker: str) -> AnalysisResponse:
    return AnalysisResponse(success=False, error=f"yfinance returned no price data for '{ticker}'.")


@pytest.fixture
def analysis_client(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.analysis.analyze_ticker",
        lambda ticker, include_raw=False: _success(ticker) if ticker != "INVALID" else _failure(ticker),
    )
    return client


class TestAnalyzeGet:
    def test_returns_200(self, analysis_client):
        assert analysis_client.get("/analyze/AAPL").status_code == 200

    def test_response_shape(self, analysis_client):
        data = analysis_client.get("/analyze/AAPL").json()
        assert data["success"] is True
        assert data["snapshot"]["ticker"] == "AAPL"
        assert data["snapshot"]["signal"] == "BUY"

    def test_lowercase_ticker_uppercased(self, analysis_client):
        data = analysis_client.get("/analyze/aapl").json()
        assert data["snapshot"]["ticker"] == "AAPL"

    def test_invalid_ticker_returns_422(self, analysis_client):
        assert analysis_client.get("/analyze/INVALID").status_code == 422

    def test_duration_ms_present(self, analysis_client):
        assert analysis_client.get("/analyze/AAPL").json()["duration_ms"] == 100.0


class TestAnalyzePost:
    def test_returns_200(self, analysis_client):
        assert analysis_client.post("/analyze", json={"ticker": "MSFT"}).status_code == 200

    def test_response_shape(self, analysis_client):
        data = analysis_client.post("/analyze", json={"ticker": "MSFT"}).json()
        assert data["success"] is True
        assert "snapshot" in data

    def test_missing_ticker_returns_422(self, analysis_client):
        assert analysis_client.post("/analyze", json={}).status_code == 422

    def test_include_raw_data_accepted(self, analysis_client):
        resp = analysis_client.post("/analyze", json={"ticker": "AAPL", "include_raw_data": True})
        assert resp.status_code == 200


class TestAnalyzeBatch:
    def test_returns_200(self, analysis_client):
        assert analysis_client.post("/analyze/batch", json=["AAPL", "MSFT"]).status_code == 200

    def test_returns_list_of_results(self, analysis_client):
        results = analysis_client.post("/analyze/batch", json=["AAPL", "MSFT"]).json()
        assert len(results) == 2
        assert all(r["success"] for r in results)

    def test_empty_list_returns_422(self, analysis_client):
        assert analysis_client.post("/analyze/batch", json=[]).status_code == 422

    def test_too_many_tickers_returns_422(self, analysis_client):
        assert analysis_client.post("/analyze/batch", json=[f"TK{i}" for i in range(11)]).status_code == 422

    def test_lowercase_tickers_uppercased(self, analysis_client):
        results = analysis_client.post("/analyze/batch", json=["aapl"]).json()
        assert results[0]["snapshot"]["ticker"] == "AAPL"
