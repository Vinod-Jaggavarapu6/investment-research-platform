"""
Live end-to-end pipeline tests — require a running DB, Redis, and all LLM APIs.
Run with: pytest -m live
"""
from __future__ import annotations

import pytest


@pytest.fixture
async def db():
    from app.database import get_db
    async for session in get_db():
        yield session


@pytest.mark.live
class TestLivePipeline:
    @pytest.mark.asyncio
    async def test_market_route_end_to_end(self, db):
        from app.graph import build_graph
        result = await build_graph(db=db).ainvoke(
            {"question": "What is the current P/E ratio of MSFT?"}
        )
        assert result["route"] == "market"
        assert result["final_answer"]

    @pytest.mark.asyncio
    async def test_filings_route_end_to_end(self, db):
        from app.graph import build_graph
        result = await build_graph(db=db).ainvoke(
            {"question": "What were the main risk factors Apple disclosed in their 10-K?"}
        )
        assert result["route"] == "filings"
        assert result["final_answer"]
        assert len(result.get("citations") or []) > 0

    @pytest.mark.asyncio
    async def test_both_route_end_to_end(self, db):
        from app.graph import build_graph
        result = await build_graph(db=db).ainvoke(
            {"question": "Is NVDA's valuation justified given their 10-K guidance?"}
        )
        assert result["route"] == "both"
        assert len(result["final_answer"]) > 100
        assert len(result.get("citations") or []) > 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("question", [
        "What is AAPL's current market cap?",
        "What did Google disclose about AI risks in their 10-K?",
        "Is TSLA fairly valued relative to their growth guidance?",
    ])
    async def test_all_routes_produce_answer(self, db, question):
        from app.graph import build_graph
        result = await build_graph(db=db).ainvoke({"question": question})
        assert result.get("final_answer"), f"No answer for: {question!r}"
