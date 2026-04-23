from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.models import (
    AnalysisResponse,
    FinancialSnapshot,
    SignalStrength,
)


# ─────────────────────────────────────────────
# LAYER 1: Unit tests
# No network. No LLM. No DB. Run instantly.
# ─────────────────────────────────────────────

class TestRouterNode:
    """Test router classification and ticker extraction in isolation."""

    @pytest.mark.asyncio
    async def test_routes_market_question(self):
        from app.agents.router_agent import router_node

        mock_response = MagicMock()                          # plain mock, not AsyncMock
        mock_response.content = [MagicMock(text='{"route": "market", "ticker": "AAPL"}')]


        with patch("app.agents.router_agent.client.messages.create", new=AsyncMock(return_value=mock_response)):
            result = await router_node({"question": "What is the current price of AAPL?"})

        assert result["route"] == "market"
        assert result["ticker"] == "AAPL"

    @pytest.mark.asyncio
    async def test_routes_filings_question(self):
        from app.agents.router_agent import router_node

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"route": "filings", "ticker": "MSFT"}')]

        with patch("app.agents.router_agent.client.messages.create", new=AsyncMock(return_value=mock_response)):
            result = await router_node({"question": "What risk factors did Microsoft disclose?"})

        assert result["route"] == "filings"
        assert result["ticker"] == "MSFT"

    @pytest.mark.asyncio
    async def test_routes_both_question(self):
        from app.agents.router_agent import router_node

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"route": "both", "ticker": "NVDA"}')]

        with patch("app.agents.router_agent.client.messages.create", new=AsyncMock(return_value=mock_response)):
            result = await router_node({"question": "Is NVDA valuation justified vs 10-K guidance?"})

        assert result["route"] == "both"
        assert result["ticker"] == "NVDA"

    @pytest.mark.asyncio
    async def test_null_ticker_for_generic_question(self):
        from app.agents.router_agent import router_node

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"route": "filings", "ticker": null}')]

        with patch("app.agents.router_agent.client.messages.create", new=AsyncMock(return_value=mock_response)):
            result = await router_node({"question": "What are common risk factors in 10-K filings?"})

        assert result["ticker"] is None

    @pytest.mark.asyncio
    async def test_malformed_json_falls_back_to_both(self):
        """If Claude returns garbage, router should default to 'both' safely."""
        from app.agents.router_agent import router_node

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="both")]   # not JSON

        with patch("app.agents.router_agent.client.messages.create", new=AsyncMock(return_value=mock_response)):
            result = await router_node({"question": "anything"})

        assert result["route"] == "both"
        assert result["ticker"] is None

    @pytest.mark.asyncio
    async def test_invalid_route_value_falls_back_to_both(self):
        """Route values outside the allowed set should default to 'both'."""
        from app.agents.router_agent import router_node

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"route": "unknown", "ticker": "AAPL"}')]

        with patch("app.agents.router_agent.client.messages.create", new=AsyncMock(return_value=mock_response)):
            result = await router_node({"question": "anything"})

        assert result["route"] == "both"


class TestRoutingLogic:
    """Test the graph edge functions independently of LangGraph."""

    def test_pick_next_after_router_market(self):
        from app.graph import pick_next_after_router
        assert pick_next_after_router({"route": "market"}) == "market_agent"

    def test_pick_next_after_router_filings(self):
        from app.graph import pick_next_after_router
        assert pick_next_after_router({"route": "filings"}) == "filings_agent"

    def test_pick_next_after_router_both(self):
        from app.graph import pick_next_after_router
        assert pick_next_after_router({"route": "both"}) == "market_agent"

    def test_pick_next_after_router_missing_defaults_to_both(self):
        from app.graph import pick_next_after_router
        assert pick_next_after_router({}) == "market_agent"

    def test_pick_next_after_market_goes_to_filings_when_both(self):
        from app.graph import pick_next_after_market
        assert pick_next_after_market({"route": "both"}) == "filings_agent"

    def test_pick_next_after_market_goes_to_synthesizer_when_market_only(self):
        from app.graph import pick_next_after_market
        assert pick_next_after_market({"route": "market"}) == "synthesizer"


class TestMarketNode:
    """Test make_market_node wrapper behavior."""

    @pytest.mark.asyncio
    async def test_no_ticker_returns_fallback(self):
        from app.agents.financial_agent import make_market_node

        node = make_market_node()
        result = await node({"question": "How is the market doing?"})

        assert "market_output" in result
        assert "No ticker" in result["market_output"]

    @pytest.mark.asyncio
    async def test_failed_analysis_returns_error_string(self):
        from app.agents.financial_agent import make_market_node

        failed_response = AnalysisResponse(
            success=False,
            error="yfinance returned no data for 'FAKE'"
        )

        with patch("app.agents.financial_agent.analyze_ticker", return_value=failed_response):
            node = make_market_node()
            result = await node({"question": "...", "ticker": "FAKE"})

        assert "failed" in result["market_output"].lower()

    @pytest.mark.asyncio
    async def test_successful_analysis_returns_json(self):
        from app.agents.financial_agent import make_market_node

        snap = FinancialSnapshot(
            ticker="AAPL",
            signal=SignalStrength.BUY,
            analysis_summary="Apple's 27% net margin leads large-cap tech.",
            key_strengths=["27% net margin"],
            key_risks=["China concentration risk"],
        )
        success_response = AnalysisResponse(success=True, snapshot=snap, duration_ms=100.0)

        with patch("app.agents.financial_agent.analyze_ticker", return_value=success_response):
            node = make_market_node()
            result = await node({"question": "...", "ticker": "AAPL"})

        import json
        parsed = json.loads(result["market_output"])
        assert parsed["snapshot"]["ticker"] == "AAPL"


class TestResearchEndpoint:
    """
    Test the /research endpoint with all agents mocked.
    Verifies routing, response shape, and error handling.
    """

    @pytest.fixture
    def client(self, monkeypatch):
        from app.main import app as fastapi_app   # ← rename to avoid collision
        from app.database import get_db
        import app.graph

        mock_graph = AsyncMock()
        mock_graph.ainvoke = AsyncMock(return_value={
            "route":         "both",
            "ticker":        "AAPL",
            "market_output": '{"snapshot": {"ticker": "AAPL", "signal": "BUY"}}',
            "filings_output": "Apple disclosed strong iPhone demand [AAPL 2024, Item 1]",
            "citations":     [{"ticker": "AAPL", "year": 2024, "section": "Item 1", "score": 0.91}],
            "final_answer":  "AAPL trades at 28x forward P/E. Management guided 8% revenue growth.",
        })

        monkeypatch.setattr(app.graph, "build_graph", lambda db: mock_graph)

        async def override_get_db():
            yield AsyncMock()

        fastapi_app.dependency_overrides[get_db] = override_get_db

        yield TestClient(fastapi_app)

        fastapi_app.dependency_overrides.clear()

    def test_research_returns_200(self, client):
        resp = client.post("/research", json={"question": "Is AAPL fairly valued?"})
        assert resp.status_code == 200

    def test_research_response_shape(self, client):
        resp = client.post("/research", json={"question": "Is AAPL fairly valued?"})
        data = resp.json()

        assert "thread_id" in data
        assert "route" in data
        assert "final_answer" in data
        assert "citations" in data

    def test_research_thread_id_is_uuid(self, client):
        import uuid
        resp = client.post("/research", json={"question": "Is AAPL fairly valued?"})
        thread_id = resp.json()["thread_id"]
        uuid.UUID(thread_id)   # raises if not valid UUID

    def test_research_reuses_provided_thread_id(self, client):
        thread_id = "test-thread-abc-123"
        resp = client.post("/research", json={
            "question":  "Is AAPL fairly valued?",
            "thread_id": thread_id,
        })
        assert resp.json()["thread_id"] == thread_id

    def test_research_missing_question_returns_422(self, client):
        resp = client.post("/research", json={})
        assert resp.status_code == 422

    def test_research_citations_is_list(self, client):
        resp = client.post("/research", json={"question": "Is AAPL fairly valued?"})
        assert isinstance(resp.json()["citations"], list)


# ─────────────────────────────────────────────
# LAYER 2: Integration tests
# Real LLM calls. Real DB. Require network.
# ─────────────────────────────────────────────

@pytest.mark.integration
class TestLiveRouterNode:
    """Router against real Claude — verify classification quality."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("question,expected_route", [
        ("What is Apple's current stock price?",                     "market"),
        ("What is MSFT trading at right now?",                       "market"),
        ("What risk factors did NVDA disclose in their 10-K?",       "filings"),
        ("What did Apple management say about margins in the 10-K?", "filings"),
        ("Is NVDA valuation justified given 10-K guidance?",         "both"),
        ("Compare AAPL's current P/E to what they guided for.",      "both"),
    ])
    async def test_router_classifies_correctly(self, question, expected_route):
        from app.agents.router_agent import router_node
        result = await router_node({"question": question})
        assert result["route"] == expected_route, (
            f"Question: {question!r}\n"
            f"Expected: {expected_route}, Got: {result['route']}"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("question,expected_ticker", [
        ("What is Apple's current stock price?",               "AAPL"),
        ("What risk factors did Microsoft disclose?",          "MSFT"),
        ("Is Nvidia valuation justified given their 10-K?",    "NVDA"),
    ])
    async def test_router_extracts_ticker(self, question, expected_ticker):
        from app.agents.router_agent import router_node
        result = await router_node({"question": question})
        assert result["ticker"] == expected_ticker


@pytest.mark.integration
class TestLiveResearchPipeline:
    """
    Full end-to-end pipeline tests.
    Real Claude + real yfinance + real FAISS + real Postgres.
    """

    @pytest.fixture
    async def db(self):
        from app.database import get_db
        async for session in get_db():
            yield session

    @pytest.mark.asyncio
    async def test_market_route_end_to_end(self, db):
        from app.graph import build_graph
        graph = build_graph(db=db)

        result = await graph.ainvoke(
            {"question": "What is the current P/E ratio of MSFT?"}
        )

        assert result["route"] == "market"
        assert result["final_answer"]
        assert result["citations"] == [] or result["citations"] is None

    @pytest.mark.asyncio
    async def test_filings_route_end_to_end(self, db):
        from app.graph import build_graph
        graph = build_graph(db=db)

        result = await graph.ainvoke(
            {"question": "What were the main risk factors Apple disclosed in their 10-K?"}
        )

        assert result["route"] == "filings"
        assert result["final_answer"]
        assert len(result["citations"]) > 0

    @pytest.mark.asyncio
    async def test_both_route_end_to_end(self, db):
        from app.graph import build_graph
        graph = build_graph(db=db)

        result = await graph.ainvoke(
            {"question": "Is NVDA's current valuation justified given what management guided in their last 10-K?"}
        )

        assert result["route"] == "both"
        assert result["final_answer"]
        assert len(result["final_answer"]) > 100   # substantive answer
        assert len(result["citations"]) > 0         # grounded in filings

    @pytest.mark.asyncio
    async def test_final_answer_is_non_empty_for_all_routes(self, db):
        from app.graph import build_graph

        questions = [
            "What is AAPL's current market cap?",
            "What did Google disclose about AI risks in their 10-K?",
            "Is TSLA fairly valued relative to their growth guidance?",
        ]
        for question in questions:
            graph = build_graph(db=db)
            result = await graph.ainvoke({"question": question})
            assert result.get("final_answer"), f"No answer for: {question!r}"