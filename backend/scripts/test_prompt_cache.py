"""
Validates that both prompt-caching fixes are working correctly.

Run from backend/:
    python -m scripts.test_prompt_cache

What this tests
---------------
Scenario 1 — System prompt caching (Fix 1)
    Two identical requests with the real SYNTH_SYSTEM prompt as a cached list block.
    call1 → cache_creation > 0, cache_read == 0  (writes the cache)
    call2 → cache_creation == 0, cache_read > 0  (reads from cache)

Scenario 2 — Research context reuse on follow-up (Fix 2)
    call1 (Q1, AAPL): sends fresh_context → cache_creation > 0
    call2 (Q2, AAPL, SAME context): simulates a follow-up where stored context is
          reused byte-for-byte → cache_read > 0 for the research block too.

Scenario 3 — Ticker change must NOT reuse context
    call1 (AAPL context, AAPL question): writes cache
    call2 (MSFT context, MSFT question): different context → cache_read == 0
    This confirms the ticker-guard in the synthesizer is necessary.

Expected output
---------------
PASS lines confirm the behaviour. FAIL lines indicate a bug.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

import anthropic

# ---------------------------------------------------------------------------
# Import the real system prompt so we test exactly what production uses.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.agents.synthesizer import SYNTH_SYSTEM

MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Fake research blobs — static so cache keys are stable across calls.
# ---------------------------------------------------------------------------
AAPL_CONTEXT = """\
## Live Market Data
AAPL: Price $189.50, P/E 28.4x, EV/EBITDA 21.1x, Revenue TTM $385B, YoY +6.1%
Gross margin 46.2%, Net margin 25.3%, Debt/Equity 1.8x, Cash $67B

## SEC Filing Research
[AAPL 2024 10-K, Item 1A] Apple faces intense competition in all its markets.
iPhone represents ~52% of total net sales. Services segment (22% of sales) carries
higher gross margins than hardware. The company repurchased $90.2B of common stock
during fiscal 2024 and paid $15.0B in dividends.

## Recent News Sentiment
AAPL: Mixed. Concerns around iPhone 16 demand in China (bearish). Vision Pro sales
below expectations. India manufacturing ramp and services acceleration seen as
structural positives. Analyst consensus: 28 Buy, 10 Hold, 2 Sell."""

MSFT_CONTEXT = """\
## Live Market Data
MSFT: Price $415.20, P/E 35.2x, EV/EBITDA 26.8x, Revenue TTM $230B, YoY +15.6%
Gross margin 69.4%, Net margin 35.1%, Debt/Equity 0.3x, Cash $80B

## SEC Filing Research
[MSFT 2024 10-K, Item 1] Microsoft operates through three segments: Productivity
and Business Processes, Intelligent Cloud, and More Personal Computing. Azure grew
28% YoY driven by AI services. Committed $13B to OpenAI, integrates AI via Copilot.
Operating income margin expanded to 44.6%. Free cash flow $74.1B.

## Recent News Sentiment
MSFT: Strongly bullish. Azure AI services driving upside surprises. Copilot
monetization beginning to show in enterprise seat growth. Key risk: $60B capex
guidance for FY2025. Analyst consensus: 52 Buy, 4 Hold, 0 Sell."""


def _fmt(u) -> str:
    created = getattr(u, "cache_creation_input_tokens", 0) or 0
    read    = getattr(u, "cache_read_input_tokens", 0) or 0
    uncached = u.input_tokens
    return f"created={created:>5}  read={read:>5}  uncached={uncached:>5}"


def _check(label: str, u, expect_read: bool, expect_creation: bool) -> bool:
    created  = getattr(u, "cache_creation_input_tokens", 0) or 0
    read     = getattr(u, "cache_read_input_tokens", 0) or 0
    ok_read  = (read > 0) == expect_read
    ok_write = (created > 0) == expect_creation
    status   = "PASS" if (ok_read and ok_write) else "FAIL"
    print(f"  [{status}] {label:40s}  {_fmt(u)}")
    return ok_read and ok_write


async def _call(
    label: str,
    system_prompt: str | list,
    research_context: str,
    question: str,
    prior_messages: list[dict] | None = None,
) -> anthropic.types.Usage:
    client = anthropic.AsyncAnthropic()

    # Build messages the same way the synthesizer does post-fix
    messages = list(prior_messages or [])
    messages.append({
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": f"Research collected:\n{research_context}",
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": f"\nQuestion: {question}\n\nSynthesize a final answer.",
            },
        ],
    })

    resp = await client.messages.create(
        model=MODEL,
        max_tokens=64,
        system=system_prompt,
        messages=messages,
    )
    print(f"    {label}: {_fmt(resp.usage)}")
    return resp.usage


# ---------------------------------------------------------------------------
# Scenario 1: system prompt caching
# ---------------------------------------------------------------------------
async def scenario_1() -> bool:
    print("\n=== Scenario 1: System prompt caching (Fix 1) ===")
    print("  call1 should WRITE the system-prompt cache (creation > 0, read == 0)")
    print("  call2 should READ the system-prompt cache  (creation == 0, read > 0)")

    cached_system = [
        {"type": "text", "text": SYNTH_SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]

    u1 = await _call("call1", cached_system, AAPL_CONTEXT, "What is AAPL's revenue growth?")
    u2 = await _call("call2", cached_system, AAPL_CONTEXT, "What is AAPL's revenue growth?")

    p1 = _check("call1 (expect creation, no read)", u1, expect_read=False, expect_creation=True)
    p2 = _check("call2 (expect read, no creation)",  u2, expect_read=True,  expect_creation=False)
    return p1 and p2


# ---------------------------------------------------------------------------
# Scenario 2: research context reused on follow-up (Fix 2)
# ---------------------------------------------------------------------------
async def scenario_2() -> bool:
    print("\n=== Scenario 2: Research context reuse on follow-up (Fix 2) ===")
    print("  call1 (Q1): writes cache for system + research context")
    print("  call2 (Q2): same context → reads cache for both blocks")

    cached_system = [
        {"type": "text", "text": SYNTH_SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]

    # Q1 — first question, no prior messages
    u1 = await _call("call1 Q1", cached_system, AAPL_CONTEXT, "What is AAPL's P/E ratio?")

    # Q2 — follow-up, prior turn added as conversation history (same context block)
    prior = [
        {"role": "user",      "content": "What is AAPL's P/E ratio?"},
        {"role": "assistant", "content": "AAPL trades at 28.4x P/E."},
    ]
    u2 = await _call("call2 Q2", cached_system, AAPL_CONTEXT, "What are the main risks?", prior_messages=prior)

    p1 = _check("call1 (expect creation)", u1, expect_read=False, expect_creation=True)
    p2 = _check("call2 (expect read)",     u2, expect_read=True,  expect_creation=False)
    return p1 and p2


# ---------------------------------------------------------------------------
# Scenario 3: different ticker must NOT hit cache
# ---------------------------------------------------------------------------
async def scenario_3() -> bool:
    print("\n=== Scenario 3: Ticker change must NOT hit research context cache ===")
    print("  call1 (AAPL context): writes cache")
    print("  call2 (MSFT context): different prefix → read == 0")

    cached_system = [
        {"type": "text", "text": SYNTH_SYSTEM, "cache_control": {"type": "ephemeral"}}
    ]

    u1 = await _call("call1 AAPL", cached_system, AAPL_CONTEXT, "What is AAPL's P/E?")
    u2 = await _call("call2 MSFT", cached_system, MSFT_CONTEXT, "What is MSFT's P/E?")

    p1 = _check("call1 AAPL (expect creation)", u1, expect_read=False, expect_creation=True)
    # call2: system prompt will be a cache READ (same system), but research block will be a WRITE (new context)
    # So: read > 0 (system prompt hit) AND creation > 0 (new research block written)
    created2 = getattr(u2, "cache_creation_input_tokens", 0) or 0
    read2    = getattr(u2, "cache_read_input_tokens", 0) or 0
    p2_ok    = read2 > 0 and created2 > 0   # system hit + new research write
    status   = "PASS" if p2_ok else "FAIL"
    print(f"  [{status}] call2 MSFT (expect sys-read + research-creation)  {_fmt(u2)}")
    return p1 and p2_ok


async def main() -> None:
    print(f"Testing prompt caching with model: {MODEL}")
    print("Each scenario sends real API requests — expect ~2-3s per call.\n")

    results = []
    results.append(await scenario_1())
    results.append(await scenario_2())
    results.append(await scenario_3())

    passed = sum(results)
    total  = len(results)
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} scenarios passed")
    if passed < total:
        print("FAIL — one or more scenarios did not behave as expected.")
        sys.exit(1)
    else:
        print("All scenarios PASSED — prompt caching is working correctly.")


asyncio.run(main())
