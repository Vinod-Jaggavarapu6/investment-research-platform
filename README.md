# Investment Research Platform

An AI-powered investment research platform that combines multi-agent orchestration, RAG over SEC 10-K filings, real-time market data, and news sentiment analysis to produce grounded research reports with live streaming.

![Demo](docs/demo.gif)

---

## Architecture

```
                                  ┌────────────┐
                                  │    User    │
                                  └─────┬──────┘
                                        │
                                        ▼
                          ┌─────────────────────────┐
                          │   Application Load       │
                          │   Balancer  (ALB)        │
                          └────────────┬────────────┘
                                       │
                        ┌──────────────┴──────────────┐
                        │                             │
                        ▼                             ▼
          ┌─────────────────────────┐   ┌─────────────────────────┐
          │    Frontend · ECS       │   │    Backend · ECS        │
          │    React + Nginx        │   │    FastAPI + SSE        │
          └─────────────────────────┘   └────────────┬────────────┘
                                                     │
                                                     ▼
                                    ┌─────────────────────────────────┐
                                    │       LangGraph Pipeline        │
                                    │                                 │
                                    │  ┌───────────────────────────┐  │
                                    │  │       Router Agent        │  │
                                    │  │    (extracts ticker)      │  │
                                    │  └─────────────┬─────────────┘  │
                                    │                │                │
                                    │                ▼                │
                                    │  ┌───────────────────────────┐  │
                                    │  │     Parallel Fan-out      │  │
                                    │  └──────┬──────────┬──────┬──┘  │
                                    │         │          │      │      │
                                    │         ▼          ▼      ▼      │
                                    │  ┌──────────┐ ┌────────┐ ┌──────┐│
                                    │  │  Market  │ │Filings │ │ News ││
                                    │  │  Agent   │ │ Agent  │ │Agent ││
                                    │  │ yfinance │ │ FAISS  │ │Finnhub│
                                    │  └────┬─────┘ └───┬────┘ └──┬───┘│
                                    │       └───────────┼──────────┘    │
                                    │                   │               │
                                    │                   ▼               │
                                    │  ┌───────────────────────────┐  │
                                    │  │       Synthesizer         │  │
                                    │  │   claude-opus · SSE       │  │
                                    │  └───────────────────────────┘  │
                                    └─────────────────────────────────┘
                                                     │
                          ┌──────────────────────────┼──────────────────────────┐
                          │                          │                          │
                          ▼                          ▼                          ▼
          ┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
          │     RDS PostgreSQL      │  │    ElastiCache Redis    │  │           S3            │
          │   · SEC chunks          │  │   · per-ticker cache    │  │   · FAISS index         │
          │   · LangGraph state     │  │   · TTL 1h / 5 min      │  │   · 10-K filings        │
          └─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘
```

---

## Evaluation Results

| Metric                  | Result                                  |
| ----------------------- | --------------------------------------- |
| RAG Top-3 Accuracy      | **88%** (50-question hand-labeled eval) |
| Full Report p95 Latency | **< 4 minutes**                         |
| FAISS Vectors           | **2,628**                               |
| Tickers Covered         | **20**                                  |

---

## Tech Stack

| Layer          | Technology                                        |
| -------------- | ------------------------------------------------- |
| LLM            | Claude (Anthropic) — haiku router, opus synthesis |
| Orchestration  | LangGraph + LangSmith                             |
| Embeddings     | OpenAI text-embedding-3-small                     |
| Vector Search  | FAISS IndexFlatIP (exact cosine similarity)       |
| API            | FastAPI + SSE streaming                           |
| Frontend       | React                                             |
| Cache          | Redis (ElastiCache)                               |
| Database       | PostgreSQL (RDS)                                  |
| Storage        | S3                                                |
| Containers     | Docker + AWS ECS Fargate                          |
| Infrastructure | Terraform                                         |
| CI/CD          | GitHub Actions                                    |
| Market Data    | yfinance                                          |
| News           | Finnhub API                                       |

---

## Features

**Multi-agent orchestration** — LangGraph routes questions through specialized agents running in parallel. A router agent extracts the ticker from the question, then three agents run concurrently: market data, SEC filings RAG, and news sentiment. A synthesizer combines all outputs into a grounded report.

**RAG over SEC 10-K filings** — 2,628 chunks from 20 company filings, embedded with OpenAI and indexed with FAISS IndexFlatIP (exact cosine similarity). 88% top-3 accuracy on a 50-question hand-labeled evaluation set. Sources are cited in the final report.

**Real-time SSE streaming** — the frontend connects via Server-Sent Events and receives per-node progress events as each agent completes, followed by token-by-token streaming of the final report for a typewriter effect.

**Redis caching** — research results are cached per ticker with TTL (1 hour for full reports, 5 minutes for market data). Cache hits return instantly, bypassing the full pipeline.

**Automated index refresh** — an ECS indexer task runs quarterly via EventBridge, downloading fresh 10-K filings from SEC EDGAR, re-embedding, rebuilding the FAISS index, and uploading it to S3.

---

<!-- ## Project Structure

```
investment-research/
├── backend/
│   ├── app/
│   │   ├── agents/          # Router, market, filings, news, synthesizer
│   │   ├── cache/           # Redis client + cache key schema
│   │   ├── graph.py         # LangGraph pipeline definition
│   │   ├── rag/             # FAISS index, embedder, chunker, ingest
│   │   ├── streaming.py     # SSE event generator
│   │   ├── tools/           # Retrieval tools
│   │   └── main.py          # FastAPI app + endpoints
│   ├── scripts/
│   │   └── build_index.py   # One-time indexer pipeline
│   ├── Dockerfile
│   └── Dockerfile.indexer
├── frontend/
│   ├── src/
│   │   ├── components/      # SearchBar, AgentTimeline
│   │   ├── useResearchStream.ts
│   │   └── App.tsx
│   ├── Dockerfile
│   └── nginx.conf
├── infra/
│   ├── terraform/           # VPC, ECS, RDS, ElastiCache, ALB, ECR, IAM
│   └── docker-compose.yml   # Local development stack
└── .github/
    └── workflows/
        ├── deploy-backend.yml
        └── deploy-frontend.yml
```

---

## Local Development

**Prerequisites:** Docker, uv

```bash
# Clone the repo
git clone https://github.com/your-username/investment-research-platform.git
cd investment-research-platform

# Copy and fill in environment variables
cp backend/.env.example backend/.env

# Start all services (backend, frontend, postgres, redis)
docker compose -f infra/docker-compose.yml up

# Backend: http://localhost:8000/docs
# Frontend: http://localhost:3000
```

**Environment variables required in `backend/.env`:**

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
LANGSMITH_API_KEY=ls-...
LANGSMITH_PROJECT=investment-research
FINNHUB_API_KEY=...
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/investment_research
REDIS_HOST=redis
REDIS_PORT=6379
```

**Build the FAISS index (first time only):**

```bash
cd backend
uv run python scripts/build_index.py
```

---

## API Endpoints

| Method | Endpoint                        | Description                             |
| ------ | ------------------------------- | --------------------------------------- |
| `GET`  | `/health`                       | Health check                            |
| `GET`  | `/research/stream?question=...` | Multi-agent research with SSE streaming |
| `POST` | `/research`                     | Multi-agent research (non-streaming)    |
| `GET`  | `/analyze/{ticker}`             | Single ticker financial analysis        |
| `POST` | `/filings/ask`                  | Ask a question about SEC filings        |
| `GET`  | `/retrieve`                     | Semantic search over SEC chunks         |
| `POST` | `/news/sentiment`               | News sentiment for a ticker             |

**Example — stream a research report:**

```bash
curl -N "http://localhost:8000/research/stream?question=What+is+Apple+revenue+trend"
```

---

## Deployment

Infrastructure is managed with Terraform on AWS.

```bash
# Initialize and deploy infrastructure
cd infra/terraform
terraform init
terraform apply

# Build and push images to ECR
docker build --platform linux/amd64 -t investment-research-backend:latest ./backend
docker push <ecr-url>/investment-research-backend:latest

# Deploy to ECS
aws ecs update-service \
  --cluster investment-research-cluster \
  --service investment-research-backend \
  --force-new-deployment
```

**CI/CD:** Pushing to `main` triggers GitHub Actions automatically. Changes to `backend/**` deploy only the backend. Changes to `frontend/**` deploy only the frontend.

**Index refresh:** The indexer ECS task runs automatically every quarter via EventBridge. To trigger manually:

```bash
aws ecs run-task \
  --cluster investment-research-cluster \
  --task-definition investment-research-indexer \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[...],securityGroups=[...],assignPublicIp=DISABLED}"
```

---

## CI/CD Flow

```
Push to main
    │
    ├── backend/** changed
    │       └── Build Docker image (linux/amd64)
    │           Push to ECR
    │           Force ECS deployment
    │           Wait for stable
    │
    └── frontend/** changed
            └── Build Docker image (linux/amd64)
                Push to ECR
                Force ECS deployment
                Wait for stable
``` -->
