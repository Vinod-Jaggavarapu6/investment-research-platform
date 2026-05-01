"""
Quick smoke test for prompt caching.
Run: python -m scripts.test_cache  (from backend/)
"""
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

import anthropic

FAKE_RESEARCH = """
## Live Market Data
AAPL: Price $189.50, P/E 28.4x, EV/EBITDA 21.1x, Revenue TTM $385B, YoY +6.1%
MSFT: Price $415.20, P/E 35.2x, EV/EBITDA 26.8x, Revenue TTM $230B, YoY +15.6%

## SEC Filing Research
[AAPL 2024 10-K, Item 1A] Apple faces intense competition in all its markets.
The company's performance is subject to global economic conditions, supply chain
disruptions, and the competitive landscape in smartphones and personal computers.
Revenue from iPhone represents approximately 52% of total net sales. Services
segment continues to grow, now representing 22% of total net sales with higher
gross margins than hardware. The company repurchased $90.2 billion of its common
stock during fiscal 2024 and paid $15.0 billion in dividends. Capital expenditure
guidance for fiscal 2025 remains modest relative to peers given asset-light model.

[MSFT 2024 10-K, Item 1] Microsoft operates through three segments: Productivity
and Business Processes, Intelligent Cloud, and More Personal Computing. Azure
cloud revenue grew 28% year-over-year, driven by AI services adoption. The
company has committed $13 billion to OpenAI and integrates AI across all product
lines via Copilot. LinkedIn revenue grew 10% to $16.4B. Gaming segment saw
notable contribution from Activision Blizzard acquisition completed January 2024.
Operating income margin expanded to 44.6% driven by cloud mix shift. Free cash
flow of $74.1 billion provides substantial capital return capacity.

## Recent News Sentiment
AAPL: Mixed sentiment. Concerns around iPhone 16 demand in China (bearish catalyst).
Vision Pro sales below expectations. However, India manufacturing ramp and services
acceleration seen as structural positives. Analyst consensus: 28 Buy, 10 Hold, 2 Sell.

MSFT: Strongly bullish sentiment. Azure AI services driving upside surprises in
cloud segment. Copilot monetization beginning to show in enterprise seat growth.
GitHub Copilot surpassed 1.8M paid subscribers. Key risk: elevated capex for AI
infrastructure ($60B guided for FY2025) compressing near-term free cash flow.
Analyst consensus: 52 Buy, 4 Hold, 0 Sell.
""" * 3


def _print_usage(label: str, u) -> None:
    print(
        f"[{label}] created={u.cache_creation_input_tokens} "
        f"read={u.cache_read_input_tokens} uncached={u.input_tokens}"
    )


async def call(label: str, model: str, question: str, extra_headers: dict | None = None) -> None:
    client = anthropic.AsyncAnthropic()
    kwargs = dict(
        model=model,
        max_tokens=128,
        system="You are a concise investment analyst. Answer in 1-2 sentences.",
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Research collected:\n{FAKE_RESEARCH}",
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "type": "text",
                    "text": f"\nQuestion: {question}\n\nAnswer briefly.",
                },
            ],
        }],
    )
    if extra_headers:
        kwargs["extra_headers"] = extra_headers
    response = await client.messages.create(**kwargs)
    _print_usage(label, response.usage)


async def main() -> None:
    question = "Which company has better cloud growth prospects?"

    print("\n=== Test 1: claude-sonnet-4-6, no beta header ===")
    await call("call1", "claude-sonnet-4-6", question)
    await call("call2", "claude-sonnet-4-6", question)

    print("\n=== Test 2: claude-sonnet-4-6 + extended-cache-ttl-2025-04-11 beta ===")
    hdrs = {"anthropic-beta": "extended-cache-ttl-2025-04-11"}
    await call("call1", "claude-sonnet-4-6", question, extra_headers=hdrs)
    await call("call2", "claude-sonnet-4-6", question, extra_headers=hdrs)

    print("\n=== Test 3: claude-haiku-4-5-20251001 (cheapest Claude 4 baseline) ===")
    await call("call1", "claude-haiku-4-5-20251001", question)
    await call("call2", "claude-haiku-4-5-20251001", question)


asyncio.run(main())
