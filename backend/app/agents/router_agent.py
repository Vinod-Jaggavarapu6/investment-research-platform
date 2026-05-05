import json
import os

import structlog

from ..state import AgentState
from ..clients import get_openai_async
from ..utils.ticker import validate_ticker, validate_tickers, InvalidTickerError

logger = structlog.get_logger(__name__)

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
  "general"         → question is NOT about a specific company's financials:
                      generic financial concepts, off-topic questions, math, trivia,
                      or any question with no discernible financial company target

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
  "what's 2 + 2?"                                  → {"route": "general",  "ticker": null,   "tickers": null}
  "what is P/E ratio?"                             → {"route": "general",  "ticker": null,   "tickers": null}
  "explain what EBITDA means"                      → {"route": "general",  "ticker": null,   "tickers": null}
  "tell me a joke"                                 → {"route": "general",  "ticker": null,   "tickers": null}

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

VALID_ROUTES = {"market", "filings", "filings_recent", "both", "news", "comprehensive", "compare", "general"}


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

    response = await get_openai_async().chat.completions.create(
        model=MODEL,
        max_completion_tokens=100,
        messages=[
            {"role": "system", "content": ROUTER_SYSTEM},
            {"role": "user", "content": user_content},
        ],
    )

    raw = response.choices[0].message.content.strip()

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
            logger.warning("router.invalid_route", route=route, fallback="both")
            route = "both"

        # Validate and normalise ticker(s) — raises InvalidTickerError on bad input,
        # which we catch below and treat as "no ticker resolved".
        if route == "compare":
            if not isinstance(tickers, list) or len(tickers) < 2:
                logger.warning("router.compare_insufficient_tickers", tickers=tickers, fallback="filings")
                route = "filings"
                tickers = None
            else:
                try:
                    tickers = validate_tickers(tickers)
                except InvalidTickerError as e:
                    logger.warning("router.invalid_tickers", error=str(e), fallback="filings")
                    route = "filings"
                    tickers = None
                ticker = None
        elif ticker:
            try:
                ticker = validate_ticker(ticker)
            except InvalidTickerError as e:
                logger.warning("router.invalid_ticker", error=str(e))
                ticker = None

        # Follow-up fallback: apply AFTER normalisation so we see the final route.
        # "compare" in the question text (e.g. "how does that compare...") can
        # briefly set route="compare" before normalisation downgrades it to "filings".
        # Guard: only fire when no ticker AND no tickers list were resolved, and the
        # question is not a general off-topic one (we don't want to inject a ticker
        # into a math question just because the prior turn was about AAPL).
        if not ticker and not tickers and prev_ticker and route != "general":
            ticker = prev_ticker

        logger.info("router.classified", route=route, ticker=ticker, tickers=tickers)

    except json.JSONDecodeError:
        logger.warning("router.parse_failed", raw_preview=raw[:200])
        route   = "both"
        ticker  = None
        tickers = None

    return {
        "route":          route,
        # For general turns preserve the prior investment ticker so the follow-up
        # chain survives: Turn 1 (AAPL) → Turn 2 (off-topic) → Turn 3 (news?) still
        # has prev_ticker="AAPL" available for the router's context prepend.
        "ticker":         prev_ticker if route == "general" else ticker,
        "tickers":        tickers,
        # Clear previous turn's outputs so agents always run fresh
        "final_answer":   None,
        "market_output":  None,
        "filings_output": None,
        "news_output":    None,
        "citations":      None,
        "ingest_pending": None,
    }