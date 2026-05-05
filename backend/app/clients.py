"""
Singleton LLM clients initialized once at app startup via init_clients().

Three clients are provided:
  - Sync OpenAI  — financial_agent and news_agent (run in thread executors)
  - Async OpenAI — router_agent and filings_agent
  - Async Anthropic — synthesizer and compare_agent
"""

from __future__ import annotations

import openai
from anthropic import AsyncAnthropic
from langsmith.wrappers import wrap_anthropic, wrap_openai
from openai import AsyncOpenAI

_openai_sync: openai.OpenAI | None = None
_openai_async: AsyncOpenAI | None = None
_anthropic_async: AsyncAnthropic | None = None


def init_clients() -> None:
    global _openai_sync, _openai_async, _anthropic_async
    _openai_sync = wrap_openai(openai.OpenAI())
    _openai_async = wrap_openai(AsyncOpenAI())
    _anthropic_async = wrap_anthropic(AsyncAnthropic())


def get_openai_sync() -> openai.OpenAI:
    if _openai_sync is None:
        raise RuntimeError("LLM clients not initialized — call init_clients() at startup")
    return _openai_sync


def get_openai_async() -> AsyncOpenAI:
    if _openai_async is None:
        raise RuntimeError("LLM clients not initialized — call init_clients() at startup")
    return _openai_async


def get_anthropic_async() -> AsyncAnthropic:
    if _anthropic_async is None:
        raise RuntimeError("LLM clients not initialized — call init_clients() at startup")
    return _anthropic_async
