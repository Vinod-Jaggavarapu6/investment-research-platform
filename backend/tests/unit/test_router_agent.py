from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_llm_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


def _patch_router(monkeypatch, text: str):
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_mock_llm_response(text))
    monkeypatch.setattr("app.agents.router_agent.get_openai_async", MagicMock(return_value=mock_client))
    return mock_client


class TestRouterNode:
    @pytest.mark.asyncio
    async def test_market_question(self, monkeypatch):
        from app.agents.router_agent import router_node
        _patch_router(monkeypatch, '{"route": "market", "ticker": "AAPL", "tickers": null}')

        result = await router_node({"question": "What is AAPL's current price?"})

        assert result["route"] == "market"
        assert result["ticker"] == "AAPL"

    @pytest.mark.asyncio
    async def test_filings_question(self, monkeypatch):
        from app.agents.router_agent import router_node
        _patch_router(monkeypatch, '{"route": "filings", "ticker": "MSFT", "tickers": null}')

        result = await router_node({"question": "What risk factors did Microsoft disclose?"})

        assert result["route"] == "filings"
        assert result["ticker"] == "MSFT"

    @pytest.mark.asyncio
    async def test_compare_question(self, monkeypatch):
        from app.agents.router_agent import router_node
        _patch_router(monkeypatch, '{"route": "compare", "ticker": null, "tickers": ["AAPL", "MSFT"]}')

        result = await router_node({"question": "Compare AAPL vs MSFT on risk factors"})

        assert result["route"] == "compare"
        assert result["tickers"] == ["AAPL", "MSFT"]
        assert result["ticker"] is None

    @pytest.mark.asyncio
    async def test_malformed_json_falls_back_to_both(self, monkeypatch):
        from app.agents.router_agent import router_node
        _patch_router(monkeypatch, "not valid json at all")

        result = await router_node({"question": "anything"})

        assert result["route"] == "both"
        assert result["ticker"] is None

    @pytest.mark.asyncio
    async def test_invalid_route_value_falls_back_to_both(self, monkeypatch):
        from app.agents.router_agent import router_node
        _patch_router(monkeypatch, '{"route": "invented_route", "ticker": "AAPL", "tickers": null}')

        result = await router_node({"question": "anything"})

        assert result["route"] == "both"

    @pytest.mark.asyncio
    async def test_compare_with_one_ticker_falls_back_to_filings(self, monkeypatch):
        from app.agents.router_agent import router_node
        _patch_router(monkeypatch, '{"route": "compare", "ticker": null, "tickers": ["AAPL"]}')

        result = await router_node({"question": "compare AAPL"})

        assert result["route"] == "filings"

    @pytest.mark.asyncio
    async def test_router_clears_stale_state(self, monkeypatch):
        from app.agents.router_agent import router_node
        _patch_router(monkeypatch, '{"route": "market", "ticker": "AAPL", "tickers": null}')

        result = await router_node({
            "question": "price of AAPL?",
            "final_answer": "stale answer from last turn",
            "market_output": "stale market data",
        })

        assert result["final_answer"] is None
        assert result["market_output"] is None

    @pytest.mark.asyncio
    async def test_follow_up_inherits_prev_ticker(self, monkeypatch):
        from app.agents.router_agent import router_node
        _patch_router(monkeypatch, '{"route": "filings", "ticker": null, "tickers": null}')

        result = await router_node({
            "question": "What about their debt levels?",
            "ticker": "NVDA",
        })

        assert result["ticker"] == "NVDA"
