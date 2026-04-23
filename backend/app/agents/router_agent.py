import json
import os
from ..state import AgentState
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI

client = wrap_openai(AsyncOpenAI())
MODEL = os.getenv("ROUTER_AGENT_MODEL", "gpt-4o-mini")

ROUTER_SYSTEM = """You are a question classifier for a financial research platform.
Analyze the user's question and return a JSON object with two fields:

1. "route": one of five categories:
   - "market"        → requires live price, volume, ratios, or current financial metrics
   - "filings"       → requires information from SEC filings (10-K, 10-Q, earnings, guidance)
   - "news"          → requires recent news sentiment, headlines, or market buzz
   - "both"          → requires BOTH live market data AND SEC filing context
   - "comprehensive" → requires market data, SEC filings, AND recent news sentiment

2. "ticker": the stock ticker symbol mentioned in the question (e.g. "AAPL", "MSFT").
   Return null if no specific ticker is mentioned.

CRITICAL DISTINCTION — route by WHERE the answer comes from, not what words appear:

  MARKET — answer comes from live market data feed:
    "What is X's current price?"          → market
    "What is X's P/E ratio?"              → market
    "What is X's gross margin?"           → market
    "What is X's revenue growth?"         → market
    "What is X's EV/EBITDA?"              → market
    "What is X's market cap?"             → market
    "What is X's dividend yield?"         → market
    "What is X's debt to equity?"         → market

  FILINGS — answer comes from SEC documents:
    "What did X disclose about revenue?"  → filings
    "What did X say about margins?"       → filings
    "What risk factors did X disclose?"   → filings
    "What did X guide for?"               → filings
    "What were X's stated priorities?"    → filings

  NEWS — answer comes from recent news and sentiment:
    "What is the news sentiment around X?"          → news
    "What are analysts saying about X this week?"   → news
    "Is there positive momentum around X?"          → news
    "What are the recent headlines for X?"          → news
    "What catalysts are driving X's price?"         → news

  BOTH — requires live market data AND SEC filing context:
    "Is X's valuation justified given their 10-K guidance?"  → both
    "How does X's current P/E compare to their guidance?"    → both
    "Does X's price reflect risks they disclosed?"           → both

  COMPREHENSIVE — requires market data, SEC filings, AND recent news:
    "Give me a full picture of X"                            → comprehensive
    "Full analysis of X — valuation, filings, and sentiment" → comprehensive
    "What should I know about X before investing?"           → comprehensive
    "Complete research on X"                                 → comprehensive

The key signal for FILINGS is verbs like "disclose", "say", "state", "guide", "report".
The key signal for NEWS is words like "sentiment", "momentum", "headlines", "analysts saying", "buzz", "catalysts".
A plain "What is X's [metric]?" is always MARKET regardless of what the metric is.

Respond with ONLY valid JSON. No preamble, no explanation, no markdown code fences.

Examples:
  {"route": "market",        "ticker": "AAPL"}
  {"route": "filings",       "ticker": "MSFT"}
  {"route": "both",          "ticker": "NVDA"}
  {"route": "news",          "ticker": "TSLA"}
  {"route": "comprehensive", "ticker": "AAPL"}
  {"route": "filings",       "ticker": null}
"""


async def router_node(state: AgentState) -> dict:
    """Classify the question and extract ticker. Sets route and ticker in state."""
    response = await client.chat.completions.create(
        model=MODEL,
        max_tokens=50,
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": state["question"]},
        ],
    )

    raw = response.choices[0].message.content.strip()
    print(f"[ROUTER DEBUG] raw LLM output: {raw!r}", flush=True)

    if raw.startswith("```"):
      raw = raw.split("```")[1]          # get content between first pair of fences
      if raw.startswith("json"):
          raw = raw[4:]                  # strip the "json" language tag
      raw = raw.strip()

    try:
        parsed = json.loads(raw)
        route  = parsed.get("route", "both")
        ticker = parsed.get("ticker")

        if route not in ("market", "filings", "both", "news", "comprehensive"):
            route = "both"

    except json.JSONDecodeError:
        route  = "both"
        ticker = None

    print(f"[ROUTER DEBUG] returning route={route!r} ticker={ticker!r}", flush=True)

    return {
        "route":  route,
        "ticker": ticker,
    }