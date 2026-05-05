"""Singleton LLM clients — call init_clients() at startup before any get_*() call."""

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
