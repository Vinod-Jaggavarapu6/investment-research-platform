import asyncio
import os

from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI
from typing import Callable, Awaitable
from ..state import AgentState

client = wrap_openai(AsyncOpenAI())

MODEL = os.getenv("SYNTHESIZER_MODEL", "gpt-4o-mini")

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

        stream = await client.chat.completions.create(
            model=MODEL,
            max_tokens=1024,
            stream=True,
            messages=[
                {"role": "system", "content": SYNTH_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Original question: {state['question']}\n\n"
                        f"Research collected:\n{combined}\n\n"
                        "Synthesize a final answer."
                    ),
                },
            ],
        )

        chunks: list[str] = []
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                chunks.append(delta)
                if on_token is not None:
                    # Call immediately — no queue, no batching
                    await on_token(delta)

        return {"final_answer": "".join(chunks)}

    return synthesizer_node
    """
    Returns a synthesizer node function.
    If token_queue is provided, each token is put into the queue as it
    streams — consumed by the SSE generator in streaming.py.
    If token_queue is None (POST /research path), tokens are just
    accumulated internally with no side effects.
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

        stream = await client.chat.completions.create(
            model=MODEL,
            max_tokens=1024,
            stream=True,                    # ← always stream from OpenAI
            messages=[
                {"role": "system", "content": SYNTH_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Original question: {state['question']}\n\n"
                        f"Research collected:\n{combined}\n\n"
                        "Synthesize a final answer."
                    ),
                },
            ],
        )

        chunks: list[str] = []
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                chunks.append(delta)
                if token_queue is not None:
                    await token_queue.put(delta)    # ← feed SSE queue

        return {"final_answer": "".join(chunks)}

    return synthesizer_node