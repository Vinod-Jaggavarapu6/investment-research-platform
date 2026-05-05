import os
from typing import Callable, Awaitable

import structlog

from ..clients import get_anthropic_async
from ..metrics import llm_tokens_total
from ..state import AgentState
from .base import node_error

logger = structlog.get_logger(__name__)

MODEL      = os.getenv("GENERAL_AGENT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("GENERAL_AGENT_MAX_TOKENS", "1024"))

GENERAL_SYSTEM = """You are a helpful assistant with deep knowledge of finance and investing.

Answer the user's question directly and concisely. If it is a general financial concept question
(e.g. "what is P/E ratio?", "explain DCF"), give a precise, finance-grounded answer.
If it is completely off-topic (e.g. math, trivia), answer it plainly.

Prior conversation turns may appear in the message history — use them for context if relevant,
but do not force a financial angle onto a question that has none.

Keep answers brief unless the question requires depth."""


def make_general_node(
    on_token: Callable[[str], Awaitable[None]] | None = None
):
    async def general_node(state: AgentState) -> dict:
        try:
            logger.info("general_agent.started", question=state["question"][:120])

            prior = state.get("messages") or []
            messages = [{"role": m["role"], "content": m["content"]} for m in prior]
            messages.append({"role": "user", "content": state["question"]})

            chunks: list[str] = []
            async with get_anthropic_async().messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=GENERAL_SYSTEM,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    chunks.append(text)
                    if on_token is not None:
                        await on_token(text)
                usage = stream.current_message_snapshot.usage
                llm_tokens_total.labels(model=MODEL, token_type="input").inc(usage.input_tokens)
                llm_tokens_total.labels(model=MODEL, token_type="output").inc(usage.output_tokens)

            logger.info("general_agent.completed", output_chars=sum(len(c) for c in chunks))
            return {"final_answer": "".join(chunks)}

        except Exception as exc:
            logger.exception("general_agent.failed", exc=str(exc))
            return node_error("final_answer", "general_agent", exc)

    return general_node
