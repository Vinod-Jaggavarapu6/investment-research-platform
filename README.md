# Investment Research Platform

An AI-powered financial research assistant that routes questions to specialized agents — live market data, SEC 10-K filings (RAG), and news sentiment — then synthesizes a coherent investment report, streamed live to the browser.

## Architecture

```text
User question
    └─▶ Router Agent (GPT-4o-mini)
            ├─▶ Market Agent   (Claude) — yfinance prices, ratios, fundamentals
            ├─▶ Filings Agent  (Claude) — FAISS + PostgreSQL RAG over SEC 10-Ks
            └─▶ News Agent     (Claude) — Finnhub headlines + sentiment scoring
                    └─▶ Synthesizer (GPT-4o-mini) — final investment insight (streamed via SSE)
```

**Tech stack:** FastAPI · LangGraph · Anthropic (Claude) · OpenAI · FAISS · PostgreSQL · LangSmith · React · Vite · Docker

## Project structure

```text
backend/
  app/
    agents/      Router, Market, Filings, News, Synthesizer agents
    tools/       yfinance, Finnhub, FAISS retrieval wrappers
    rag/         Embedding, chunking, FAISS index build/query
    data/        FAISS index + raw SEC filings (gitignored, build locally)
  scripts/       Index build, evaluation, data export utilities
  tests/         pytest test suites
frontend/
  src/
    components/  AgentCard, AgentTimeline, SearchBar
    useResearchStream.ts   SSE stream hook
    types.ts               Shared types and state shapes
infra/           Docker Compose (PostgreSQL + backend)
```
