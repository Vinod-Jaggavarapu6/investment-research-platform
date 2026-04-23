"""
test_phase4.py — Tests for Phase 4 News Sentiment Agent

Layer 1: Unit tests  — models, source weights, score aggregation (no network, no LLM)
Layer 2: Integration — live Finnhub + live Claude
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.models import (
    ArticleSentiment,
    NewsArticle,
    NewsRequest,
    NewsResponse,
    NewsSentiment,
    SentimentSignal,
)

from dotenv import load_dotenv
load_dotenv()


# ─────────────────────────────────────────────
# HELPERS — reusable fixtures
# ─────────────────────────────────────────────

def make_article(
    headline:      str   = "Apple beats earnings estimates",
    source:        str   = "Reuters",
    score:         float | None = 0.8,
    sentiment:     ArticleSentiment | None = ArticleSentiment.POSITIVE,
    source_weight: float = 1.0,
) -> NewsArticle:
    return NewsArticle(
        headline     = headline,
        source       = source,
        url          = "https://example.com/article",
        published_at = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
        summary      = "Summary of article.",
        sentiment    = sentiment,
        score        = score,
        justification= "Earnings beat is a direct positive catalyst.",
        source_weight= source_weight,
    )


def make_sentiment(
    ticker:        str   = "AAPL",
    overall_score: float = 0.5,
    signal:        SentimentSignal = SentimentSignal.BULLISH,
    article_count: int   = 5,
    scored_count:  int   = 5,
) -> NewsSentiment:
    return NewsSentiment(
        ticker        = ticker,
        days_analyzed = 7,
        article_count = article_count,
        scored_count  = scored_count,
        overall_score = overall_score,
        signal        = signal,
        bull_catalysts= ["Strong earnings beat"],
        bear_catalysts= ["China headwinds"],
        summary       = "Sentiment is broadly positive driven by earnings.",
    )


# ─────────────────────────────────────────────
# LAYER 1: Unit tests
# ─────────────────────────────────────────────

class TestNewsArticleModel:
    """Verify NewsArticle model construction and defaults."""

    def test_minimal_construction(self):
        article = NewsArticle(
            headline    = "Apple beats Q3 earnings",
            source      = "Reuters",
            url         = "https://reuters.com/article",
            published_at= datetime(2024, 1, 15, tzinfo=timezone.utc),
        )
        assert article.headline == "Apple beats Q3 earnings"
        assert article.source_weight == 0.6     # default weight
        assert article.sentiment is None
        assert article.score is None
        assert article.justification is None

    def test_scored_article(self):
        article = make_article(score=0.8, sentiment=ArticleSentiment.POSITIVE)
        assert article.score == 0.8
        assert article.sentiment == ArticleSentiment.POSITIVE

    def test_negative_score_accepted(self):
        article = make_article(score=-0.7, sentiment=ArticleSentiment.NEGATIVE)
        assert article.score == -0.7

    def test_neutral_score(self):
        article = make_article(
            score=0.0,
            sentiment=ArticleSentiment.NEUTRAL,
            source_weight=0.8,
        )
        assert article.score == 0.0
        assert article.sentiment == ArticleSentiment.NEUTRAL

    def test_all_sentiment_values_valid(self):
        for sentiment in ArticleSentiment:
            article = make_article(sentiment=sentiment)
            assert article.sentiment == sentiment


class TestNewsSentimentModel:
    """Verify NewsSentiment model construction and defaults."""

    def test_minimal_construction(self):
        sentiment = make_sentiment()
        assert sentiment.ticker == "AAPL"
        assert sentiment.days_analyzed == 7
        assert isinstance(sentiment.fetched_at, datetime)
        assert sentiment.data_warning is None
        assert sentiment.articles == []

    def test_insufficient_data_signal(self):
        sentiment = NewsSentiment(
            ticker        = "UNKNOWN",
            days_analyzed = 7,
            article_count = 0,
            scored_count  = 0,
            overall_score = 0.0,
            signal        = SentimentSignal.INSUFFICIENT,
            summary       = "No articles found.",
            data_warning  = "No articles available from Finnhub.",
        )
        assert sentiment.signal == SentimentSignal.INSUFFICIENT
        assert sentiment.data_warning is not None

    def test_all_signals_valid(self):
        for signal in SentimentSignal:
            sentiment = make_sentiment(signal=signal)
            assert sentiment.signal == signal

    def test_bull_bear_catalysts_default_empty(self):
        sentiment = NewsSentiment(
            ticker        = "AAPL",
            days_analyzed = 7,
            article_count = 0,
            scored_count  = 0,
            overall_score = 0.0,
            signal        = SentimentSignal.NEUTRAL,
            summary       = "No news.",
        )
        assert sentiment.bull_catalysts == []
        assert sentiment.bear_catalysts == []

    def test_articles_list_stored(self):
        articles  = [make_article(), make_article(headline="Second article")]
        sentiment = make_sentiment()
        sentiment.articles = articles
        assert len(sentiment.articles) == 2


class TestNewsRequestModel:
    """Verify NewsRequest validation."""

    def test_default_days(self):
        req = NewsRequest(ticker="AAPL")
        assert req.days == 7

    def test_custom_days(self):
        req = NewsRequest(ticker="MSFT", days=14)
        assert req.days == 14

    def test_days_below_minimum_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            NewsRequest(ticker="AAPL", days=0)

    def test_days_above_maximum_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            NewsRequest(ticker="AAPL", days=31)

    def test_days_at_boundaries(self):
        assert NewsRequest(ticker="AAPL", days=1).days  == 1
        assert NewsRequest(ticker="AAPL", days=30).days == 30


class TestNewsResponseModel:
    """Verify NewsResponse envelope."""

    def test_success_response(self):
        sentiment = make_sentiment()
        resp = NewsResponse(success=True, sentiment=sentiment, duration_ms=1234.5)
        assert resp.success is True
        assert resp.sentiment.ticker == "AAPL"
        assert resp.error is None

    def test_error_response(self):
        resp = NewsResponse(success=False, error="Finnhub API key not set")
        assert resp.success is False
        assert resp.sentiment is None
        assert resp.error == "Finnhub API key not set"


class TestSourceWeights:
    """Verify source quality weight lookup."""

    def test_reuters_gets_highest_weight(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("Reuters") == 1.0

    def test_bloomberg_gets_highest_weight(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("Bloomberg") == 1.0

    def test_seeking_alpha_gets_low_weight(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("Seeking Alpha") == 0.5

    def test_unknown_source_gets_default_weight(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("Some Random Blog") == 0.6

    def test_case_insensitive(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("REUTERS") == get_source_weight("reuters")

    def test_partial_match(self):
        # "wall street journal" should match "wsj" substring logic
        from app.tools.news_data import get_source_weight
        weight = get_source_weight("The Wall Street Journal")
        assert weight >= 0.9


class TestScoreToSignal:
    """Verify score → signal boundary mapping."""

    def test_very_bullish(self):
        from app.agents.news_agent import _score_to_signal
        assert _score_to_signal(0.8)  == SentimentSignal.VERY_BULLISH
        assert _score_to_signal(0.6)  == SentimentSignal.VERY_BULLISH

    def test_bullish(self):
        from app.agents.news_agent import _score_to_signal
        assert _score_to_signal(0.5)  == SentimentSignal.BULLISH
        assert _score_to_signal(0.2)  == SentimentSignal.BULLISH

    def test_neutral(self):
        from app.agents.news_agent import _score_to_signal
        assert _score_to_signal(0.0)  == SentimentSignal.NEUTRAL
        assert _score_to_signal(-0.1) == SentimentSignal.NEUTRAL

    def test_bearish(self):
        from app.agents.news_agent import _score_to_signal
        assert _score_to_signal(-0.3) == SentimentSignal.BEARISH
        assert _score_to_signal(-0.6) == SentimentSignal.BEARISH

    def test_very_bearish(self):
        from app.agents.news_agent import _score_to_signal
        assert _score_to_signal(-0.7) == SentimentSignal.VERY_BEARISH
        assert _score_to_signal(-1.0) == SentimentSignal.VERY_BEARISH

    def test_boundary_at_zero_is_neutral(self):
        from app.agents.news_agent import _score_to_signal
        assert _score_to_signal(0.0) == SentimentSignal.NEUTRAL


class TestAggregation:
    """Verify weighted score aggregation logic."""

    def test_single_article_score_equals_overall(self):
        from app.agents.news_agent import _aggregate
        articles = [make_article(score=0.8, source_weight=1.0)]
        result   = _aggregate(articles, ["bull"], ["bear"], "summary", "AAPL", 7)
        assert result.overall_score == 0.8

    def test_weighted_average_favors_high_weight_source(self):
        from app.agents.news_agent import _aggregate
        # Reuters article (weight 1.0, score 0.9) vs Seeking Alpha (weight 0.5, score 0.1)
        # Weighted avg = (0.9*1.0 + 0.1*0.5) / (1.0 + 0.5) = 0.95/1.5 = 0.633
        articles = [
            make_article(score=0.9, source_weight=1.0),
            make_article(score=0.1, source_weight=0.5),
        ]
        result = _aggregate(articles, [], [], "summary", "AAPL", 7)
        assert abs(result.overall_score - 0.6333) < 0.001

    def test_no_scored_articles_returns_insufficient(self):
        from app.agents.news_agent import _aggregate
        articles = [make_article(score=None, sentiment=None)]
        result   = _aggregate(articles, [], [], "", "AAPL", 7)
        assert result.signal       == SentimentSignal.INSUFFICIENT
        assert result.scored_count == 0

    def test_article_count_vs_scored_count(self):
        from app.agents.news_agent import _aggregate
        articles = [
            make_article(score=0.8),
            make_article(score=None, sentiment=None),   # unscored
            make_article(score=0.4),
        ]
        result = _aggregate(articles, [], [], "summary", "AAPL", 7)
        assert result.article_count == 3
        assert result.scored_count  == 2

    def test_bull_bear_catalysts_capped_at_four(self):
        from app.agents.news_agent import _aggregate
        articles   = [make_article(score=0.5)]
        bull       = ["a", "b", "c", "d", "e"]     # 5 items, should be capped at 4
        result     = _aggregate(articles, bull, [], "summary", "AAPL", 7)
        assert len(result.bull_catalysts) == 4

    def test_negative_overall_score_gives_bearish_signal(self):
        from app.agents.news_agent import _aggregate
        articles = [make_article(score=-0.7, sentiment=ArticleSentiment.NEGATIVE)]
        result   = _aggregate(articles, [], ["revenue miss"], "summary", "AAPL", 7)
        assert result.signal in (SentimentSignal.BEARISH, SentimentSignal.VERY_BEARISH)


class TestNewsSentimentEndpoint:
    """Test /news/sentiment endpoint with mocked agent."""

    @pytest.fixture
    def client(self, monkeypatch):
        from app.main import app as fastapi_app

        mock_result = NewsResponse(
            success   = True,
            sentiment = make_sentiment(ticker="AAPL", overall_score=0.65),
            duration_ms = 1500.0,
        )

        monkeypatch.setattr(
            "app.agents.news_agent.analyze_news_sentiment",
            lambda ticker, days: mock_result,
        )

        return TestClient(fastapi_app)

    def test_returns_200(self, client):
        resp = client.post("/news/sentiment", json={"ticker": "AAPL"})
        assert resp.status_code == 200

    def test_response_shape(self, client):
        resp = client.post("/news/sentiment", json={"ticker": "AAPL"})
        data = resp.json()
        assert data["success"] is True
        assert "sentiment" in data
        assert data["sentiment"]["ticker"] == "AAPL"

    def test_sentiment_fields_present(self, client):
        resp = client.post("/news/sentiment", json={"ticker": "AAPL"})
        s    = resp.json()["sentiment"]
        assert "overall_score"  in s
        assert "signal"         in s
        assert "bull_catalysts" in s
        assert "bear_catalysts" in s
        assert "summary"        in s
        assert "article_count"  in s
        assert "scored_count"   in s

    def test_missing_ticker_returns_422(self, client):
        resp = client.post("/news/sentiment", json={})
        assert resp.status_code == 422

    def test_days_out_of_range_returns_422(self, client):
        resp = client.post("/news/sentiment", json={"ticker": "AAPL", "days": 31})
        assert resp.status_code == 422

    def test_ticker_uppercased(self, client):
        resp = client.post("/news/sentiment", json={"ticker": "aapl"})
        assert resp.status_code == 200

    def test_custom_days_accepted(self, client):
        resp = client.post("/news/sentiment", json={"ticker": "AAPL", "days": 14})
        assert resp.status_code == 200

    def test_error_response_raises_503(self, monkeypatch):
        from app.main import app as fastapi_app

        monkeypatch.setattr(
            "app.agents.news_agent.analyze_news_sentiment",
            lambda ticker, days: NewsResponse(
                success=False,
                error="Finnhub API key not set",
            ),
        )
        test_client = TestClient(fastapi_app)
        resp = test_client.post("/news/sentiment", json={"ticker": "AAPL"})
        assert resp.status_code == 503


# ─────────────────────────────────────────────
# LAYER 2: Integration tests
# ─────────────────────────────────────────────

@pytest.mark.integration
class TestLiveFinnhub:
    """Tests that call real Finnhub API. Require FINNHUB_API_KEY."""

    def test_fetch_news_returns_articles(self):
        from app.tools.news_data import fetch_news
        articles = fetch_news("AAPL", days=7)
        # May be empty on weekends/holidays — just verify no exception
        assert isinstance(articles, list)

    def test_fetch_news_articles_have_required_fields(self):
        from app.tools.news_data import fetch_news
        articles = fetch_news("AAPL", days=14)
        for article in articles[:5]:        # check first 5
            assert article.headline
            assert article.source
            assert article.published_at is not None
            assert 0.0 < article.source_weight <= 1.0

    def test_fetch_news_sorted_newest_first(self):
        from app.tools.news_data import fetch_news
        articles = fetch_news("AAPL", days=14)
        if len(articles) >= 2:
            assert articles[0].published_at >= articles[1].published_at

    def test_fetch_news_invalid_ticker_returns_empty(self):
        from app.tools.news_data import fetch_news
        articles = fetch_news("INVALIDXYZ999", days=7)
        assert articles == []

    @pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "NVDA"])
    def test_fetch_news_multiple_tickers(self, ticker):
        from app.tools.news_data import fetch_news
        articles = fetch_news(ticker, days=7)
        assert isinstance(articles, list)


@pytest.mark.integration
class TestLiveNewsAgent:
    """Tests that call real Finnhub + real Claude."""

    def test_analyze_aapl_returns_success(self):
        from app.agents.news_agent import analyze_news_sentiment
        result = analyze_news_sentiment("AAPL", days=7)

        assert result.success is True
        assert result.sentiment is not None
        assert result.sentiment.ticker == "AAPL"
        assert result.sentiment.signal in list(SentimentSignal)
        assert -1.0 <= result.sentiment.overall_score <= 1.0
        assert result.duration_ms is not None

    def test_analyze_returns_catalysts(self):
        from app.agents.news_agent import analyze_news_sentiment
        result = analyze_news_sentiment("AAPL", days=7)

        if result.sentiment.signal != SentimentSignal.INSUFFICIENT:
            # Should have at least one catalyst when articles exist
            total_catalysts = (
                len(result.sentiment.bull_catalysts) +
                len(result.sentiment.bear_catalysts)
            )
            assert total_catalysts >= 1

    def test_analyze_scored_count_lte_article_count(self):
        from app.agents.news_agent import analyze_news_sentiment
        result = analyze_news_sentiment("MSFT", days=7)

        assert result.success is True
        assert result.sentiment.scored_count <= result.sentiment.article_count

    def test_analyze_invalid_ticker_returns_insufficient(self):
        from app.agents.news_agent import analyze_news_sentiment
        result = analyze_news_sentiment("INVALIDXYZ999", days=7)

        # Should succeed but with INSUFFICIENT signal — not crash
        assert result.success is True
        assert result.sentiment.signal == SentimentSignal.INSUFFICIENT

    def test_summary_is_non_empty_when_articles_exist(self):
        from app.agents.news_agent import analyze_news_sentiment
        result = analyze_news_sentiment("NVDA", days=14)

        if result.sentiment.article_count > 0:
            assert len(result.sentiment.summary) > 50

    @pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "NVDA", "TSLA"])
    def test_multiple_tickers(self, ticker):
        from app.agents.news_agent import analyze_news_sentiment
        result = analyze_news_sentiment(ticker, days=7)

        assert result.success, f"{ticker} failed: {result.error}"
        assert result.sentiment.signal in list(SentimentSignal)