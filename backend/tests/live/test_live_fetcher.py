"""
Live tests — require real network access to yfinance and Finnhub.
Run with: pytest -m live
"""
from __future__ import annotations

import pytest

from app.models.financial import DataSource


@pytest.mark.live
class TestLiveMarketData:
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

    @pytest.mark.parametrize("ticker", ["MSFT", "GOOGL", "JPM", "NVDA"])
    def test_fetch_major_tickers(self, ticker):
        from app.tools.market_data import fetch_financial_data
        raw = fetch_financial_data(ticker)
        assert raw.ticker == ticker
        assert raw.current_price is not None


@pytest.mark.live
class TestLiveNewsFetch:
    def test_fetch_returns_list(self):
        from app.tools.news_data import fetch_news
        articles = fetch_news("AAPL", days=7)
        assert isinstance(articles, list)

    def test_articles_have_required_fields(self):
        from app.tools.news_data import fetch_news
        for article in fetch_news("AAPL", days=14)[:5]:
            assert article.headline
            assert article.source
            assert article.published_at is not None
            assert 0.0 < article.source_weight <= 1.0

    def test_invalid_ticker_returns_empty(self):
        from app.tools.news_data import fetch_news
        assert fetch_news("INVALIDXYZ999", days=7) == []
