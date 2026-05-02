from langgraph.graph import END

from app.graph import pick_agents_for_route


class TestPickAgentsForRoute:
    def test_market_route(self):
        assert pick_agents_for_route({"route": "market"}) == ["market_agent"]

    def test_filings_route(self):
        assert pick_agents_for_route({"route": "filings"}) == ["filings_agent"]

    def test_filings_recent_route(self):
        assert pick_agents_for_route({"route": "filings_recent"}) == ["filings_agent"]

    def test_news_route(self):
        assert pick_agents_for_route({"route": "news"}) == ["news_agent"]

    def test_both_route(self):
        assert pick_agents_for_route({"route": "both"}) == ["market_agent", "filings_agent"]

    def test_compare_route(self):
        assert pick_agents_for_route({"route": "compare"}) == ["compare_agent"]

    def test_comprehensive_route_runs_all_agents(self):
        result = pick_agents_for_route({"route": "comprehensive"})
        assert set(result) == {"market_agent", "filings_agent", "news_agent"}

    def test_final_answer_already_set_returns_end(self):
        assert pick_agents_for_route({"route": "market", "final_answer": "cached answer"}) == [END]

    def test_ingest_pending_returns_end(self):
        assert pick_agents_for_route({"route": "market", "ingest_pending": True}) == [END]

    def test_unknown_route_falls_back_gracefully(self):
        result = pick_agents_for_route({"route": "not_a_real_route"})
        assert isinstance(result, list)
        assert len(result) > 0
