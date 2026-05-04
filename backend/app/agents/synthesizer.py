import os
from typing import Callable, Awaitable

import structlog

from ..clients import get_anthropic_async
from ..metrics import llm_tokens_total
from ..state import AgentState
from .base import node_error

logger = structlog.get_logger(__name__)

MODEL      = os.getenv("SYNTHESIZER_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("SYNTHESIZER_MAX_TOKENS", "2048"))

SYNTH_SYSTEM = """You are a senior investment analyst synthesizing research from multiple sources:
1. Live market data (prices, ratios, recent performance)
2. SEC filing excerpts (management guidance, risk factors, financial statements)
3. Recent news sentiment (headlines, catalysts, market buzz)

Not all sources will be present — only synthesize what was collected.
Produce a concise, grounded answer. Where you use filing data, reference the citation.
Where you use news sentiment, note the overall signal and key catalysts.
Be direct. Do not hedge excessively. Flag genuine conflicts between sources.

## Output format

Structure your answer in the following order:
1. **Bottom-line verdict** (1–2 sentences): the single most important takeaway.
2. **Key findings by source** (use headers for each present source):
   - Market data: highlight the most relevant metric(s) for the question.
   - SEC filings: cite specific disclosure language with [TICKER YEAR TYPE, Section].
   - News sentiment: summarize the prevailing signal and name 1–2 catalysts.
3. **Conflicts or caveats**: flag any meaningful disagreement between sources (e.g., bullish filings guidance vs. negative news sentiment). If none, omit this section.
4. **Analyst note** (optional): one sentence of forward-looking framing only when the data clearly supports it.

## Citation rules
- Every claim drawn from a filing must be followed by a citation: [TICKER YEAR TYPE, Section].
  Examples: [AAPL 2024 10-K, Item 1A] · [NVDA 2024 10-Q, MD&A] · [TSLA 2024 8-K, Item 8.01]
- News citations: reference the source signal as (News, [date or recency]) when available.
- Do not cite live market data — state it as current metrics.

## Tone and length
- Write for a sophisticated investor, not a retail audience.
- Avoid hedging phrases like "it's worth noting" or "it's important to consider".
- Keep the answer under 400 words unless the question explicitly asks for depth.
- Use **bold** for company names and key metrics on first mention.

## Conversation follow-ups
- Prior Q&A turns may appear before the current research message.
- Use them to resolve references like "that figure", "point 3", "you mentioned X", or "elaborate on Y".
- If the follow-up question is better answered from the prior answer than from the new research, do so — but still incorporate any new data retrieved.

## Edge cases
- If a source is absent, skip its section entirely — do not mention its absence unless it is material to answering the question.
- If the question asks about a forward-looking metric (e.g., next quarter guidance) and only historical data is present, say so in one sentence, then answer with what is available.
- If market data contradicts filing disclosures (e.g., revenue growth in filings vs. declining price), flag the conflict explicitly."""


def make_synthesizer_node(
    on_token: Callable[[str], Awaitable[None]] | None = None
):
    """
    on_token: async callback called with each token as it arrives.
              Used by the SSE stream to push tokens immediately.
              None for the non-streaming POST /research path.
    """
    async def synthesizer_node(state: AgentState) -> dict:
        try:
            parts = []
            if state.get("market_output"):
                parts.append(f"## Live Market Data\n{state['market_output']}")
            if state.get("filings_output"):
                parts.append(f"## SEC Filing Research\n{state['filings_output']}")
            if state.get("news_output"):
                parts.append(f"## Recent News Sentiment\n{state['news_output']}")

            sources = [
                k for k, present in {
                    "market": bool(state.get("market_output")),
                    "filings": bool(state.get("filings_output")),
                    "news": bool(state.get("news_output")),
                }.items() if present
            ]
            logger.info(
                "synthesizer.started",
                ticker=state.get("ticker"),
                route=state.get("route"),
                sources=sources,
                model=MODEL,
            )

            combined = "\n\n".join(parts)

            # Build messages: prepend prior Q&A turns so the model can resolve
            # follow-up references ("that figure", "elaborate on point 3", etc.)
            prior = state.get("messages") or []
            messages = [{"role": m["role"], "content": m["content"]} for m in prior]
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        # Cache the research data — the expensive prefix that repeats across calls
                        "text": f"Research collected:\n{combined}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": f"\nQuestion: {state['question']}\n\nSynthesize a final answer.",
                    },
                ],
            })

            chunks: list[str] = []
            async with get_anthropic_async().messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYNTH_SYSTEM,
                messages=messages
            ) as stream:
                async for text in stream.text_stream:
                    chunks.append(text)
                    if on_token is not None:
                        await on_token(text)
                usage = stream.current_message_snapshot.usage
                llm_tokens_total.labels(model=MODEL, token_type="input").inc(usage.input_tokens)
                llm_tokens_total.labels(model=MODEL, token_type="output").inc(usage.output_tokens)
                cache_read = getattr(usage, "cache_read_input_tokens", None) or 0
                if cache_read:
                    llm_tokens_total.labels(model=MODEL, token_type="cache_read").inc(cache_read)

            logger.info(
                "synthesizer.completed",
                ticker=state.get("ticker"),
                output_chars=sum(len(c) for c in chunks),
            )
            return {"final_answer": "".join(chunks)}

        except Exception as exc:
            logger.exception("synthesizer.failed", ticker=state.get("ticker"), exc=str(exc))
            return node_error("final_answer", "synthesizer", exc)

    return synthesizer_node