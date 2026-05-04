"""
news_agent.py — Extract structured sentiment from news articles

Flow:
  1. Fetch last N days of news via Finnhub
  2. Score each article with OpenAI (structured tool call)
  3. Aggregate scores weighted by source quality
  4. Return NewsSentiment with top catalysts
"""

import asyncio
import json
import os
import time
from datetime import datetime

import openai
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.models import (
    ArticleSentiment,
    NewsArticle,
    NewsResponse,
    NewsSentiment,
    SentimentSignal,
)
from app.tools.news_data import fetch_news
from app.agents.financial_agent import _elapsed
from ..clients import get_openai_sync
from ..metrics import llm_tokens_total
from ..state import AgentState
from .base import node_error

logger = structlog.get_logger(__name__)

MODEL          = os.getenv("NEWS_AGENT_MODEL", "gpt-4o-mini")
NEWS_MAX_TOKENS = int(os.getenv("NEWS_AGENT_MAX_TOKENS", "2000"))
NEWS_DAYS       = int(os.getenv("NEWS_DAYS_LOOKBACK", "7"))

# ---------------------------------------------------------------------------
# Tool definition — forces structured per-article scoring (OpenAI format)
# ---------------------------------------------------------------------------

SCORE_ARTICLES_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_sentiment",
        "description": (
            "Submit structured sentiment analysis for a batch of news articles. "
            "Score every article provided. Do not skip any. "
            "Base scores strictly on article content — not on general company reputation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {
                                "type": "integer",
                                "description": "Article index from the input list (0-based)",
                            },
                            "sentiment": {
                                "type": "string",
                                "enum": ["POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"],
                            },
                            "score": {
                                "type": "number",
                                "description": (
                                    "Sentiment score from -1.0 (very negative) "
                                    "to +1.0 (very positive). 0.0 = neutral."
                                ),
                            },
                            "justification": {
                                "type": "string",
                                "description": (
                                    "One sentence grounded in the article content. "
                                    "Example: 'Earnings beat of 12% and raised guidance "
                                    "are direct positive price catalysts.'"
                                ),
                            },
                        },
                        "required": ["index", "sentiment", "score", "justification"],
                    },
                },
                "bull_catalysts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 key positive themes across all articles. Specific, not vague.",
                },
                "bear_catalysts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-4 key negative themes across all articles. Specific, not vague.",
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "2-3 sentence synthesis of overall news sentiment. "
                        "Lead with the dominant theme. Cite specific events."
                    ),
                },
            },
            "required": ["articles", "bull_catalysts", "bear_catalysts", "summary"],
        },
    },
}

SYSTEM_PROMPT = """\
You are a financial news analyst extracting structured sentiment from news articles.

Rules:
- Score each article on its own content — not on the company's general reputation
- A neutral earnings report is NEUTRAL even for a company you like
- Scores must reflect magnitude: a minor product update is ±0.2, a major earnings beat is ±0.8
- Justifications must cite specific facts from the article, not vague phrases
- Call submit_sentiment exactly once with scores for ALL articles provided
"""

# ---------------------------------------------------------------------------
# Batch articles to avoid context window limits
# ---------------------------------------------------------------------------

MAX_ARTICLES_PER_BATCH = int(os.getenv("NEWS_BATCH_SIZE", "15"))


def _format_articles_for_prompt(articles: list[NewsArticle]) -> str:
    parts = []
    for i, article in enumerate(articles):
        parts.append(
            f"[{i}] Source: {article.source} | "
            f"Date: {article.published_at.strftime('%Y-%m-%d')}\n"
            f"Headline: {article.headline}\n"
            f"Summary: {article.summary or 'No summary available'}\n"
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Score a batch of articles with OpenAI
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(openai.APIConnectionError),
)
def _score_batch(
    ticker: str,
    articles: list[NewsArticle],
) -> tuple[list[dict], list[str], list[str], str]:
    formatted = _format_articles_for_prompt(articles)

    response = get_openai_sync().chat.completions.create(
        model=MODEL,
        max_completion_tokens=NEWS_MAX_TOKENS,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Score the sentiment of these {len(articles)} news articles "
                    f"about {ticker}:\n\n{formatted}"
                ),
            },
        ],
        tools=[SCORE_ARTICLES_TOOL],
        tool_choice={"type": "function", "function": {"name": "submit_sentiment"}},
    )

    if response.usage:
        llm_tokens_total.labels(model=MODEL, token_type="input").inc(response.usage.prompt_tokens)
        llm_tokens_total.labels(model=MODEL, token_type="output").inc(response.usage.completion_tokens)

    tool_calls = (response.choices[0].message.tool_calls or []) if response.choices else []
    for tc in tool_calls:
        if tc.function.name == "submit_sentiment":
            data = json.loads(tc.function.arguments)
            return (
                data.get("articles", []),
                data.get("bull_catalysts", []),
                data.get("bear_catalysts", []),
                data.get("summary", ""),
            )

    raise RuntimeError(
        f"Model did not call submit_sentiment. "
        f"Tool calls: {[tc.function.name for tc in tool_calls]}"
    )


# ---------------------------------------------------------------------------
# Aggregate scores → overall signal
# ---------------------------------------------------------------------------

def _score_to_signal(score: float) -> SentimentSignal:
    if score >= 0.6:   return SentimentSignal.VERY_BULLISH
    if score >= 0.2:   return SentimentSignal.BULLISH
    if score >= -0.2:  return SentimentSignal.NEUTRAL
    if score >= -0.6:  return SentimentSignal.BEARISH
    return SentimentSignal.VERY_BEARISH


def _aggregate(
    articles: list[NewsArticle],
    all_bull: list[str],
    all_bear: list[str],
    summary: str,
    ticker: str,
    days: int,
) -> NewsSentiment:
    scored = [a for a in articles if a.score is not None]

    if not scored:
        return NewsSentiment(
            ticker        = ticker,
            days_analyzed = days,
            article_count = len(articles),
            scored_count  = 0,
            overall_score = 0.0,
            signal        = SentimentSignal.INSUFFICIENT,
            summary       = "Insufficient scored articles to determine sentiment.",
            data_warning  = "No articles were successfully scored.",
        )

    total_weight  = sum(a.source_weight for a in scored)
    weighted_sum  = sum(a.score * a.source_weight for a in scored)  # type: ignore
    overall_score = round(weighted_sum / total_weight, 4)

    return NewsSentiment(
        ticker        = ticker,
        days_analyzed = days,
        article_count = len(articles),
        scored_count  = len(scored),
        overall_score = overall_score,
        signal        = _score_to_signal(overall_score),
        bull_catalysts= all_bull[:4],
        bear_catalysts= all_bear[:4],
        summary       = summary,
        articles      = articles,
    )


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------

def analyze_news_sentiment(
    ticker: str,
    days: int = 7,
) -> NewsResponse:
    start  = time.perf_counter()
    ticker = ticker.upper().strip()

    # ── Step A: Fetch articles ──────────────────────────────────────────
    try:
        articles = fetch_news(ticker, days)
    except Exception as e:
        return NewsResponse(
            success=False,
            error=f"News fetch failed: {str(e)[:300]}",
            duration_ms=_elapsed(start),
        )

    if not articles:
        return NewsResponse(
            success=True,
            sentiment=NewsSentiment(
                ticker        = ticker,
                days_analyzed = days,
                article_count = 0,
                scored_count  = 0,
                overall_score = 0.0,
                signal        = SentimentSignal.INSUFFICIENT,
                summary       = f"No news articles found for {ticker} in the last {days} days.",
                data_warning  = "No articles available from Finnhub.",
            ),
            duration_ms=_elapsed(start),
        )

    logger.info("news_agent.scoring", ticker=ticker, article_count=len(articles), batch_size=MAX_ARTICLES_PER_BATCH)

    # ── Step B: Score in batches ────────────────────────────────────────
    all_bull: list[str] = []
    all_bear: list[str] = []
    final_summary = ""

    for batch_start in range(0, len(articles), MAX_ARTICLES_PER_BATCH):
        batch = articles[batch_start: batch_start + MAX_ARTICLES_PER_BATCH]

        try:
            scored_dicts, bull, bear, summary = _score_batch(ticker, batch)
        except Exception as e:
            logger.error("news_agent.batch_failed", ticker=ticker, error=str(e))
            continue

        for scored in scored_dicts:
            idx = scored.get("index", -1)
            if 0 <= idx < len(batch):
                article = batch[idx]
                try:
                    article.sentiment     = ArticleSentiment(scored["sentiment"])
                    article.score         = float(scored["score"])
                    article.justification = scored.get("justification")
                except ValueError:
                    logger.warning("news_agent.invalid_sentiment", value=scored.get("sentiment"), fallback="NEUTRAL")
                    article.sentiment     = ArticleSentiment.NEUTRAL
                    article.score         = 0.0
                    article.justification = scored.get("justification")

        all_bull.extend(bull)
        all_bear.extend(bear)
        if summary:
            final_summary = summary

    # ── Step C: Aggregate ───────────────────────────────────────────────
    sentiment = _aggregate(
        articles  = articles,
        all_bull  = all_bull,
        all_bear  = all_bear,
        summary   = final_summary,
        ticker    = ticker,
        days      = days,
    )

    logger.info(
        "news_agent.completed",
        ticker=ticker,
        signal=sentiment.signal.value,
        score=sentiment.overall_score,
        scored=sentiment.scored_count,
        total=sentiment.article_count,
        duration_ms=_elapsed(start),
    )

    return NewsResponse(
        success     = True,
        sentiment   = sentiment,
        duration_ms = _elapsed(start),
    )


# ---------------------------------------------------------------------------
# LangGraph node wrapper
# ---------------------------------------------------------------------------

def make_news_node():
    async def news_node(state: AgentState) -> dict:
        try:
            ticker = state.get("ticker")

            if not ticker:
                return {"news_output": "No ticker specified — cannot fetch news sentiment."}

            loop   = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, analyze_news_sentiment, ticker, NEWS_DAYS)

            if not result.success:
                return {"news_output": f"News sentiment unavailable: {result.error}"}

            return {"news_output": result.sentiment.model_dump_json(indent=2)}

        except Exception as exc:
            return node_error("news_output", "news_agent", exc)

    return news_node
