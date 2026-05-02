"""
Live tests — require real LLM API calls (Anthropic + OpenAI).
Run with: pytest -m live
"""
from __future__ import annotations

import pytest

from app.models.financial import SignalStrength
from app.models.news import SentimentSignal


@pytest.mark.live
class TestLiveFinancialAgent:
    def test_analyze_aapl(self):
        from app.agents.financial_agent import analyze_ticker
        result = analyze_ticker("AAPL")
        assert result.success is True
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

    @pytest.mark.parametrize("ticker", ["MSFT", "GOOGL", "NVDA", "TSLA", "JPM"])
    def test_analyze_major_tickers(self, ticker):
        from app.agents.financial_agent import analyze_ticker
        result = analyze_ticker(ticker)
        assert result.success, f"{ticker} failed: {result.error}"
        assert result.snapshot.signal != SignalStrength.INSUFFICIENT_DATA


@pytest.mark.live
class TestLiveNewsAgent:
    def test_analyze_aapl_returns_success(self):
        from app.agents.news_agent import analyze_news_sentiment
        result = analyze_news_sentiment("AAPL", days=7)
        assert result.success is True
        assert result.sentiment.ticker == "AAPL"
        assert result.sentiment.signal in list(SentimentSignal)
        assert -1.0 <= result.sentiment.overall_score <= 1.0

    def test_scored_count_lte_article_count(self):
        from app.agents.news_agent import analyze_news_sentiment
        result = analyze_news_sentiment("MSFT", days=7)
        assert result.sentiment.scored_count <= result.sentiment.article_count

    def test_invalid_ticker_returns_insufficient(self):
        from app.agents.news_agent import analyze_news_sentiment
        result = analyze_news_sentiment("INVALIDXYZ999", days=7)
        assert result.success is True
        assert result.sentiment.signal == SentimentSignal.INSUFFICIENT


@pytest.mark.live
class TestLiveRouterClassification:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("question,expected_route", [
        ("What is Apple's current stock price?", "market"),
        ("What risk factors did NVDA disclose in their 10-K?", "filings"),
        ("Is NVDA valuation justified given 10-K guidance?", "both"),
        ("News sentiment around TSLA?", "news"),
        ("Compare AAPL vs MSFT on margins", "compare"),
    ])
    async def test_router_classifies_correctly(self, question, expected_route):
        from app.agents.router_agent import router_node
        result = await router_node({"question": question})
        assert result["route"] == expected_route, (
            f"Q: {question!r} — expected {expected_route!r}, got {result['route']!r}"
        )
