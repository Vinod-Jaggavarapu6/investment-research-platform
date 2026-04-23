from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models import (
    AnalysisResponse,
    DataSource,
    FinancialSnapshot,
    RawFinancialData,
    SignalStrength,
)


# ─────────────────────────────────────────────
# LAYER 1: Unit tests
# No network. No LLM. Run instantly.
# ─────────────────────────────────────────────

class TestRawFinancialData:
    """Verify the RawFinancialData model behaves correctly."""

    def test_minimal_construction(self):
        # Only ticker is required — all financial fields are optional
        raw = RawFinancialData(ticker="TEST")
        assert raw.ticker == "TEST"
        assert raw.pe_ratio is None
        assert raw.fetch_errors == []
        assert raw.source == DataSource.YFINANCE

    def test_fetch_errors_accumulate(self):
        raw = RawFinancialData(
            ticker="X",
            fetch_errors=["pe_ratio: got NaN", "revenue_growth: missing"],
        )
        assert len(raw.fetch_errors) == 2

    def test_market_cap_stored_as_raw_usd(self):
        # Market cap is NOT converted to billions at this layer
        raw = RawFinancialData(ticker="AAPL", market_cap=3_000_000_000_000)
        assert raw.market_cap == 3e12

    def test_source_serializes_as_string(self):
        # str + Enum means JSON has "yfinance" not {"value": "yfinance"}
        raw = RawFinancialData(ticker="X")
        dumped = raw.model_dump()
        assert dumped["source"] == "yfinance"


class TestFinancialSnapshot:
    """Verify the FinancialSnapshot model and its constraints."""

    def test_valid_minimal_snapshot(self):
        snap = FinancialSnapshot(
            ticker="AAPL",
            signal=SignalStrength.BUY,
            analysis_summary="Apple's 27% net margin leads the large-cap tech cohort.",
            key_strengths=["27% net margin", "Strong FCF generation"],
            key_risks=["China revenue concentration risk"],
        )
        assert snap.ticker == "AAPL"
        assert snap.signal == SignalStrength.BUY
        assert snap.pe_ratio is None  

    def test_invalid_signal_raises(self):
        # Pydantic should reject a signal value not in the enum
        with pytest.raises(ValidationError) as exc_info:
            FinancialSnapshot(
                ticker="X",
                signal="MEGA_BUY",        
                analysis_summary="Test",
                key_strengths=[],
                key_risks=[],
            )
        errors = exc_info.value.errors()
        assert any("signal" in str(e) for e in errors)

    def test_insufficient_data_signal(self):
        snap = FinancialSnapshot(
            ticker="UNKNOWN",
            signal=SignalStrength.INSUFFICIENT_DATA,
            analysis_summary="No financial data was available for this ticker.",
            key_strengths=[],
            key_risks=[],
            data_quality_warning="All core metrics missing.",
        )
        assert snap.data_quality_warning is not None

    def test_all_signals_are_valid(self):
        for signal in SignalStrength:
            snap = FinancialSnapshot(
                ticker="X",
                signal=signal,
                analysis_summary="Test summary with a number: 12% margin.",
                key_strengths=["Test strength"],
                key_risks=["Test risk"],
            )
            assert snap.signal == signal


class TestAnalysisResponse:
    """Verify the API response envelope."""

    def test_error_response_shape(self):
        resp = AnalysisResponse(success=False, error="Ticker not found")
        assert not resp.success
        assert resp.snapshot is None
        assert resp.error == "Ticker not found"

    def test_success_response_shape(self):
        snap = FinancialSnapshot(
            ticker="MSFT",
            signal=SignalStrength.HOLD,
            analysis_summary="Microsoft's Azure growth is decelerating but margins remain solid.",
            key_strengths=["High gross margin"],
            key_risks=["AI capex pressuring FCF"],
        )
        resp = AnalysisResponse(success=True, snapshot=snap, duration_ms=1234.5)
        assert resp.success
        assert resp.snapshot.ticker == "MSFT"
        assert resp.duration_ms == 1234.5

    def test_success_requires_no_error(self):
        # A successful response has no error field
        resp = AnalysisResponse(success=True, snapshot=None)
        assert resp.error is None


class TestSafeFloat:
    """
    Unit test the _safe_float helper directly.
    This is testing a private function — acceptable because it's
    a pure function with complex behavior worth testing in isolation.
    """

    def setup_method(self):
        from app.tools.market_data import _safe_float
        self._safe_float = _safe_float

    def test_none_returns_none(self):
        errors: list[str] = []
        assert self._safe_float(None, "field", errors) is None
        assert errors == []          # no error logged for plain None

    def test_valid_float_string(self):
        errors: list[str] = []
        assert self._safe_float("24.5", "pe", errors) == 24.5
        assert errors == []

    def test_valid_int(self):
        errors: list[str] = []
        assert self._safe_float(100, "price", errors) == 100.0

    def test_nan_returns_none_with_error(self):
        errors: list[str] = []
        result = self._safe_float(float("nan"), "pe_ratio", errors)
        assert result is None
        assert len(errors) == 1
        assert "NaN" in errors[0]
        assert "pe_ratio" in errors[0]

    def test_inf_returns_none_with_error(self):
        errors: list[str] = []
        result = self._safe_float(float("inf"), "ev_ebitda", errors)
        assert result is None
        assert "Inf" in errors[0]

    def test_bad_string_returns_none_with_error(self):
        errors: list[str] = []
        result = self._safe_float("N/A", "pe", errors)
        assert result is None
        assert len(errors) == 1

    def test_multiple_errors_accumulate(self):
        # Errors from multiple calls accumulate in the same list
        errors: list[str] = []
        self._safe_float(float("nan"), "field_a", errors)
        self._safe_float("N/A", "field_b", errors)
        assert len(errors) == 2


class TestAPIEndpoints:
    """
    Test the FastAPI endpoints using TestClient.
    TestClient runs the full app in-process — no server needed.
    These DO NOT make real external calls (agent is mocked).
    """

    @pytest.fixture
    def client(self, monkeypatch):
        """
        Create a TestClient with analyze_ticker mocked out.

        WHY MOCK:
        We want to test that the API layer correctly:
          - Calls the agent
          - Returns the right HTTP status codes
          - Serializes responses correctly
        We do NOT want these tests to call yfinance or the Anthropic API.
        That's what integration tests are for.

        monkeypatch replaces the function for the duration of this test only.
        """
        from app.main import app

        def mock_analyze(ticker: str, include_raw: bool = False) -> AnalysisResponse:
            if ticker == "INVALID":
                return AnalysisResponse(
                    success=False,
                    error=f"yfinance returned no price data for '{ticker}'."
                )
            snap = FinancialSnapshot(
                ticker=ticker,
                signal=SignalStrength.BUY,
                analysis_summary=f"{ticker} shows a net margin of 22% with 15% YoY growth.",
                key_strengths=["22% net margin", "15% revenue growth"],
                key_risks=["Valuation elevated at 28x forward P/E"],
                current_price=150.0,
                market_cap_billions=2400.0,
            )
            return AnalysisResponse(success=True, snapshot=snap, duration_ms=100.0)

        monkeypatch.setattr("app.main.analyze_ticker", mock_analyze)
        return TestClient(app)

    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["phase"] == 1

    def test_get_analyze_success(self, client):
        resp = client.get("/analyze/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["snapshot"]["ticker"] == "AAPL"
        assert data["snapshot"]["signal"] == "BUY"

    def test_get_analyze_lowercase_ticker(self, client):
        # Ticker should be uppercased by the endpoint
        resp = client.get("/analyze/aapl")
        assert resp.status_code == 200
        assert resp.json()["snapshot"]["ticker"] == "AAPL"

    def test_get_analyze_invalid_ticker_returns_422(self, client):
        resp = client.get("/analyze/INVALID")
        assert resp.status_code == 422

    def test_post_analyze_success(self, client):
        resp = client.post("/analyze", json={"ticker": "MSFT"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "snapshot" in data

    def test_post_analyze_missing_ticker_returns_422(self, client):
        # FastAPI validates request body — missing required field → 422
        resp = client.post("/analyze", json={})
        assert resp.status_code == 422

    def test_batch_success(self, client):
        resp = client.post("/analyze/batch", json=["AAPL", "MSFT"])
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 2
        assert all(r["success"] for r in results)

    def test_batch_too_many_tickers(self, client):
        tickers = [f"TK{i}" for i in range(11)]   # 11 tickers, limit is 10
        resp = client.post("/analyze/batch", json=tickers)
        assert resp.status_code == 422

    def test_batch_empty_list(self, client):
        resp = client.post("/analyze/batch", json=[])
        assert resp.status_code == 422

    def test_response_has_duration_ms(self, client):
        resp = client.get("/analyze/AAPL")
        assert resp.json()["duration_ms"] == 100.0


# ─────────────────────────────────────────────
# LAYER 2: Integration tests
# ─────────────────────────────────────────────

@pytest.mark.integration
class TestLiveFetcher:
    """Tests that call real yfinance. Require network access."""

    def test_fetch_valid_ticker(self):
        from app.tools.market_data import fetch_financial_data
        raw = fetch_financial_data("AAPL")
        assert raw.ticker == "AAPL"
        assert raw.current_price is not None and raw.current_price > 0
        assert raw.company_name is not None
        assert raw.source == DataSource.YFINANCE

    def test_fetch_invalid_ticker_raises(self):
        from app.tools.market_data import fetch_financial_data
        with pytest.raises(ValueError, match="no price data"):
            fetch_financial_data("INVALIDXYZ999")

    @pytest.mark.parametrize("ticker", ["MSFT", "GOOGL", "JPM", "XOM"])
    def test_fetch_multiple_tickers(self, ticker):
        from app.tools.market_data import fetch_financial_data
        raw = fetch_financial_data(ticker)
        assert raw.ticker == ticker
        assert raw.current_price is not None


@pytest.mark.integration
class TestLiveAgent:
    """Tests that call real yfinance + real Anthropic API."""

    def test_analyze_aapl(self):
        from app.agents.financial_agent import analyze_ticker
        result = analyze_ticker("AAPL")

        assert result.success is True
        assert result.snapshot is not None
        assert result.snapshot.ticker == "AAPL"
        assert result.snapshot.signal in list(SignalStrength)
        assert len(result.snapshot.analysis_summary) > 50
        assert len(result.snapshot.key_strengths) >= 1
        assert len(result.snapshot.key_risks) >= 1
        assert result.duration_ms is not None

    def test_analyze_invalid_ticker(self):
        from app.agents.financial_agent import analyze_ticker
        result = analyze_ticker("INVALIDXYZ999")
        assert result.success is False
        assert result.error is not None
        assert result.snapshot is None

    @pytest.mark.parametrize("ticker", [
        "MSFT", "GOOGL", "AMZN", "TSLA",
        "META", "JPM", "JNJ", "XOM", "WMT", "V"
    ])
    def test_analyze_ten_tickers(self, ticker):
        from app.agents.financial_agent import analyze_ticker
        result = analyze_ticker(ticker)

        assert result.success, f"{ticker} failed: {result.error}"
        assert result.snapshot.signal != SignalStrength.INSUFFICIENT_DATA, \
            f"{ticker} got INSUFFICIENT_DATA — check raw data quality"