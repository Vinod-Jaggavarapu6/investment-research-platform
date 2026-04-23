"""
evaluate_pipeline.py — Measure Phase 3 multi-agent pipeline quality

Metrics computed:
  Router accuracy     : correct route classification (market/filings/both)
  Ticker accuracy     : correct ticker extraction from question
  Answer completeness : final_answer is non-empty and meets minimum length
  Citation presence   : filings/both routes return at least one citation
  End-to-end latency  : wall-clock time per question

Logged to LangSmith as a dataset + experiment for traceability.
"""

import asyncio
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
import os

sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

from app.database import AsyncSessionLocal, get_checkpointer_url
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.graph import build_graph
from app.rag.index import load_index
from app.tools.retrieval import init_retrieval

import langsmith
from langsmith import Client as LangSmithClient

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

EVAL_SET_PATH = Path("data/pipeline_eval_set.json")
RESULTS_PATH  = Path("data/pipeline_eval_results.json")


# ---------------------------------------------------------------------------
# Eval set schema
# ---------------------------------------------------------------------------

@dataclass
class PipelineQuestion:
    id:                 int
    question:           str
    expected_route:     str            # "market" | "filings" | "both" | "news" | "comprehensive"
    expected_ticker:    str | None
    requires_citations: bool
    requires_news:      bool = False   # ← add this

@dataclass
class PipelineResult:
    question_id:        int
    question:           str
    expected_route:     str
    expected_ticker:    str | None

    actual_route:       str | None
    actual_ticker:      str | None
    final_answer:       str
    citations:          list[dict]

    route_correct:      bool
    ticker_correct:     bool
    answer_non_empty:   bool
    answer_min_length:  bool
    citations_present:  bool
    news_present:       bool           # ← add this
    latency_ms:         float

# ---------------------------------------------------------------------------
# Hardcoded eval set — 30 questions across all three routes
# ---------------------------------------------------------------------------
# Same philosophy as Phase 2: gold labels are hand-curated, not generated.
# Route labels are ground truth — if the router disagrees, that's a miss.

# EVAL_QUESTIONS: list[dict] = [
#     # ── MARKET route (10 questions) ────────────────────────────────────────
#     {"id": 1,  "question": "What is Apple's current stock price?",
#      "expected_route": "market", "expected_ticker": "AAPL", "requires_citations": False},

#     {"id": 2,  "question": "What is Microsoft's market cap right now?",
#      "expected_route": "market", "expected_ticker": "MSFT", "requires_citations": False},

#     {"id": 3,  "question": "What is NVDA's trailing P/E ratio?",
#      "expected_route": "market", "expected_ticker": "NVDA", "requires_citations": False},

#     {"id": 4,  "question": "What is Tesla's current price to earnings ratio?",
#      "expected_route": "market", "expected_ticker": "TSLA", "requires_citations": False},

#     {"id": 5,  "question": "What is Google's revenue growth year over year?",
#      "expected_route": "market", "expected_ticker": "GOOGL", "requires_citations": False},

#     {"id": 6,  "question": "What is Amazon's gross margin?",
#      "expected_route": "market", "expected_ticker": "AMZN", "requires_citations": False},

#     {"id": 7,  "question": "What is Meta's forward P/E?",
#      "expected_route": "market", "expected_ticker": "META", "requires_citations": False},

#     {"id": 8,  "question": "What is JPMorgan's current debt to equity ratio?",
#      "expected_route": "market", "expected_ticker": "JPM",  "requires_citations": False},

#     {"id": 9,  "question": "What is Walmart's dividend yield?",
#      "expected_route": "market", "expected_ticker": "WMT",  "requires_citations": False},

#     {"id": 10, "question": "What is Visa's EV to EBITDA?",
#      "expected_route": "market", "expected_ticker": "V",    "requires_citations": False},

#     # ── FILINGS route (10 questions) ───────────────────────────────────────
#     {"id": 11, "question": "What risk factors did Apple disclose in their 10-K?",
#      "expected_route": "filings", "expected_ticker": "AAPL", "requires_citations": True},

#     {"id": 12, "question": "What did Microsoft say about their cloud segment in their annual filing?",
#      "expected_route": "filings", "expected_ticker": "MSFT", "requires_citations": True},

#     {"id": 13, "question": "What was Nvidia's revenue guidance in their most recent 10-K?",
#      "expected_route": "filings", "expected_ticker": "NVDA", "requires_citations": True},

#     {"id": 14, "question": "What did Tesla disclose about competition risks in their SEC filing?",
#      "expected_route": "filings", "expected_ticker": "TSLA", "requires_citations": True},

#     {"id": 15, "question": "What were Google's key business segments according to their 10-K?",
#      "expected_route": "filings", "expected_ticker": "GOOGL", "requires_citations": True},

#     {"id": 16, "question": "What did Amazon disclose about AWS growth in their annual report?",
#      "expected_route": "filings", "expected_ticker": "AMZN", "requires_citations": True},

#     {"id": 17, "question": "What did Meta say about regulatory risks in their 10-K?",
#      "expected_route": "filings", "expected_ticker": "META", "requires_citations": True},

#     {"id": 18, "question": "What were JPMorgan's stated priorities in their annual filing?",
#      "expected_route": "filings", "expected_ticker": "JPM",  "requires_citations": True},

#     {"id": 19, "question": "What did Walmart disclose about supply chain risks in their 10-K?",
#      "expected_route": "filings", "expected_ticker": "WMT",  "requires_citations": True},

#     {"id": 20, "question": "What did Visa say about competition in their SEC annual report?",
#      "expected_route": "filings", "expected_ticker": "V",    "requires_citations": True},

#     # ── BOTH route (10 questions) ──────────────────────────────────────────
#     {"id": 21, "question": "Is Apple's current valuation justified given what management guided in their 10-K?",
#      "expected_route": "both", "expected_ticker": "AAPL", "requires_citations": True},

#     {"id": 22, "question": "How does Microsoft's current P/E compare to their growth guidance in their annual filing?",
#      "expected_route": "both", "expected_ticker": "MSFT", "requires_citations": True},

#     {"id": 23, "question": "Is Nvidia fairly valued relative to the revenue growth they guided for in their 10-K?",
#      "expected_route": "both", "expected_ticker": "NVDA", "requires_citations": True},

#     {"id": 24, "question": "Does Tesla's current stock price reflect the risks they disclosed in their SEC filing?",
#      "expected_route": "both", "expected_ticker": "TSLA", "requires_citations": True},

#     {"id": 25, "question": "Is Google's current market cap justified by the business segments described in their 10-K?",
#      "expected_route": "both", "expected_ticker": "GOOGL", "requires_citations": True},

#     {"id": 26, "question": "How does Amazon's current gross margin compare to what they discussed in their annual report?",
#      "expected_route": "both", "expected_ticker": "AMZN", "requires_citations": True},

#     {"id": 27, "question": "Is Meta's forward P/E reasonable given the regulatory risks they disclosed?",
#      "expected_route": "both", "expected_ticker": "META", "requires_citations": True},

#     {"id": 28, "question": "How does JPMorgan's current debt to equity compare to their stated capital priorities?",
#      "expected_route": "both", "expected_ticker": "JPM",  "requires_citations": True},

#     {"id": 29, "question": "Is Walmart's dividend yield sustainable given what they disclosed about supply chain costs?",
#      "expected_route": "both", "expected_ticker": "WMT",  "requires_citations": True},

#     {"id": 30, "question": "Does Visa's current EV/EBITDA reflect the competition risks they outlined in their 10-K?",
#      "expected_route": "both", "expected_ticker": "V",    "requires_citations": True},

#      # ── NEWS route (5 questions) ───────────────────────────────────────────
#     {"id": 31, "question": "What is the current news sentiment around AAPL?",
#      "expected_route": "news", "expected_ticker": "AAPL",
#      "requires_citations": False, "requires_news": True},

#     {"id": 32, "question": "What are analysts saying about TSLA this week?",
#      "expected_route": "news", "expected_ticker": "TSLA",
#      "requires_citations": False, "requires_news": True},

#     {"id": 33, "question": "What are the recent headlines for NVDA?",
#      "expected_route": "news", "expected_ticker": "NVDA",
#      "requires_citations": False, "requires_news": True},

#     {"id": 34, "question": "Is there positive momentum around MSFT right now?",
#      "expected_route": "news", "expected_ticker": "MSFT",
#      "requires_citations": False, "requires_news": True},

#     {"id": 35, "question": "What catalysts are driving META's price this week?",
#      "expected_route": "news", "expected_ticker": "META",
#      "requires_citations": False, "requires_news": True},

#     # ── COMPREHENSIVE route (5 questions) ──────────────────────────────────
#     {"id": 36, "question": "Give me a full picture of AAPL — valuation, filings, and recent news sentiment",
#      "expected_route": "comprehensive", "expected_ticker": "AAPL",
#      "requires_citations": True, "requires_news": True},

#     {"id": 37, "question": "Complete research on MSFT",
#      "expected_route": "comprehensive", "expected_ticker": "MSFT",
#      "requires_citations": True, "requires_news": True},

#     {"id": 38, "question": "What should I know about NVDA before investing?",
#      "expected_route": "comprehensive", "expected_ticker": "NVDA",
#      "requires_citations": True, "requires_news": True},

#     {"id": 39, "question": "Full analysis of TSLA — valuation, filings, and sentiment",
#      "expected_route": "comprehensive", "expected_ticker": "TSLA",
#      "requires_citations": True, "requires_news": True},

#     {"id": 40, "question": "Give me a complete investment analysis of AMZN",
#      "expected_route": "comprehensive", "expected_ticker": "AMZN",
#      "requires_citations": True, "requires_news": True},
# ]

EVAL_QUESTIONS: list[dict] = [
    # ── MARKET (2) ─────────────────────────────────────────────────────────
    {"id": 1,  "question": "What is Apple's current stock price?",
     "expected_route": "market", "expected_ticker": "AAPL",
     "requires_citations": False, "requires_news": False},

    {"id": 2,  "question": "What is Visa's EV to EBITDA?",
     "expected_route": "market", "expected_ticker": "V",
     "requires_citations": False, "requires_news": False},

    # ── FILINGS (2) ────────────────────────────────────────────────────────
    {"id": 3,  "question": "What risk factors did Apple disclose in their 10-K?",
     "expected_route": "filings", "expected_ticker": "AAPL",
     "requires_citations": True, "requires_news": False},

    {"id": 4,  "question": "What did Nvidia's revenue guidance in their most recent 10-K?",
     "expected_route": "filings", "expected_ticker": "NVDA",
     "requires_citations": True, "requires_news": False},

    # ── BOTH (2) ───────────────────────────────────────────────────────────
    {"id": 5,  "question": "Is Apple's current valuation justified given what management guided in their 10-K?",
     "expected_route": "both", "expected_ticker": "AAPL",
     "requires_citations": True, "requires_news": False},

    {"id": 6,  "question": "Is Nvidia fairly valued relative to the revenue growth they guided for in their 10-K?",
     "expected_route": "both", "expected_ticker": "NVDA",
     "requires_citations": True, "requires_news": False},

    # ── NEWS (2) ───────────────────────────────────────────────────────────
    {"id": 7,  "question": "What is the current news sentiment around AAPL?",
     "expected_route": "news", "expected_ticker": "AAPL",
     "requires_citations": False, "requires_news": True},

    {"id": 8,  "question": "What are the recent headlines for NVDA?",
     "expected_route": "news", "expected_ticker": "NVDA",
     "requires_citations": False, "requires_news": True},

    # ── COMPREHENSIVE (2) ──────────────────────────────────────────────────
    {"id": 9,  "question": "Give me a full picture of AAPL — valuation, filings, and recent news sentiment",
     "expected_route": "comprehensive", "expected_ticker": "AAPL",
     "requires_citations": True, "requires_news": True},

    {"id": 10, "question": "What should I know about NVDA before investing?",
     "expected_route": "comprehensive", "expected_ticker": "NVDA",
     "requires_citations": True, "requires_news": True},
]

# ---------------------------------------------------------------------------
# Load eval set
# ---------------------------------------------------------------------------

def load_eval_set() -> list[PipelineQuestion]:
    return [
        PipelineQuestion(
            id=q["id"],
            question=q["question"],
            expected_route=q["expected_route"],
            expected_ticker=q["expected_ticker"],
            requires_citations=q["requires_citations"],
            requires_news=q.get("requires_news", False),    # ← add this
        )
        for q in EVAL_QUESTIONS
    ]


# ---------------------------------------------------------------------------
# Evaluate one question
# ---------------------------------------------------------------------------

async def evaluate_question(
    question: PipelineQuestion,
    graph,
) -> PipelineResult:
    start = time.perf_counter()

    try:
        result = await asyncio.wait_for(
            graph.ainvoke(
                {"question": question.question},
                config={"configurable": {"thread_id": f"eval-{question.id}"}},
            ),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        logger.error(f"Q{question.id} timed out after 120s")
        result = {}
    except Exception as e:
        logger.error(f"Q{question.id} failed: {e}")
        result = {}

    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    actual_route  = result.get("route")
    actual_ticker = result.get("ticker")
    final_answer  = result.get("final_answer") or ""
    citations     = result.get("citations") or []
    news_output   = result.get("news_output")

    route_correct  = actual_route == question.expected_route
    ticker_correct = (
        actual_ticker == question.expected_ticker
        if question.expected_ticker is not None
        else actual_ticker is None
    )
    answer_non_empty  = len(final_answer.strip()) > 0
    answer_min_length = len(final_answer.strip()) >= 100
    citations_present = (
        len(citations) > 0
        if question.requires_citations
        else True
    )
    news_present = (
        bool(news_output and len(news_output.strip()) > 0)
        if question.requires_news
        else True           # not required — pass by default
    )

    return PipelineResult(
        question_id=question.id,
        question=question.question,
        expected_route=question.expected_route,
        expected_ticker=question.expected_ticker,
        actual_route=actual_route,
        actual_ticker=actual_ticker,
        final_answer=final_answer,
        citations=citations,
        route_correct=route_correct,
        ticker_correct=ticker_correct,
        answer_non_empty=answer_non_empty,
        answer_min_length=answer_min_length,
        citations_present=citations_present,
        news_present=news_present,
        latency_ms=latency_ms,
    )

# ---------------------------------------------------------------------------
# Compute aggregate metrics
# ---------------------------------------------------------------------------

def compute_metrics(results: list[PipelineResult]) -> dict:
    n = len(results)
    if n == 0:
        return {}

    by_route = {"market": [], "filings": [], "both": [], "news": [], "comprehensive": []}
    for r in results:
        if r.expected_route in by_route:
            by_route[r.expected_route].append(r)

    def accuracy(items, attr):
        if not items:
            return 0.0
        return round(sum(1 for r in items if getattr(r, attr)) / len(items), 4)

    return {
        "total": n,

        # Overall
        "route_accuracy":      accuracy(results, "route_correct"),
        "ticker_accuracy":     accuracy(results, "ticker_correct"),
        "answer_completeness": accuracy(results, "answer_non_empty"),
        "answer_quality":      accuracy(results, "answer_min_length"),
        "citation_coverage":   accuracy(
            [r for r in results if r.expected_route in ("filings", "both", "comprehensive")],
            "citations_present"
        ),
        "news_coverage":       accuracy(
            [r for r in results if r.expected_route in ("news", "comprehensive")],
            "news_present"
        ),

        # Per-route router accuracy
        "route_accuracy_market":        accuracy(by_route["market"],        "route_correct"),
        "route_accuracy_filings":       accuracy(by_route["filings"],       "route_correct"),
        "route_accuracy_both":          accuracy(by_route["both"],          "route_correct"),
        "route_accuracy_news":          accuracy(by_route["news"],          "route_correct"),
        "route_accuracy_comprehensive": accuracy(by_route["comprehensive"], "route_correct"),

        # Latency
        "avg_latency_ms": round(sum(r.latency_ms for r in results) / n, 1),
        "p95_latency_ms": round(sorted(r.latency_ms for r in results)[int(n * 0.95)], 1),
    }

# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------

def print_results(results: list[PipelineResult], metrics: dict) -> None:
    print("\n" + "=" * 80)
    print("PIPELINE EVALUATION RESULTS")
    print("=" * 80)

    print(f"\n{'ID':<4} {'Exp':<8} {'Got':<8} {'Ticker':<6} {'Rt':<3} {'Tk':<3} "
          f"{'Ans':<4} {'Cit':<4} {'ms':<7} Question")
    print("-" * 80)

    for r in sorted(results, key=lambda x: x.question_id):
        rt  = "✅" if r.route_correct     else "❌"
        tk  = "✅" if r.ticker_correct    else "❌"
        ans = "✅" if r.answer_min_length else "❌"
        cit = "✅" if r.citations_present else "❌"
        q   = r.question[:35] + "..." if len(r.question) > 35 else r.question
        print(
            f"{r.question_id:<4} {r.expected_route:<8} "
            f"{(r.actual_route or 'None'):<8} "
            f"{(r.expected_ticker or 'N/A'):<6} "
            f"{rt:<3} {tk:<3} {ans:<4} {cit:<4} "
            f"{r.latency_ms:<7.0f} {q}"
        )

    # Misrouted questions
    misrouted = [r for r in results if not r.route_correct]
    if misrouted:
        print(f"\n--- Misrouted questions ({len(misrouted)}) ---")
        for r in misrouted:
            print(f"  Q{r.question_id}: expected={r.expected_route} "
                  f"got={r.actual_route} | {r.question}")

    # Missing citations
    missing_citations = [
        r for r in results
        if r.expected_route in ("filings", "both") and not r.citations_present
    ]
    if missing_citations:
        print(f"\n--- Missing citations ({len(missing_citations)}) ---")
        for r in missing_citations:
            print(f"  Q{r.question_id}: {r.question}")

    print("\n" + "=" * 80)
    print("SUMMARY METRICS")
    print("=" * 80)
    print(f"  Total questions    : {metrics['total']}")
    print(f"  Router accuracy    : {metrics['route_accuracy']:.1%}")
    print(f"    market route     : {metrics['route_accuracy_market']:.1%}")
    print(f"    filings route    : {metrics['route_accuracy_filings']:.1%}")
    print(f"    both route       : {metrics['route_accuracy_both']:.1%}")
    print(f"    news route       : {metrics['route_accuracy_news']:.1%}")
    print(f"    comprehensive    : {metrics['route_accuracy_comprehensive']:.1%}")
    print(f"  Ticker accuracy    : {metrics['ticker_accuracy']:.1%}")
    print(f"  Answer completeness: {metrics['answer_completeness']:.1%}")
    print(f"  Answer quality     : {metrics['answer_quality']:.1%}  (>=100 chars)")
    print(f"  Citation coverage  : {metrics['citation_coverage']:.1%}  (filings+both+comprehensive)")
    print(f"  News coverage      : {metrics['news_coverage']:.1%}  (news+comprehensive routes)")
    print(f"  Avg latency        : {metrics['avg_latency_ms']:.0f}ms")
    print(f"  P95 latency        : {metrics['p95_latency_ms']:.0f}ms")
    print("=" * 80)

    # Resume bullet guidance
    route_acc = metrics["route_accuracy"]
    print("\nResume bullet guidance:")
    if route_acc >= 0.90:
        print(f"  ✅ Router accuracy {route_acc:.0%} — use this number directly")
    elif route_acc >= 0.75:
        print(f"  ⚠️  Router accuracy {route_acc:.0%} — consider refining router prompt")
    else:
        print(f"  ❌ Router accuracy {route_acc:.0%} — router prompt needs significant work")


# ---------------------------------------------------------------------------
# Log to LangSmith
# ---------------------------------------------------------------------------

def log_to_langsmith(results: list[PipelineResult], metrics: dict) -> None:
    try:
        ls_client = LangSmithClient()

        # Create or get dataset
        dataset_name = "investment-research-pipeline-eval"
        try:
            dataset = ls_client.create_dataset(
                dataset_name=dataset_name,
                description="Phase 3 multi-agent pipeline evaluation — router + synthesis quality",
            )
            # Populate with examples on first run
            for r in results:
                ls_client.create_example(
                    inputs={"question": r.question},
                    outputs={
                        "expected_route":  r.expected_route,
                        "expected_ticker": r.expected_ticker,
                    },
                    dataset_id=dataset.id,
                )
            print(f"\nLangSmith dataset created: {dataset_name}")
        except Exception:
            # Dataset already exists — skip creation
            pass

        project_name = "investment-research"    # ← update from phase3
        for r in results:
            ls_client.create_run(
                name=f"pipeline-eval-Q{r.question_id}",
                run_type="chain",
                project_name=project_name,
                inputs={"question": r.question},
                outputs={
                    "route":        r.actual_route,
                    "ticker":       r.actual_ticker,
                    "final_answer": r.final_answer,
                    "citations":    r.citations,
                },
                extra={
                    "route_correct":      r.route_correct,
                    "ticker_correct":     r.ticker_correct,
                    "answer_min_length":  r.answer_min_length,
                    "citations_present":  r.citations_present,
                    "news_present":       r.news_present,      # ← add this
                    "latency_ms":         r.latency_ms,
                    "metrics":            metrics,
                },
                end_time=__import__("datetime").datetime.utcnow(),
            )
        print(f"Logged {len(results)} runs to LangSmith project: {project_name}")

    except Exception as e:
        print(f"LangSmith logging failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

def save_results(results: list[PipelineResult], metrics: dict) -> None:
    output = {
        "metrics": metrics,
        "per_question": [
            {
                "id":               r.question_id,
                "question":         r.question,
                "expected_route":   r.expected_route,
                "expected_ticker":  r.expected_ticker,
                "actual_route":     r.actual_route,
                "actual_ticker":    r.actual_ticker,
                "route_correct":    r.route_correct,
                "ticker_correct":   r.ticker_correct,
                "answer_non_empty": r.answer_non_empty,
                "answer_min_length":r.answer_min_length,
                "citations_present":r.citations_present,
                "news_present":     r.news_present,            # ← add this
                "latency_ms":       r.latency_ms,
                "final_answer":     r.final_answer[:200],
                "num_citations":    len(r.citations),
            }
            for r in results
        ]
    }
    RESULTS_PATH.write_text(json.dumps(output, indent=2))
    print(f"\nDetailed results saved to {RESULTS_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("Loading eval set...")
    questions = load_eval_set()
    print(f"  {len(questions)} questions loaded")

    print("Initializing FAISS index...")
    init_retrieval()

    print("Building graph with Postgres checkpointer...")
    async with AsyncPostgresSaver.from_conn_string(get_checkpointer_url()) as checkpointer:
        await checkpointer.setup()

        async with AsyncSessionLocal() as db:
            graph = build_graph(db=db, checkpointer=checkpointer)

            print(f"\nEvaluating {len(questions)} questions...")
            print("Each question runs the full pipeline — expect ~60s total\n")

            results = []
            for i, question in enumerate(questions, 1):
                print(f"  [{i:02d}/{len(questions)}] Q{question.id} "
                      f"[{question.expected_route}]: {question.question[:55]}...")
                result = await evaluate_question(question, graph)
                results.append(result)

                # Print inline pass/fail so you can watch progress
                status = "✅" if (result.route_correct and result.answer_min_length) else "❌"
                print(f"    {status} route={result.actual_route} "
                      f"latency={result.latency_ms:.0f}ms "
                      f"citations={len(result.citations)}")

    metrics = compute_metrics(results)
    print_results(results, metrics)
    save_results(results, metrics)
    log_to_langsmith(results, metrics)


if __name__ == "__main__":
    asyncio.run(main())