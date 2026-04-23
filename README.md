# Investment Research Platform

An AI-powered financial research assistant that routes questions to specialized agents — live market data, SEC 10-K filings (RAG), and news sentiment — then synthesizes a coherent answer.

## Architecture

```text
User question
    └─▶ Router Agent (GPT-4o-mini)
            ├─▶ Financial Agent (Claude) — yfinance market data
            ├─▶ Filings Agent  (Claude) — FAISS + PostgreSQL RAG over SEC 10-Ks
            └─▶ News Agent     (Claude) — Finnhub headlines + sentiment scoring
                    └─▶ Synthesizer (GPT-4o-mini) — final investment insight
```

**Tech stack:** FastAPI · LangGraph · Anthropic (Claude) · OpenAI · FAISS · PostgreSQL · LangSmith · Docker

## Project structure

```text
backend/         FastAPI application
  app/
    agents/      Router, Financial, Filings, News, Synthesizer agents
    tools/       yfinance, Finnhub, FAISS retrieval wrappers
    rag/         Embedding, chunking, FAISS index build/query
    data/        FAISS index + raw SEC filings (gitignored, build locally)
  scripts/       Index build, evaluation, data export utilities
  tests/         pytest test suites
infra/           Docker Compose (PostgreSQL + backend)
frontend/        UI — planned (Phase 6)
evals/           Evaluation harness — planned
```

## Quick start

### Prerequisites

- Docker & Docker Compose
- API keys: Anthropic, OpenAI, Finnhub, LangSmith (optional)

### 1. Configure environment

```bash
cp backend/.env.example backend/.env
# Edit backend/.env and fill in your API keys:
#   ANTHROPIC_API_KEY
#   OPENAI_API_KEY
#   FINNHUB_API_KEY
#   DATABASE_URL (already set for docker-compose)
#   LANGSMITH_API_KEY  (optional — for tracing)
```

### 2. Start services

```bash
cd infra
docker compose up
```

Backend is available at `http://localhost:8000`.

### 3. Build the RAG index (first run only)

```bash
cd backend
uv run python scripts/build_index.py
```

This downloads SEC 10-K filings for 16 companies, chunks them, embeds with OpenAI, and stores in FAISS + PostgreSQL.

## API endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/analyze/{ticker}` | Financial snapshot with buy/hold/sell signal |
| `POST` | `/analyze/batch` | Analyze multiple tickers |
| `POST` | `/research` | Full multi-agent research pipeline |
| `GET` | `/research/stream` | SSE streaming of research progress |
| `POST` | `/filings/ask` | Q&A on SEC 10-K filings with citations |
| `GET` | `/retrieve` | Raw FAISS similarity search |
| `POST` | `/news/sentiment` | News sentiment analysis for a ticker |

Interactive docs: `http://localhost:8000/docs`

## Data sources

- **yfinance** — Live prices, P/E, margins, fundamentals
- **SEC EDGAR** — 10-K filings for AAPL, MSFT, NVDA, GOOGL, AMZN, TSLA, META, ORCL, INTC, AMD, QCOM, JPM, LLY, PFE, UNH, RTX
- **Finnhub** — Recent news headlines

## Development

### Running locally without Docker

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

PostgreSQL must be running separately (see `infra/docker-compose.yml` for connection details).

### Running tests

```bash
cd backend
uv run pytest
```

### Environment variables

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `ANTHROPIC_API_KEY` | Yes | Claude API key (filings, financial, news agents) |
| `OPENAI_API_KEY` | Yes | GPT-4o-mini key (router + synthesizer), OpenAI embeddings |
| `FINNHUB_API_KEY` | Yes | News data |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `LANGSMITH_API_KEY` | No | LLM tracing (LangSmith) |
| `LANGSMITH_TRACING` | No | Enable LangSmith tracing (`true`/`false`) |
| `FINANCIAL_AGENT_MODEL` | No | Override Claude model (default: `claude-opus-4-5`) |
| `FILINGS_AGENT_MODEL` | No | Override Claude model (default: `claude-opus-4-5`) |
| `NEWS_AGENT_MODEL` | No | Override Claude model (default: `claude-opus-4-5`) |
| `ROUTER_AGENT_MODEL` | No | Override router model (default: `gpt-4o-mini`) |
| `SYNTHESIZER_MODEL` | No | Override synthesizer model (default: `gpt-4o-mini`) |

## Status

| Phase | Feature | Status |
| ----- | ------- | ------ |
| 1 | Single-ticker financial analysis | Done |
| 2 | SEC filing RAG (FAISS + PostgreSQL) | Done |
| 3 | Multi-agent LangGraph routing | Done |
| 4 | News sentiment + SSE streaming | Done |
| 5 | Evaluation harness | In progress |
| 6 | Frontend UI | Planned |
