"""
evaluate_pipeline.py — Measure multi-agent pipeline quality end-to-end

Metrics:
  router_accuracy     — correct route classification per question
  ticker_accuracy     — correct ticker extracted from question
  citation_coverage   — filings/both/comprehensive routes return ≥1 citation
  news_coverage       — news/comprehensive routes return news_output
  llm_quality_score   — Claude Haiku judges answer quality 1–5
  latency_ms          — wall-clock time per question

Results appended to data/eval_history.jsonl for trend tracking.
"""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import anthropic
from app.database import AsyncSessionLocal, get_checkpointer_url
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.graph import build_graph

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

RESULTS_PATH = Path("data/pipeline_eval_results.json")
HISTORY_PATH = Path("data/eval_history.jsonl")


# ---------------------------------------------------------------------------
# Eval set — 10 questions covering all 5 routes
# ---------------------------------------------------------------------------

EVAL_QUESTIONS: list[dict] = [
    # MARKET (2)
    {"id": 1, "question": "What is Apple's current stock price?",
     "expected_route": "market", "expected_ticker": "AAPL",
     "requires_citations": False, "requires_news": False},

    {"id": 2, "question": "What is Visa's EV to EBITDA?",
     "expected_route": "market", "expected_ticker": "V",
     "requires_citations": False, "requires_news": False},

    # FILINGS (2)
    {"id": 3, "question": "What risk factors did Apple disclose in their 10-K?",
     "expected_route": "filings", "expected_ticker": "AAPL",
     "requires_citations": True, "requires_news": False},

    {"id": 4, "question": "What did Nvidia say about their revenue guidance in their most recent 10-K?",
     "expected_route": "filings", "expected_ticker": "NVDA",
     "requires_citations": True, "requires_news": False},

    # BOTH (2)
    {"id": 5, "question": "Is Apple's current valuation justified given what management guided in their 10-K?",
     "expected_route": "both", "expected_ticker": "AAPL",
     "requires_citations": True, "requires_news": False},

    {"id": 6, "question": "Is Nvidia fairly valued relative to the revenue growth they guided for in their 10-K?",
     "expected_route": "both", "expected_ticker": "NVDA",
     "requires_citations": True, "requires_news": False},

    # NEWS (2)
    {"id": 7, "question": "What is the current news sentiment around AAPL?",
     "expected_route": "news", "expected_ticker": "AAPL",
     "requires_citations": False, "requires_news": True},

    {"id": 8, "question": "What are the recent headlines for NVDA?",
     "expected_route": "news", "expected_ticker": "NVDA",
     "requires_citations": False, "requires_news": True},

    # COMPREHENSIVE (2)
    {"id": 9, "question": "Give me a full picture of AAPL — valuation, filings, and recent news sentiment",
     "expected_route": "comprehensive", "expected_ticker": "AAPL",
     "requires_citations": True, "requires_news": True},

    {"id": 10, "question": "What should I know about NVDA before investing?",
     "expected_route": "comprehensive", "expected_ticker": "NVDA",
     "requires_citations": True, "requires_news": True},
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PipelineQuestion:
    id:                 int
    question:           str
    expected_route:     str
    expected_ticker:    str | None
    requires_citations: bool
    requires_news:      bool = False


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
    citations_present:  bool
    news_present:       bool
    latency_ms:         float
    llm_quality_score:  int   = 0   # 1–5 from judge, 0 = not scored
    llm_quality_reason: str   = ""


# ---------------------------------------------------------------------------
# LLM-as-judge (Claude Haiku — cheap, fast)
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """\
You are a financial research quality evaluator. Score the answer to the given question
on a 1–5 scale using ONLY the criteria below. Respond with valid JSON only.

Scoring rubric:
  5 — Excellent: specific numbers cited, sources referenced, clear reasoning,
      directly answers the question
  4 — Good: relevant and informative, some specific data, minor gaps
  3 — Adequate: on topic and non-empty but vague, missing key specifics
  2 — Weak: partially relevant but mostly generic or incomplete
  1 — Poor: empty, off-topic, hallucinated, or refused to answer

Return JSON only:
  {"score": <1-5>, "reason": "<one sentence explaining the score>"}
"""

_judge_client: anthropic.Anthropic | None = None


def _get_judge_client() -> anthropic.Anthropic:
    global _judge_client
    if _judge_client is None:
        _judge_client = anthropic.Anthropic()
    return _judge_client


def judge_answer(question: str, answer: str) -> tuple[int, str]:
    """Score the answer 1-5. Returns (score, reason). Falls back to 0 on error."""
    if not answer or len(answer.strip()) < 10:
        return 1, "Answer is empty or too short"

    try:
        client = _get_judge_client()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system=JUDGE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\n"
                        f"Answer (first 800 chars):\n{answer[:800]}"
                    ),
                }
            ],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        score  = int(parsed["score"])
        reason = parsed.get("reason", "")
        if not 1 <= score <= 5:
            raise ValueError(f"score {score} out of range")
        return score, reason
    except Exception as e:
        logger.warning("Judge failed for question %r: %s", question[:40], e)
        return 0, f"judge error: {e}"


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
            requires_news=q.get("requires_news", False),
        )
        for q in EVAL_QUESTIONS
    ]


# ---------------------------------------------------------------------------
# Evaluate one question through the full pipeline
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
                config={"configurable": {"thread_id": f"eval-{question.id}-{int(time.time())}"}},
            ),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        logger.error("Q%d timed out", question.id)
        result = {}
    except Exception as e:
        logger.error("Q%d failed: %s", question.id, e)
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
    citations_present = (len(citations) > 0) if question.requires_citations else True
    news_present      = (bool(news_output) and len(news_output.strip()) > 0) if question.requires_news else True

    # LLM judge runs only when an answer was produced
    llm_score, llm_reason = (0, "no answer") if not final_answer else judge_answer(question.question, final_answer)

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
        answer_non_empty=len(final_answer.strip()) > 0,
        citations_present=citations_present,
        news_present=news_present,
        latency_ms=latency_ms,
        llm_quality_score=llm_score,
        llm_quality_reason=llm_reason,
    )


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------

def compute_metrics(results: list[PipelineResult]) -> dict:
    n = len(results)
    if n == 0:
        return {}

    def accuracy(items, attr):
        if not items:
            return 0.0
        return round(sum(1 for r in items if getattr(r, attr)) / len(items), 4)

    by_route: dict[str, list] = {r: [] for r in ["market", "filings", "both", "news", "comprehensive"]}
    for r in results:
        if r.expected_route in by_route:
            by_route[r.expected_route].append(r)

    filings_results = [r for r in results if r.expected_route in ("filings", "both", "comprehensive")]
    news_results    = [r for r in results if r.expected_route in ("news", "comprehensive")]
    scored          = [r for r in results if r.llm_quality_score > 0]

    return {
        "total":                   n,
        "route_accuracy":          accuracy(results,        "route_correct"),
        "ticker_accuracy":         accuracy(results,        "ticker_correct"),
        "answer_completeness":     accuracy(results,        "answer_non_empty"),
        "citation_coverage":       accuracy(filings_results,"citations_present"),
        "news_coverage":           accuracy(news_results,   "news_present"),
        "llm_quality_avg":         round(sum(r.llm_quality_score for r in scored) / len(scored), 2) if scored else 0.0,
        "llm_quality_pct_good":    round(sum(1 for r in scored if r.llm_quality_score >= 4) / len(scored), 4) if scored else 0.0,
        "route_accuracy_market":         accuracy(by_route["market"],        "route_correct"),
        "route_accuracy_filings":        accuracy(by_route["filings"],       "route_correct"),
        "route_accuracy_both":           accuracy(by_route["both"],          "route_correct"),
        "route_accuracy_news":           accuracy(by_route["news"],          "route_correct"),
        "route_accuracy_comprehensive":  accuracy(by_route["comprehensive"], "route_correct"),
        "avg_latency_ms":          round(sum(r.latency_ms for r in results) / n, 1),
        "p95_latency_ms":          round(sorted(r.latency_ms for r in results)[int(n * 0.95)], 1),
    }


# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------

def print_results(results: list[PipelineResult], metrics: dict) -> None:
    print("\n" + "=" * 88)
    print("PIPELINE EVALUATION RESULTS")
    print("=" * 88)

    print(f"\n{'ID':<4} {'Exp':<8} {'Got':<8} {'Tk':<3} {'Rt':<3} "
          f"{'Cit':<4} {'Q⭐':<4} {'ms':<7} Question")
    print("-" * 88)

    for r in sorted(results, key=lambda x: x.question_id):
        rt    = "✅" if r.route_correct     else "❌"
        tk    = "✅" if r.ticker_correct    else "❌"
        cit   = "✅" if r.citations_present else "❌"
        stars = str(r.llm_quality_score) if r.llm_quality_score > 0 else "—"
        q     = r.question[:32] + "..." if len(r.question) > 32 else r.question
        print(
            f"{r.question_id:<4} {r.expected_route:<8} "
            f"{(r.actual_route or 'None'):<8} "
            f"{tk:<3} {rt:<3} {cit:<4} {stars:<4} "
            f"{r.latency_ms:<7.0f} {q}"
        )

    # Quality details for low-scoring answers
    low_quality = [r for r in results if 0 < r.llm_quality_score <= 2]
    if low_quality:
        print(f"\n--- Low quality answers (score ≤2, {len(low_quality)} questions) ---")
        for r in low_quality:
            print(f"  Q{r.question_id} [{r.expected_route}] score={r.llm_quality_score}: {r.llm_quality_reason}")
            print(f"    Answer preview: {r.final_answer[:120]}...")

    misrouted = [r for r in results if not r.route_correct]
    if misrouted:
        print(f"\n--- Misrouted ({len(misrouted)}) ---")
        for r in misrouted:
            print(f"  Q{r.question_id}: expected={r.expected_route} got={r.actual_route} | {r.question}")

    print("\n" + "=" * 88)
    print("SUMMARY")
    print("=" * 88)
    print(f"  Questions         : {metrics['total']}")
    print(f"  Router accuracy   : {metrics['route_accuracy']:.1%}")
    print(f"    market          : {metrics['route_accuracy_market']:.1%}")
    print(f"    filings         : {metrics['route_accuracy_filings']:.1%}")
    print(f"    both            : {metrics['route_accuracy_both']:.1%}")
    print(f"    news            : {metrics['route_accuracy_news']:.1%}")
    print(f"    comprehensive   : {metrics['route_accuracy_comprehensive']:.1%}")
    print(f"  Ticker accuracy   : {metrics['ticker_accuracy']:.1%}")
    print(f"  Answer produced   : {metrics['answer_completeness']:.1%}")
    print(f"  Citation coverage : {metrics['citation_coverage']:.1%}  (filings+both+comprehensive)")
    print(f"  News coverage     : {metrics['news_coverage']:.1%}  (news+comprehensive)")
    print(f"  LLM quality avg   : {metrics['llm_quality_avg']:.1f}/5  "
          f"({metrics['llm_quality_pct_good']:.0%} scored ≥4)")
    print(f"  Avg latency       : {metrics['avg_latency_ms']:.0f}ms")
    print(f"  P95 latency       : {metrics['p95_latency_ms']:.0f}ms")
    print("=" * 88)

    _print_diagnosis(metrics)


def _print_diagnosis(metrics: dict) -> None:
    print("\nDiagnosis:")
    ra = metrics["route_accuracy"]
    if ra >= 0.90:
        print(f"  ✅ Router {ra:.0%} — router prompt is solid")
    elif ra >= 0.75:
        print(f"  ⚠️  Router {ra:.0%} — consider refining router prompt examples")
    else:
        print(f"  ❌ Router {ra:.0%} — router prompt needs significant work")

    qa = metrics["llm_quality_avg"]
    if qa >= 4.0:
        print(f"  ✅ Quality {qa:.1f}/5 — synthesizer producing strong answers")
    elif qa >= 3.0:
        print(f"  ⚠️  Quality {qa:.1f}/5 — answers are adequate; tune synthesizer prompt or add more context")
    else:
        print(f"  ❌ Quality {qa:.1f}/5 — answers are weak; check retrieval K, chunk size, or synthesizer prompt")

    cc = metrics["citation_coverage"]
    if cc < 1.0:
        print(f"  ⚠️  Citation coverage {cc:.0%} — some filings queries return no citations (check DB has data)")


# ---------------------------------------------------------------------------
# Persist & history
# ---------------------------------------------------------------------------

def save_results(results: list[PipelineResult], metrics: dict) -> None:
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "per_question": [
            {
                "id":                  r.question_id,
                "question":            r.question,
                "expected_route":      r.expected_route,
                "expected_ticker":     r.expected_ticker,
                "actual_route":        r.actual_route,
                "actual_ticker":       r.actual_ticker,
                "route_correct":       r.route_correct,
                "ticker_correct":      r.ticker_correct,
                "answer_non_empty":    r.answer_non_empty,
                "citations_present":   r.citations_present,
                "news_present":        r.news_present,
                "llm_quality_score":   r.llm_quality_score,
                "llm_quality_reason":  r.llm_quality_reason,
                "latency_ms":          r.latency_ms,
                "num_citations":       len(r.citations),
                "answer_preview":      r.final_answer[:300],
            }
            for r in results
        ],
    }
    RESULTS_PATH.write_text(json.dumps(output, indent=2))
    print(f"\nDetailed results → {RESULTS_PATH}")


def append_history(metrics: dict) -> None:
    keep_keys = {
        "route_accuracy", "ticker_accuracy", "citation_coverage",
        "news_coverage", "llm_quality_avg", "llm_quality_pct_good",
        "avg_latency_ms",
    }
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script":    "pipeline",
        "metrics":   {k: v for k, v in metrics.items() if k in keep_keys},
    }
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def print_trend() -> None:
    if not HISTORY_PATH.exists():
        return
    runs = [
        json.loads(line)
        for line in HISTORY_PATH.read_text().splitlines()
        if json.loads(line).get("script") == "pipeline"
    ]
    if len(runs) < 2:
        return

    recent = runs[-3:]
    print("\nTrend (last {} pipeline runs):".format(len(recent)))
    print(f"  {'Date':<22} {'Router':>7} {'Quality':>8} {'Citations':>10} {'Latency':>8}")
    for r in recent:
        ts = r["timestamp"][:16].replace("T", " ")
        m  = r["metrics"]
        print(f"  {ts:<22} {m.get('route_accuracy', 0):>7.1%} "
              f"{m.get('llm_quality_avg', 0):>8.1f} "
              f"{m.get('citation_coverage', 0):>10.1%} "
              f"{m.get('avg_latency_ms', 0):>7.0f}ms")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    questions = load_eval_set()
    print(f"Loaded {len(questions)} eval questions")

    print("Building graph...")
    async with AsyncPostgresSaver.from_conn_string(get_checkpointer_url()) as checkpointer:
        await checkpointer.setup()

        async with AsyncSessionLocal() as db:
            graph = build_graph(db=db, checkpointer=checkpointer)

            print(f"\nEvaluating {len(questions)} questions (full pipeline + LLM judge per answer)...")
            print("Expect ~2–3 minutes total.\n")

            results = []
            for i, q in enumerate(questions, 1):
                print(f"  [{i:02d}/{len(questions)}] Q{q.id} [{q.expected_route}]: {q.question[:55]}...")
                result = await evaluate_question(q, graph)
                results.append(result)

                status = "✅" if (result.route_correct and result.llm_quality_score >= 3) else "❌"
                print(f"    {status} route={result.actual_route}  "
                      f"quality={result.llm_quality_score}/5  "
                      f"citations={len(result.citations)}  "
                      f"latency={result.latency_ms:.0f}ms")

    metrics = compute_metrics(results)
    print_results(results, metrics)
    save_results(results, metrics)
    append_history(metrics)
    print_trend()


if __name__ == "__main__":
    asyncio.run(main())
