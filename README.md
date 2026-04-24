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

## Quick start

### Prerequisites

- Docker & Docker Compose
- Node.js 18+ (for frontend dev server)
- API keys: Anthropic, OpenAI, Finnhub, LangSmith (optional)

### 1. Configure environment

```bash
cp backend/.env.example backend/.env
# Fill in:
#   ANTHROPIC_API_KEY
#   OPENAI_API_KEY
#   FINNHUB_API_KEY
#   DATABASE_URL        (pre-filled for docker-compose)
#   LANGSMITH_API_KEY   (optional)
```

### 2. Start backend services

```bash
cd infra
docker compose up
```

Backend available at `http://localhost:8000`.

### 3. Build the RAG index (first run only)

```bash
cd backend
uv run python scripts/build_index.py
```

Downloads SEC 10-K filings for 16 companies, chunks and embeds them with OpenAI, and stores in FAISS + PostgreSQL.

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend available at `http://localhost:5173`. The dev server proxies `/research` to the backend.

## UI

The frontend is a React + Vite single-page app. It streams research progress in real time via SSE:

- **Agent timeline** — each agent card shows status (queued → running → done) with animated loading messages
- **Synthesizer card** — expands inline with live markdown as the report streams token by token
- **Search bar** — submit a plain-English question about any covered ticker

## API endpoints

| Method | Path | Description |
| ------ | ---- | ----------- |
| `GET` | `/analyze/{ticker}` | Financial snapshot with buy/hold/sell signal |
| `POST` | `/analyze/batch` | Analyze multiple tickers |
| `POST` | `/research` | Full multi-agent research (blocking) |
| `GET` | `/research/stream` | SSE streaming of research progress + synthesis |
| `POST` | `/filings/ask` | Q&A on SEC 10-K filings with citations |
| `GET` | `/retrieve` | Raw FAISS similarity search |
| `POST` | `/news/sentiment` | News sentiment analysis for a ticker |

Interactive docs: `http://localhost:8000/docs`

## Data sources

- **yfinance** — Live prices, P/E, margins, fundamentals
- **SEC EDGAR** — 10-K filings for AAPL, MSFT, NVDA, GOOGL, AMZN, TSLA, META, ORCL, INTC, AMD, QCOM, JPM, LLY, PFE, UNH, RTX
- **Finnhub** — Recent news headlines

## Development

### Backend (without Docker)

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

PostgreSQL must be running separately (see `infra/docker-compose.yml` for connection details).

### Tests

```bash
cd backend
uv run pytest
```

### Environment variables

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `ANTHROPIC_API_KEY` | Yes | Claude API key (market, filings, news agents) |
| `OPENAI_API_KEY` | Yes | GPT-4o-mini (router + synthesizer) + embeddings |
| `FINNHUB_API_KEY` | Yes | News headlines |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `LANGSMITH_API_KEY` | No | LLM tracing |
| `LANGSMITH_TRACING` | No | Enable tracing (`true`/`false`) |
| `FINANCIAL_AGENT_MODEL` | No | Override Claude model (default: `claude-opus-4-5`) |
| `FILINGS_AGENT_MODEL` | No | Override Claude model (default: `claude-opus-4-5`) |
| `NEWS_AGENT_MODEL` | No | Override Claude model (default: `claude-opus-4-5`) |
| `ROUTER_AGENT_MODEL` | No | Override router model (default: `gpt-4o-mini`) |
| `SYNTHESIZER_MODEL` | No | Override synthesizer model (default: `gpt-4o-mini`) |
| `NEWS_MAX_ARTICLES` | No | Max articles fetched from Finnhub per request (default: `30`) |
| `NEWS_BATCH_SIZE` | No | Articles per Claude scoring batch (default: `15`) |
| `NEWS_DAYS_LOOKBACK` | No | Days of news history to fetch (default: `7`) |
| `NEWS_AGENT_MAX_TOKENS` | No | Max tokens for news scoring responses (default: `2000`) |
| `FINANCIAL_AGENT_MAX_TOKENS` | No | Max tokens for financial analysis response (default: `1500`) |
| `FILINGS_AGENT_MAX_TOKENS` | No | Max tokens for filings answer (default: `1024`) |
| `FILINGS_RETRIEVAL_K` | No | Number of SEC chunks to retrieve per query (default: `5`) |
| `SYNTHESIZER_MAX_TOKENS` | No | Max tokens for synthesis report (default: `1024`) |

## Status

| Phase | Feature | Status |
| ----- | ------- | ------ |
| 1 | Single-ticker financial analysis | Done |
| 2 | SEC filing RAG (FAISS + PostgreSQL) | Done |
| 3 | Multi-agent LangGraph routing | Done |
| 4 | News sentiment + SSE streaming | Done |
| 5 | React frontend with live agent timeline | Done |
| 6 | Evaluation harness | In progress |
