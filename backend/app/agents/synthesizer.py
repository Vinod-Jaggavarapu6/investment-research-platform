import asyncio
import os

import anthropic
from langsmith.wrappers import wrap_anthropic
from typing import Callable, Awaitable
from ..state import AgentState

MODEL      = os.getenv("SYNTHESIZER_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("SYNTHESIZER_MAX_TOKENS", "2048"))

SYNTH_SYSTEM = """You are a senior investment analyst synthesizing research from multiple sources:
1. Live market data (prices, ratios, recent performance)
2. SEC filing excerpts (management guidance, risk factors, financial statements)
3. Recent news sentiment (headlines, catalysts, market buzz)

Not all sources will be present — only synthesize what was collected.
Produce a concise, grounded answer. Where you use filing data, reference the citation.
Where you use news sentiment, note the overall signal and key catalysts.
Be direct. Do not hedge excessively. Flag genuine conflicts between sources."""


def make_synthesizer_node(
    on_token: Callable[[str], Awaitable[None]] | None = None
):
    """
    on_token: async callback called with each token as it arrives.
              Used by the SSE stream to push tokens immediately.
              None for the non-streaming POST /research path.
    """
    async def synthesizer_node(state: AgentState) -> dict:
        parts = []
        if state.get("market_output"):
            parts.append(f"## Live Market Data\n{state['market_output']}")
        if state.get("filings_output"):
            parts.append(f"## SEC Filing Research\n{state['filings_output']}")
        if state.get("news_output"):
            parts.append(f"## Recent News Sentiment\n{state['news_output']}")

        combined = "\n\n".join(parts)

        client = wrap_anthropic(anthropic.AsyncAnthropic())
        chunks: list[str] = []
        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYNTH_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Original question: {state['question']}\n\n"
                        f"Research collected:\n{combined}\n\n"
                        "Synthesize a final answer."
                    ),
                },
            ],
        ) as stream:
            async for text in stream.text_stream:
                chunks.append(text)
                if on_token is not None:
                    await on_token(text)

        return {"final_answer": "".join(chunks)}

    return synthesizer_node