# Investment Research Platform

An AI-powered investment research platform that combines multi-agent orchestration, RAG over SEC filings (10-K, 10-Q, 8-K), real-time market data, and news sentiment to produce grounded research reports — with live token streaming, persistent conversation history, and coherent multi-turn follow-up questions.

![Demo](docs/final-demo.gif)

---

## Architecture

```
                              ┌─────────────────────────┐
                              │     React Frontend      │
                              │  Conversation Sidebar   │
                              │  Chat Window + Timeline │
                              │      EventSource        │
                              └────────────┬────────────┘
                                           │  SSE stream
                                           ▼
                              ┌─────────────────────────┐
                              │    FastAPI Backend      │
                              │   /research/stream      │
                              │   /conversations        │
                              └────────────┬────────────┘
                                           │
                                           ▼
                              ┌─────────────────────────┐
                              │   LangGraph StateGraph  │
                              │  AsyncPostgresSaver     │
                              │  thread_id=conv_id      │
                              └────────────┬────────────┘
                                           │
                              ┌────────────▼────────────┐
                              │      Router Agent       │
                              │       GPT-4o-mini       │
                              │   ticker + route        │
                              │   prev_ticker fallback  │
                              └────────────┬────────────┘
                                           │
               ┌───────────────────────────┼──────────────────────────┐
               │               (route)     │              (route)     │
               ▼                           ▼                          ▼
  ┌────────────────────┐    ┌──────────────────────┐    ┌─────────────────────┐
  │   Market Agent     │    │   Filings Agent      │    │   Compare Agent     │
  │     yfinance       │    │   pgvector RAG       │    │   Parallel retrieve │
  │   GPT-4o           │    │   Query Rewriter     │    │   2-5 tickers       │
  └────────┬───────────┘    │   10-K/10-Q/8-K      │    └──────────┬──────────┘
           │                └──────────┬───────────┘               │
           │              ┌────────────▼────────────┐              │
           │              │    News Agent           │              │
           │              │    Finnhub + Claude     │              │
           │              └────────────┬────────────┘              │
           │                           │                           │
           └───────────────────────────▼                           │
                          ┌─────────────────────────┐              │
                          │      Synthesizer        │              │
                          │  Claude Sonnet · SSE    │              │
                          │  Conversation History   │              │
                          │  Prompt Caching         │              │
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
           │  + conversations    │    │                     │  │                    │
           │  + messages         │    │                     │  │                    │
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

### Persistent Conversation History

Every research session is persisted to Postgres and displayed in a Claude-style sidebar. Conversations are created eagerly at stream start — the sidebar entry appears while the first agent is still running, not after the full answer arrives.

- `conversations` table stores the title (derived from the first question), ticker chip, and timestamps
- `messages` table stores every user question and assistant answer as `role/content` pairs, linked by foreign key with `CASCADE DELETE`
- Session identity is maintained via `localStorage` so conversations survive page refreshes
- Four REST endpoints: `GET /conversations`, `POST /conversations`, `GET /conversations/{id}/messages`, `DELETE /conversations/{id}`

The key architectural insight: `conversation_id == LangGraph thread_id`. LangGraph's `AsyncPostgresSaver` persists full graph state per thread. By making these IDs equal, every follow-up question automatically resumes from the exact state of the previous turn — same ticker, same route context — without any custom session management code.

### Multi-Turn Follow-Up Questions

Follow-up questions that reference prior answers work coherently across three layers:

**Layer 1 — Router ticker inheritance.** The router reads `prev_ticker` from the LangGraph checkpoint before calling the LLM, then injects it as context into the prompt: `"[Conversation context: the previous question was about META]"`. A hard fallback after route normalisation copies `prev_ticker` if the LLM still returns null. This handles questions like `"How does that compare to their margins?"` correctly resolving `ticker=META`.

**Layer 2 — Retrieval query rewriting.** Before the filings agent calls pgvector, a lightweight `gpt-4o-mini` call rewrites the natural language question into a compact, pronoun-free retrieval query. `"How does that compare to their gross margin trend?"` + `ticker=META` becomes `"META gross margin trend"`. The original question is preserved for the answer-generation LLM; only the retrieval query is rewritten. Without this, pronoun-heavy follow-ups produce empty retrieval results because their embeddings carry no financial semantic content.

**Layer 3 — Synthesizer conversation history.** At stream start, the last 6 messages (3 Q&A pairs) are loaded from the database and injected into `AgentState`. The synthesizer prepends these prior turns to the Anthropic API messages array as alternating `user`/`assistant` entries before the current research context. The model can resolve references like `"elaborate on point 3"`, `"that figure you mentioned"`, or `"can you simplify the last section"` by looking at its prior answers.

### RAG over SEC Filings (10-K · 10-Q · 8-K)

For each ticker, the ingest pipeline downloads:

- **10-K** — most recent annual report
- **10-Q** — last 4 quarterly reports
- **8-K** — last 6 material event reports

Filings are parsed, chunked at 1600 chars with 200-char overlap, prefixed with `[TICKER YEAR FILING-TYPE — Section]` for embedding context, and stored in pgvector. Retrieval uses HNSW cosine similarity with optional ticker and filing-type filters.

### Multi-Ticker Comparison

Ask questions like _"Compare NVDA vs AMD on AI chip strategy"_ or _"AAPL vs MSFT vs GOOGL — cloud margins"_. The compare agent runs parallel `asyncio.gather` pgvector queries for each ticker, formats per-company context blocks, and calls Claude to produce a structured comparison with per-company findings, a head-to-head section, and a bottom-line summary — all with inline citations.

### Real-Time SSE Streaming

The frontend connects via `EventSource`. The streaming layer runs three concurrent async components: a `run_graph` task consuming LangGraph's `astream_events`, a `forward_tokens` task bridging the synthesizer's token stream, and a main SSE queue drained by the FastAPI async generator.

Event types emitted to the frontend:

| Event                | When                                            |
| -------------------- | ----------------------------------------------- |
| `conversation_ready` | Immediately after conversation row is created   |
| `node_start`         | Each LangGraph node begins execution            |
| `node_complete`      | Each node finishes, includes summary data       |
| `token`              | Each synthesizer token (typewriter effect)      |
| `done`               | Stream complete, includes final report          |
| `error`              | Unhandled exception in the graph                |

### Prompt Caching

The synthesizer marks the research data block with Anthropic's `cache_control: ephemeral`. On follow-up questions about the same company where the retrieved research is similar, Anthropic serves the prefix from its 1-hour cache at significantly reduced input token cost. Cache usage is logged per request (`cache_creation`, `cache_read`, `uncached` token counts).

### On-Demand Ingest with Polling

Asking about a ticker not yet indexed triggers automatic background ingestion. The graph short-circuits with `ingest_pending`, the frontend polls `/ingest/status/{ticker}` every 30 seconds, and automatically re-runs the research query when filings are ready.

### Redis Caching

Full research reports are cached per `(ticker, question)` with TTL. Cache hits bypass the entire agent pipeline and return instantly. The cache is checked as a short-circuit node inside the LangGraph graph. Cache writes are skipped when `ingest_pending=True` to avoid caching incomplete results.

---

## Multi-Turn Flow

```
User: "What is Meta's gross margin trend?"
  → Router: ticker=META, route=filings
  → Filings Agent: search_query="META gross margin trend" → retrieves chunks
  → Synthesizer: generates report with citations
  → Persisted to DB: conversation row + user message + assistant message

User: "What's driving that improvement?"
  → stream start: loads prior 2 messages from DB → injected into AgentState.messages
  → Router: reads prev_ticker=META from checkpoint → injects context hint → ticker=META
  → Filings Agent: rewrites "What's driving that improvement?" → "META gross margin improvement drivers"
                   → retrieves relevant chunks
  → Synthesizer: receives [prior Q, prior A, current research] → answers with full context
  → Persisted to DB: 2 new messages appended to same conversation

User: "Can you elaborate on the R&D section?"
  → stream start: loads prior 4 messages (2 Q&A pairs) → injected into AgentState.messages
  → Synthesizer: sees prior answers, knows exactly what the R&D section contained
                 → elaborates coherently without starting from scratch
```

<!--
## Project Structure

```text
investment-research/
├── backend/
│   ├── app/
│   │   ├── agents/
│   │   │   ├── router_agent.py       # GPT-4o-mini classifier (7 routes, ticker inheritance, prev_ticker fallback)
│   │   │   ├── financial_agent.py    # yfinance + GPT-4o market analysis
│   │   │   ├── filings_agent.py      # pgvector RAG + query rewriter + GPT-4o (10-K/10-Q/8-K)
│   │   │   ├── news_agent.py         # Finnhub + Claude news sentiment
│   │   │   ├── compare_agent.py      # Parallel multi-ticker comparison
│   │   │   └── synthesizer.py        # Claude Sonnet streaming synthesis + conversation history
│   │   ├── rag/
│   │   │   ├── ingest.py             # SEC EDGAR downloader + HTML parser
│   │   │   ├── chunker.py            # Section-aware chunker with context prefix
│   │   │   ├── embedder.py           # text-embedding-3-large batch embedder
│   │   │   └── background_ingest.py  # On-demand async ingest + status tracking
│   │   ├── tools/
│   │   │   ├── retrieval.py          # pgvector cosine search
│   │   │   ├── market_data.py        # yfinance wrapper
│   │   │   └── news_data.py          # Finnhub API wrapper
│   │   ├── cache/
│   │   │   ├── redis_client.py       # Async Redis client
│   │   │   └── cache_keys.py         # Key schema + TTL constants
│   │   ├── graph.py                  # LangGraph pipeline definition + conditional edges
│   │   ├── streaming.py              # SSE generator, eager conversation creation, message loading
│   │   ├── state.py                  # AgentState TypedDict (includes messages field)
│   │   ├── database.py               # SQLAlchemy models: FilingChunk, Conversation, Message
│   │   ├── models.py                 # Pydantic response models
│   │   └── main.py                   # FastAPI app + research + conversation endpoints
│   ├── scripts/
│   │   ├── build_index.py            # Full ingest pipeline (download → embed → store)
│   │   ├── evaluate_retrieval.py     # Local retrieval eval (hit@k, MRR)
│   │   ├── evaluate_pipeline.py      # End-to-end LLM-as-judge eval
│   │   ├── upload_eval_dataset.py    # Push eval_set.json to LangSmith
│   │   └── langsmith_eval.py         # Run evals via LangSmith aevaluate()
│   ├── data/
│   │   ├── eval_set.json             # 60-question gold-label eval set
│   │   └── eval_history.jsonl        # Local eval run history
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── AgentTimeline.tsx     # Live node status + streaming report + citations
│   │   │   ├── ChatWindow.tsx        # Message feed: DB history + live AgentTimeline
│   │   │   ├── ChatInput.tsx         # Pill-shaped input bar
│   │   │   ├── ConversationSidebar.tsx  # Conversation list with ticker chips + delete
│   │   │   └── FinalReport.tsx       # Markdown renderer with table support
│   │   ├── useResearchStream.ts      # EventSource hook + SSE state reducer + reset()
│   │   ├── useConversations.ts       # Conversation list + message loading hook
│   │   ├── types.ts                  # ResearchState, SSEEvent, Conversation, Message types
│   │   └── App.tsx                   # Top-level orchestration: streaming + history state
│   ├── nginx.local.conf              # nginx reverse proxy (SSE + API routes)
│   ├── Dockerfile
│   └── vite.config.ts
└── docker-compose.yml                # 4-service stack: backend, frontend, postgres, redis
```

---

## Database Schema

```sql
-- SEC filing chunks for RAG retrieval
filing_chunks (id, ticker, year, filing_type, section, text, embedding vector(1536))

-- LangGraph checkpoint tables (managed by AsyncPostgresSaver)
checkpoints, checkpoint_blobs, checkpoint_migrations

-- Conversation history
conversations (id, session_id, title, ticker, created_at, updated_at)
messages      (id, conversation_id → CASCADE, role, content, created_at)
```

---

## API Endpoints

| Method   | Path                              | Description                              |
| -------- | --------------------------------- | ---------------------------------------- |
| `GET`    | `/research/stream`                | SSE stream: question + optional conv ID  |
| `GET`    | `/conversations`                  | List conversations for a session         |
| `POST`   | `/conversations`                  | Create a conversation manually           |
| `GET`    | `/conversations/{id}/messages`    | Load full message history                |
| `DELETE` | `/conversations/{id}`             | Delete conversation and all messages     |
| `POST`   | `/ingest/{ticker}`                | Trigger background SEC filing ingest     |
| `GET`    | `/ingest/status/{ticker}`         | Poll ingest job status                   |
| `GET`    | `/news/{ticker}`                  | Fetch recent headlines                   |

---

## Environment Variables

```env
# LLM
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/research

# Cache
REDIS_URL=redis://localhost:6379

# Observability
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=investment-research

# Model overrides (optional)
ROUTER_AGENT_MODEL=gpt-4o-mini
FILINGS_AGENT_MODEL=gpt-4o
SYNTHESIZER_MODEL=claude-sonnet-4-6
```

---

## Running Locally

```bash
# Start all services
docker compose up --build

# Ingest SEC filings for a ticker
curl -X POST http://localhost:8000/ingest/AAPL

# Or ingest a batch via script
cd backend && python scripts/build_index.py

# Open the app
open http://localhost:3000
```

---

## Key Design Decisions

**`conversation_id == thread_id`** — LangGraph's checkpointer persists full graph state per thread ID. By equating it with conversation ID, every follow-up question resumes from the prior turn's exact state for free. No custom session management needed.

**Two-store persistence** — LangGraph checkpoint holds graph execution state (ticker, route, agent outputs). A separate `messages` table holds human-readable Q&A history. The checkpoint is for graph continuity; the messages table is for the UI and for feeding conversation context back to the synthesizer.

**Eager conversation creation** — The conversation row is written to DB and a `conversation_ready` SSE event is emitted before the graph starts executing. This lets the sidebar update while the first agent is still running rather than waiting 20-30 seconds for the full answer.

**Query rewriting before retrieval** — Embedding a pronoun-heavy follow-up question (`"How does that compare to their margins?"`) produces a vector with no financial semantic content. A fast gpt-4o-mini call rewrites it to a compact query (`"META gross margin comparison"`) before the pgvector search. The original question is preserved for the answer-generation LLM.

**Synthesizer conversation history** — The last 6 messages are loaded from DB at stream start and prepended to the Anthropic API messages array. The synthesizer can resolve references to its prior answers without the retrieval agents needing to know about conversation history.

**`pendingQuestion` as transition gate** — `pendingQuestion` and `historyMessages` are set in the same React `.then()` callback. React 18 batches these into a single render. Using `pendingQuestion !== null` to control `AgentTimeline` visibility ensures the live view disappears in the same render that the DB history appears — no flash, no duplicate.
-->
