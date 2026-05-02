from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.news import ArticleSentiment, NewsArticle, SentimentSignal


def _article(
    score: float | None = 0.8,
    sentiment: ArticleSentiment | None = ArticleSentiment.POSITIVE,
    source_weight: float = 1.0,
    headline: str = "Test headline",
) -> NewsArticle:
    return NewsArticle(
        headline=headline,
        source="Reuters",
        url="https://example.com/article",
        published_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
        score=score,
        sentiment=sentiment,
        source_weight=source_weight,
    )


class TestSourceWeights:
    def test_reuters_top_tier(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("Reuters") == 1.0

    def test_bloomberg_top_tier(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("Bloomberg") == 1.0

    def test_seeking_alpha_low_tier(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("Seeking Alpha") == 0.5

    def test_unknown_source_returns_default(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("Some Random Blog") == 0.6

    def test_case_insensitive(self):
        from app.tools.news_data import get_source_weight
        assert get_source_weight("REUTERS") == get_source_weight("reuters")


class TestScoreToSignal:
    def _sig(self, score: float) -> SentimentSignal:
        from app.agents.news_agent import _score_to_signal
        return _score_to_signal(score)

    def test_very_bullish(self):
        assert self._sig(0.8) == SentimentSignal.VERY_BULLISH
        assert self._sig(0.6) == SentimentSignal.VERY_BULLISH

    def test_bullish(self):
        assert self._sig(0.5) == SentimentSignal.BULLISH
        assert self._sig(0.2) == SentimentSignal.BULLISH

    def test_neutral(self):
        assert self._sig(0.0) == SentimentSignal.NEUTRAL
        assert self._sig(-0.1) == SentimentSignal.NEUTRAL

    def test_bearish(self):
        assert self._sig(-0.3) == SentimentSignal.BEARISH
        assert self._sig(-0.6) == SentimentSignal.BEARISH

    def test_very_bearish(self):
        assert self._sig(-0.7) == SentimentSignal.VERY_BEARISH
        assert self._sig(-1.0) == SentimentSignal.VERY_BEARISH

    def test_zero_boundary_is_neutral(self):
        assert self._sig(0.0) == SentimentSignal.NEUTRAL


class TestAggregation:
    def _agg(self, articles, bull=None, bear=None, summary="", ticker="AAPL", days=7):
        from app.agents.news_agent import _aggregate
        return _aggregate(articles, bull or [], bear or [], summary, ticker, days)

    def test_single_article(self):
        result = self._agg([_article(score=0.8, source_weight=1.0)])
        assert result.overall_score == 0.8

    def test_weighted_average(self):
        # (0.9*1.0 + 0.1*0.5) / (1.0+0.5) = 0.95/1.5 ≈ 0.633
        articles = [_article(score=0.9, source_weight=1.0), _article(score=0.1, source_weight=0.5)]
        result = self._agg(articles)
        assert abs(result.overall_score - 0.6333) < 0.001

    def test_no_scored_articles_returns_insufficient(self):
        result = self._agg([_article(score=None, sentiment=None)])
        assert result.signal == SentimentSignal.INSUFFICIENT
        assert result.scored_count == 0

    def test_article_count_vs_scored_count(self):
        articles = [_article(score=0.8), _article(score=None, sentiment=None), _article(score=0.4)]
        result = self._agg(articles)
        assert result.article_count == 3
        assert result.scored_count == 2

    def test_bull_catalysts_capped_at_four(self):
        result = self._agg([_article(score=0.5)], bull=["a", "b", "c", "d", "e"])
        assert len(result.bull_catalysts) == 4

    def test_negative_score_gives_bearish_signal(self):
        result = self._agg([_article(score=-0.7, sentiment=ArticleSentiment.NEGATIVE)])
        assert result.signal in (SentimentSignal.BEARISH, SentimentSignal.VERY_BEARISH)

    def test_ticker_and_days_stored(self):
        result = self._agg([_article()], ticker="NVDA", days=14)
        assert result.ticker == "NVDA"
        assert result.days_analyzed == 14
