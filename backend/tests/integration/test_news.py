from __future__ import annotations

import pytest

from app.models.news import NewsResponse, NewsSentiment, SentimentSignal


def _mock_sentiment(ticker: str = "AAPL") -> NewsSentiment:
    return NewsSentiment(
        ticker=ticker,
        days_analyzed=7,
        article_count=10,
        scored_count=10,
        overall_score=0.65,
        signal=SentimentSignal.BULLISH,
        bull_catalysts=["Strong earnings beat"],
        bear_catalysts=["China headwinds"],
        summary="Sentiment broadly positive driven by earnings.",
    )


@pytest.fixture
def news_client(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.news.analyze_news_sentiment",
        lambda ticker, days: NewsResponse(
            success=True, sentiment=_mock_sentiment(ticker), duration_ms=1500.0
        ),
    )
    return client


class TestNewsSentimentEndpoint:
    def test_returns_200(self, news_client):
        assert news_client.post("/news/sentiment", json={"ticker": "AAPL"}).status_code == 200

    def test_response_shape(self, news_client):
        data = news_client.post("/news/sentiment", json={"ticker": "AAPL"}).json()
        assert data["success"] is True
        s = data["sentiment"]
        assert s["ticker"] == "AAPL"
        assert "overall_score" in s
        assert "signal" in s
        assert "bull_catalysts" in s
        assert "bear_catalysts" in s

    def test_lowercase_ticker_accepted(self, news_client):
        assert news_client.post("/news/sentiment", json={"ticker": "aapl"}).status_code == 200

    def test_custom_days_accepted(self, news_client):
        assert news_client.post("/news/sentiment", json={"ticker": "AAPL", "days": 14}).status_code == 200

    def test_missing_ticker_returns_422(self, news_client):
        assert news_client.post("/news/sentiment", json={}).status_code == 422

    def test_days_above_max_returns_422(self, news_client):
        assert news_client.post("/news/sentiment", json={"ticker": "AAPL", "days": 31}).status_code == 422

    def test_agent_error_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.routers.news.analyze_news_sentiment",
            lambda ticker, days: NewsResponse(success=False, error="Finnhub API key not set"),
        )
        assert client.post("/news/sentiment", json={"ticker": "AAPL"}).status_code == 503
