# Investment Research Platform

An AI-powered investment research platform that combines multi-agent orchestration, RAG over SEC filings (10-K, 10-Q, 8-K), real-time market data, and news sentiment to produce grounded research reports — with live token streaming to the browser.

<video src="docs/demo.mp4" width="100%" controls></video>

---

## Architecture

```
                              ┌─────────────────────────┐
                              │     React Frontend      │
                              │      EventSource        │
                              └────────────┬────────────┘
                                           │  SSE stream
                                           ▼
                              ┌─────────────────────────┐
                              │    FastAPI Backend      │
                              │   /research/stream      │
                              └────────────┬────────────┘
                                           │
                                           ▼
                              ┌─────────────────────────┐
                              │   LangGraph StateGraph  │
                              └────────────┬────────────┘
                                           │
                              ┌────────────▼────────────┐
                              │      Router Agent       │
                              │       GPT-4o-mini       │
                              └────────────┬────────────┘
                                           │
               ┌───────────────────────────┼──────────────────────────┐
               │               (route)     │              (route)     │
               ▼                           ▼                          ▼
  ┌────────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
  │   Market Agent     │    │   Filings Agent      │    │   Compare Agent     │
  │     yfinance       │    │   pgvector RAG       │    │   Parallel retrieve │
  │   GPT-4o           │    │   10-K/10-Q/8-K      │    │   2-5 tickers       │
  └────────┬───────────┘    └──────────┬───────────┘    └──────────┬──────────┘
           │                           │                           │
           │              ┌────────────▼────────────┐              │
           │              │    News Agent           │              │
           │              │    Finnhub + Claude     │              │
           │              └────────────┬────────────┘              │
           │                           │                           │
           └───────────────────────────▼                           │
                          ┌─────────────────────────┐              │
                          │      Synthesizer        │              │
                          │  Claude Sonnet · SSE    │              │
                          └─────────────────────────┘              │
                                       │                           │
                                       └───────────────────────────┘
                                                    │
                         ┌──────────────────────────┼──────────────────────┐
                         ▼                          ▼                      ▼
           ┌─────────────────────┐    ┌─────────────────────┐  ┌────────────────────┐
           │  PostgreSQL 16      │    │    Redis 7          │  │   LangSmith        │
           │  + pgvector HNSW    │    │  Research cache     │  │   Tracing + Evals  │
           │  + LangGraph state  │    │                     │  │                    │
           └─────────────────────┘    └─────────────────────┘  └────────────────────┘
```

---

## Features

### Multi-Agent Orchestration

A LangGraph `StateGraph` routes every question through the right combination of agents. The router classifies the question into one of 7 routes and extracts one or more tickers, then the graph executes only the agents needed — in parallel where possible.

| Route            | Agents invoked          | When used                             |
| ---------------- | ----------------------- | ------------------------------------- |
| `market`         | Market                  | Live price, P/E, margins, ratios      |
| `filings`        | Filings                 | Annual SEC disclosures (10-K)         |
| `filings_recent` | Filings                 | Recent quarters or events (10-Q, 8-K) |
| `news`           | News                    | Sentiment, headlines, catalysts       |
| `both`           | Market + Filings        | Valuation vs. guidance questions      |
| `comprehensive`  | Market + Filings + News | Full analysis                         |
| `compare`        | Compare                 | Side-by-side of 2–5 companies         |

### RAG over SEC Filings (10-K · 10-Q · 8-K)

For each of 20 tickers, the ingest pipeline downloads:

- **10-K** — most recent annual report
- **10-Q** — last 4 quarterly reports
- **8-K** — last 6 material event reports

Filings are parsed, chunked at 1600 chars with 200-char overlap, prefixed with `[TICKER YEAR FILING-TYPE — Section]` for embedding context, and stored in pgvector. Retrieval uses HNSW cosine similarity with optional ticker and filing-type filters.

### Multi-Ticker Comparison

Ask questions like _"Compare NVDA vs AMD on AI chip strategy"_ or _"AAPL vs MSFT vs GOOGL — cloud margins"_. The compare agent runs parallel `asyncio.gather` pgvector queries for each ticker, formats per-company context blocks, and calls Claude to produce a structured comparison with per-company findings, a head-to-head section, and a bottom-line summary — all with inline citations.

### Real-Time SSE Streaming

The frontend connects via `EventSource`. Each LangGraph node emits `node_start` and `node_complete` events as it runs. The synthesizer streams tokens through an asyncio queue for a typewriter effect. The React state machine applies each event to a live `ResearchState`, updating the agent timeline in real time.

### On-Demand Ingest with Polling

Asking about a ticker not yet indexed triggers automatic background ingestion. The graph short-circuits with `ingest_pending`, the frontend polls `/ingest/status/{ticker}` every 30 seconds, and automatically re-runs the research query when filings are ready.

### Redis Caching

Research results are cached per `(ticker, question)` with TTL. Cache hits bypass the entire agent pipeline and return instantly. The cache is checked as a short-circuit node inside the LangGraph graph and invalidated manually via `/cache/debug` endpoints.

### Conversation Memory

LangGraph's Postgres checkpointer persists full graph state per `thread_id`, enabling multi-turn research sessions where follow-up questions reference prior answers.

### Retrieval Evaluation Pipeline

A 60-question eval set covering 10-K, 10-Q, 8-K, cross-ticker, and comparison question types. Metrics tracked per run:

| Metric               | What it measures                             |
| -------------------- | -------------------------------------------- |
| `hit@1 / @3 / @5`    | Correct chunk in top-k                       |
| `mrr`                | 1/rank of first correct chunk                |
| `section_prec@5`     | Fraction of top-5 from the right section     |
| `ticker_prec@5`      | Fraction of top-5 from the right ticker      |
| `filing_type_prec@5` | Fraction of top-5 from the right filing type |

Results are tracked in LangSmith Experiments for side-by-side comparison across runs. Retrieval accuracy improved from **48% → 72% hit@3** through iterative tuning.

## Project Structure

```
investment-research/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   │   ├── router_agent.py      # GPT-4o-mini classifier (7 routes)
│   │   │   ├── financial_agent.py   # yfinance + GPT-4o market analysis
│   │   │   ├── filings_agent.py     # pgvector RAG + GPT-4o (10-K/10-Q/8-K)
│   │   │   ├── news_agent.py        # Finnhub + Claude news sentiment
│   │   │   ├── compare_agent.py     # Parallel multi-ticker comparison
│   │   │   └── synthesizer.py       # Claude Sonnet streaming synthesis
│   │   ├── rag/
│   │   │   ├── ingest.py            # SEC EDGAR downloader + HTML parser
│   │   │   ├── chunker.py           # Section-aware chunker with context prefix
│   │   │   ├── embedder.py          # text-embedding-3-large batch embedder
│   │   │   └── background_ingest.py # On-demand async ingest + status tracking
│   │   ├── tools/
│   │   │   └── retrieval.py         # pgvector cosine search
│   │   ├── cache/
│   │   │   ├── redis_client.py      # Async Redis client
│   │   │   └── cache_keys.py        # Key schema + TTL constants
│   │   ├── graph.py                 # LangGraph pipeline definition
│   │   ├── streaming.py             # SSE event generator + asyncio queues
│   │   ├── state.py                 # AgentState TypedDict
│   │   ├── database.py              # SQLAlchemy async engine + pgvector schema
│   │   ├── models.py                # Pydantic API models
│   │   └── main.py                  # FastAPI app + all endpoints
│   ├── scripts/
│   │   ├── build_index.py           # Full ingest pipeline (download → embed → store)
│   │   ├── evaluate_retrieval.py    # Local retrieval eval (hit@k, MRR)
│   │   ├── evaluate_pipeline.py     # End-to-end LLM-as-judge eval
│   │   ├── upload_eval_dataset.py   # Push eval_set.json to LangSmith
│   │   └── langsmith_eval.py        # Run evals via LangSmith aevaluate()
│   ├── data/
│   │   ├── eval_set.json            # 60-question gold-label eval set
│   │   └── eval_history.jsonl       # Local eval run history
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── AgentTimeline.tsx    # Live node log + streaming report + citations
│   │   │   └── SearchBar.tsx        # Question input
│   │   ├── useResearchStream.ts     # EventSource hook + SSE state reducer
│   │   ├── types.ts                 # ResearchState, NodeName, SSEEvent types
│   │   └── App.tsx
│   ├── Dockerfile
│   └── nginx.conf
└── infra/
    ├── docker-compose.yml           # Local 4-service stack
    └── .env                         # Postgres credentials
```
