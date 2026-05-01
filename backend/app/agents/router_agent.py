import json
import os
from ..state import AgentState
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI

client = wrap_openai(AsyncOpenAI())
MODEL = os.getenv("ROUTER_AGENT_MODEL", "gpt-4o-mini")

ROUTER_SYSTEM = """You are a question classifier for a financial research platform.
Analyze the user's question and return a JSON object.

ROUTES — pick exactly one:
  "market"          → live price, volume, ratios, or current financial metrics for ONE company
  "filings"         → SEC annual (10-K) disclosures for ONE company
  "filings_recent"  → recent quarters (10-Q) or events (8-K) for ONE company
  "news"            → recent headlines/sentiment for ONE company
  "both"            → live market data AND SEC filings for ONE company
  "comprehensive"   → market data, SEC filings, AND news for ONE company
  "compare"         → comparing TWO OR MORE companies against each other

TICKER FIELDS:
  For single-company routes: return "ticker" (string) and "tickers" (null)
  For "compare" route:       return "tickers" (array of strings) and "ticker" (null)

CRITICAL — use "compare" when the question explicitly asks to:
  - compare, contrast, rank, or pit companies against each other
  - find which company is better/stronger/larger at something
  - compare side-by-side across a dimension

Examples:
  "Compare AAPL vs MSFT on risk factors"           → {"route": "compare", "ticker": null, "tickers": ["AAPL", "MSFT"]}
  "Which is better, NVDA or AMD for AI?"           → {"route": "compare", "ticker": null, "tickers": ["NVDA", "AMD"]}
  "GOOGL vs META vs AMZN cloud strategy"           → {"route": "compare", "ticker": null, "tickers": ["GOOGL", "META", "AMZN"]}
  "Compare JPM and BAC margins"                    → {"route": "compare", "ticker": null, "tickers": ["JPM", "BAC"]}
  "What is AAPL's current price?"                  → {"route": "market",  "ticker": "AAPL", "tickers": null}
  "What did MSFT disclose about revenue?"          → {"route": "filings", "ticker": "MSFT", "tickers": null}
  "What happened last quarter at NVDA?"            → {"route": "filings_recent", "ticker": "NVDA", "tickers": null}
  "News sentiment around TSLA?"                    → {"route": "news",    "ticker": "TSLA", "tickers": null}
  "Is NVDA valuation justified vs guidance?"       → {"route": "both",    "ticker": "NVDA", "tickers": null}
  "Full picture of AAPL"                           → {"route": "comprehensive", "ticker": "AAPL", "tickers": null}
  "Which semiconductor company has better margins?"→ {"route": "filings", "ticker": null,   "tickers": null}

ROUTE SIGNALS:
  MARKET:          "What is X's price/P/E/margin/revenue/market cap?" — plain metric lookup
  FILINGS:         verbs like disclose, say, state, guide, report (annual)
  FILINGS_RECENT:  "last quarter", "recent earnings", "Q1/Q2/Q3/Q4", "latest", "8-K"
  NEWS:            sentiment, momentum, headlines, analysts saying, catalysts
  BOTH:            valuation vs guidance, price vs disclosed risks
  COMPREHENSIVE:   "full picture", "full analysis", "what should I know before investing"
  COMPARE:         vs, versus, compare, contrast, better, stronger, side-by-side + 2+ companies

Respond with ONLY valid JSON. No preamble, no explanation, no markdown code fences.
"""

VALID_ROUTES = {"market", "filings", "filings_recent", "both", "news", "comprehensive", "compare"}


async def router_node(state: AgentState) -> dict:
    """Classify the question and extract ticker(s). Sets route, ticker, tickers in state."""
    # Capture previous turn's ticker BEFORE we reset state — used as follow-up context
    prev_ticker = (state.get("ticker") or "").upper() or None

    # When the question is a follow-up (no ticker mentioned), tell the LLM what
    # company the conversation has been about so it can route correctly.
    user_content = state["question"]
    if prev_ticker:
        user_content = (
            f"[Conversation context: the previous question in this conversation "
            f"was about {prev_ticker}]\n\n{state['question']}"
        )

    response = await client.chat.completions.create(
        model=MODEL,
        max_completion_tokens=100,
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": user_content},
        ],
    )

    raw = response.choices[0].message.content.strip()
    print(f"[ROUTER DEBUG] raw LLM output: {raw!r}", flush=True)

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed  = json.loads(raw)
        route   = parsed.get("route", "both")
        ticker  = parsed.get("ticker")
        tickers = parsed.get("tickers")

        if route not in VALID_ROUTES:
            route = "both"

        # Normalise: compare must have a tickers list
        if route == "compare":
            if not isinstance(tickers, list) or len(tickers) < 2:
                route = "filings"   # fall back gracefully
                tickers = None
            else:
                tickers = [t.upper() for t in tickers]
                ticker  = None

        # Follow-up fallback: apply AFTER normalisation so we see the final route.
        # "compare" in the question text (e.g. "how does that compare...") can
        # briefly set route="compare" before normalisation downgrades it to "filings".
        # Guard: only fire when no ticker AND no tickers list were resolved.
        if not ticker and not tickers and prev_ticker:
            ticker = prev_ticker

    except json.JSONDecodeError:
        route   = "both"
        ticker  = None
        tickers = None

    print(f"[ROUTER DEBUG] returning route={route!r} ticker={ticker!r} tickers={tickers!r}", flush=True)

    return {
        "route":          route,
        "ticker":         ticker,
        "tickers":        tickers,
        # Clear previous turn's outputs so agents always run fresh
        "final_answer":   None,
        "market_output":  None,
        "filings_output": None,
        "news_output":    None,
        "citations":      None,
        "ingest_pending": None,
    }