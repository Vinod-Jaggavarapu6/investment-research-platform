from __future__ import annotations

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from app.models.financial import (
    AnalysisResponse, DataSource, FinancialSnapshot, RawFinancialData, SignalStrength,
)
from app.models.news import (
    ArticleSentiment, NewsArticle, NewsRequest, NewsResponse, NewsSentiment, SentimentSignal,
)
from app.models.research import FilingsRequest, ResearchRequest, ResearchResponse
from app.models.conversations import ConversationResponse, MessageResponse


# ── Financial models ──────────────────────────────────────────────────────────

class TestRawFinancialData:
    def test_only_ticker_required(self):
        raw = RawFinancialData(ticker="TEST")
        assert raw.ticker == "TEST"
        assert raw.pe_ratio is None
        assert raw.fetch_errors == []
        assert raw.source == DataSource.YFINANCE

    def test_fetch_errors_accumulate(self):
        raw = RawFinancialData(ticker="X", fetch_errors=["pe_ratio: NaN", "revenue: missing"])
        assert len(raw.fetch_errors) == 2

    def test_source_serializes_as_string(self):
        assert RawFinancialData(ticker="X").model_dump()["source"] == "yfinance"

    def test_market_cap_stored_as_raw_usd(self):
        raw = RawFinancialData(ticker="AAPL", market_cap=3_000_000_000_000)
        assert raw.market_cap == 3e12


class TestFinancialSnapshot:
    def _make(self, **kwargs) -> FinancialSnapshot:
        params = dict(
            ticker="AAPL",
            signal=SignalStrength.BUY,
            analysis_summary="27% net margin with 15% YoY growth.",
            key_strengths=["27% margin"],
            key_risks=["China concentration risk"],
        )
        params.update(kwargs)
        return FinancialSnapshot(**params)

    def test_minimal_construction(self):
        snap = self._make()
        assert snap.ticker == "AAPL"
        assert snap.signal == SignalStrength.BUY
        assert snap.pe_ratio is None

    def test_invalid_signal_raises(self):
        with pytest.raises(ValidationError):
            FinancialSnapshot(
                ticker="X", signal="MEGA_BUY",
                analysis_summary="Test", key_strengths=[], key_risks=[],
            )

    def test_all_signals_accepted(self):
        for signal in SignalStrength:
            snap = self._make(signal=signal)
            assert snap.signal == signal

    def test_data_quality_warning(self):
        snap = self._make(signal=SignalStrength.INSUFFICIENT_DATA, data_quality_warning="All metrics missing.")
        assert snap.data_quality_warning is not None


class TestAnalysisResponse:
    def test_error_shape(self):
        resp = AnalysisResponse(success=False, error="Ticker not found")
        assert not resp.success
        assert resp.snapshot is None
        assert resp.error == "Ticker not found"

    def test_success_shape(self):
        snap = FinancialSnapshot(
            ticker="MSFT", signal=SignalStrength.HOLD,
            analysis_summary="Azure growth decelerating but margins solid.",
            key_strengths=["High gross margin"], key_risks=["AI capex"],
        )
        resp = AnalysisResponse(success=True, snapshot=snap, duration_ms=1234.5)
        assert resp.success
        assert resp.snapshot.ticker == "MSFT"
        assert resp.duration_ms == 1234.5

    def test_success_has_no_error_by_default(self):
        assert AnalysisResponse(success=True).error is None


# ── News models ───────────────────────────────────────────────────────────────

class TestNewsArticle:
    def _make(self, **kwargs) -> NewsArticle:
        return NewsArticle(
            headline="Apple beats Q3 earnings",
            source="Reuters",
            url="https://reuters.com/article",
            published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
            **kwargs,
        )

    def test_defaults(self):
        a = self._make()
        assert a.source_weight == 0.6
        assert a.sentiment is None
        assert a.score is None

    def test_scored_article(self):
        a = self._make(score=0.8, sentiment=ArticleSentiment.POSITIVE)
        assert a.score == 0.8
        assert a.sentiment == ArticleSentiment.POSITIVE

    def test_all_sentiments_valid(self):
        for s in ArticleSentiment:
            assert self._make(sentiment=s).sentiment == s


class TestNewsRequest:
    def test_default_days(self):
        assert NewsRequest(ticker="AAPL").days == 7

    def test_days_below_min_raises(self):
        with pytest.raises(ValidationError):
            NewsRequest(ticker="AAPL", days=0)

    def test_days_above_max_raises(self):
        with pytest.raises(ValidationError):
            NewsRequest(ticker="AAPL", days=31)

    def test_boundary_days_accepted(self):
        assert NewsRequest(ticker="AAPL", days=1).days == 1
        assert NewsRequest(ticker="AAPL", days=30).days == 30


class TestNewsResponse:
    def test_success_shape(self):
        resp = NewsResponse(success=True, duration_ms=1500.0)
        assert resp.success
        assert resp.error is None

    def test_error_shape(self):
        resp = NewsResponse(success=False, error="Finnhub key not set")
        assert not resp.success
        assert resp.sentiment is None


# ── Research models ───────────────────────────────────────────────────────────

class TestResearchRequest:
    def test_thread_id_optional(self):
        req = ResearchRequest(question="What is AAPL revenue?")
        assert req.thread_id is None

    def test_question_required(self):
        with pytest.raises(ValidationError):
            ResearchRequest()


class TestFilingsRequest:
    def test_defaults(self):
        req = FilingsRequest(question="What are the risk factors?")
        assert req.ticker is None
        assert req.k == 5

    def test_with_ticker_and_k(self):
        req = FilingsRequest(question="margins?", ticker="AAPL", k=10)
        assert req.ticker == "AAPL"
        assert req.k == 10
