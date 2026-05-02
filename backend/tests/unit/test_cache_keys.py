from app.cache.cache_keys import (
    full_report_key, market_data_key, news_sentiment_key, rag_answer_key,
)


class TestMarketDataKey:
    def test_format(self):
        assert market_data_key("AAPL") == "research:market:AAPL:v1"

    def test_uppercases_ticker(self):
        assert market_data_key("aapl") == "research:market:AAPL:v1"

    def test_versioned(self):
        assert market_data_key("MSFT").endswith(":v1")


class TestNewsSentimentKey:
    def test_format(self):
        assert news_sentiment_key("TSLA") == "research:news:TSLA:v1"

    def test_uppercases_ticker(self):
        assert news_sentiment_key("tsla") == "research:news:TSLA:v1"


class TestRagAnswerKey:
    def test_format(self):
        key = rag_answer_key("AAPL", "what are the risk factors")
        assert key.startswith("research:rag:AAPL:")
        assert key.endswith(":v1")

    def test_query_case_insensitive(self):
        assert rag_answer_key("AAPL", "Risk Factors") == rag_answer_key("AAPL", "risk factors")

    def test_different_queries_produce_different_keys(self):
        assert rag_answer_key("AAPL", "revenue?") != rag_answer_key("AAPL", "risks?")

    def test_different_tickers_produce_different_keys(self):
        assert rag_answer_key("AAPL", "margins") != rag_answer_key("MSFT", "margins")


class TestFullReportKey:
    def test_with_query(self):
        key = full_report_key("AAPL", "What is the P/E ratio?")
        assert key.startswith("research:report:AAPL:")
        assert key.endswith(":v1")

    def test_same_query_deterministic(self):
        q = "What are the risk factors?"
        assert full_report_key("AAPL", q) == full_report_key("AAPL", q)

    def test_different_questions_different_keys(self):
        assert full_report_key("AAPL", "revenue?") != full_report_key("AAPL", "risks?")

    def test_without_query_returns_default(self):
        assert full_report_key("AAPL") == "research:report:AAPL:default:v1"

    def test_empty_ticker(self):
        key = full_report_key("", "any question")
        assert "research:report:" in key
