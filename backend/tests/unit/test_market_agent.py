from __future__ import annotations

import json

import pytest
from unittest.mock import patch

from app.models.financial import AnalysisResponse, FinancialSnapshot, SignalStrength


def _snapshot(ticker: str = "AAPL") -> FinancialSnapshot:
    return FinancialSnapshot(
        ticker=ticker,
        signal=SignalStrength.BUY,
        analysis_summary="27% net margin with 15% YoY revenue growth.",
        key_strengths=["27% net margin"],
        key_risks=["China concentration risk"],
        current_price=150.0,
        market_cap_billions=2400.0,
    )


class TestMarketNode:
    @pytest.mark.asyncio
    async def test_no_ticker_returns_fallback_message(self):
        from app.agents.financial_agent import make_market_node

        node = make_market_node()
        result = await node({"question": "How is the market doing?"})

        assert "market_output" in result
        assert "No ticker" in result["market_output"]

    @pytest.mark.asyncio
    async def test_failed_analysis_stores_error_string(self):
        from app.agents.financial_agent import make_market_node

        failed = AnalysisResponse(success=False, error="yfinance returned no data for 'FAKE'")
        with patch("app.agents.financial_agent.analyze_ticker", return_value=failed):
            node = make_market_node()
            result = await node({"question": "...", "ticker": "FAKE"})

        assert "unavailable" in result["market_output"].lower()

    @pytest.mark.asyncio
    async def test_successful_analysis_returns_json_string(self):
        from app.agents.financial_agent import make_market_node

        success = AnalysisResponse(success=True, snapshot=_snapshot(), duration_ms=100.0)
        with patch("app.agents.financial_agent.analyze_ticker", return_value=success):
            node = make_market_node()
            result = await node({"question": "...", "ticker": "AAPL"})

        parsed = json.loads(result["market_output"])
        assert parsed["snapshot"]["ticker"] == "AAPL"
        assert parsed["snapshot"]["signal"] == "BUY"

    @pytest.mark.asyncio
    async def test_output_key_always_present(self):
        from app.agents.financial_agent import make_market_node

        node = make_market_node()
        result = await node({"question": "anything"})

        assert "market_output" in result


class TestSafeFloat:
    def setup_method(self):
        from app.tools.market_data import _safe_float
        self._f = _safe_float

    def test_none_returns_none_no_error(self):
        errors: list[str] = []
        assert self._f(None, "field", errors) is None
        assert errors == []

    def test_valid_float_string(self):
        errors: list[str] = []
        assert self._f("24.5", "pe", errors) == 24.5
        assert errors == []

    def test_valid_int(self):
        assert self._f(100, "price", []) == 100.0

    def test_nan_returns_none_with_error(self):
        errors: list[str] = []
        assert self._f(float("nan"), "pe_ratio", errors) is None
        assert any("NaN" in e and "pe_ratio" in e for e in errors)

    def test_inf_returns_none_with_error(self):
        errors: list[str] = []
        assert self._f(float("inf"), "ev_ebitda", errors) is None
        assert any("Inf" in e for e in errors)

    def test_bad_string_returns_none_with_error(self):
        errors: list[str] = []
        assert self._f("N/A", "pe", errors) is None
        assert len(errors) == 1

    def test_errors_accumulate_across_calls(self):
        errors: list[str] = []
        self._f(float("nan"), "field_a", errors)
        self._f("N/A", "field_b", errors)
        assert len(errors) == 2
