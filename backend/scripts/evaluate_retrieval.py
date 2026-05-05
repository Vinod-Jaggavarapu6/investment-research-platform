"""
evaluate_retrieval.py — Measure RAG retrieval quality against pgvector

Gold labels: each eval question specifies an expected (ticker, section).
Retrieval runs WITHOUT ticker filtering so we measure raw embedding quality —
if AAPL chunks come back for an NVDA question, that's a real failure.

Metrics:
  hit@k          — any retrieved chunk matches gold (ticker+section, or section-only
                   for cross-ticker questions where ticker=null)
  mrr            — 1/rank of first matching chunk (mean across questions)
  ticker_prec@k  — fraction of top-k from the correct ticker (ticker questions only)
  section_prec@k — fraction of top-k from the correct section (all questions)

Results appended to data/eval_history.jsonl for trend tracking.
"""

import asyncio
import json
import logging
import sys
import os
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.clients import init_clients
from app.database import AsyncSessionLocal
from app.tools.retrieval import retrieve_chunks, ticker_has_data

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

EVAL_SET_PATH  = Path("data/eval_set.json")
RESULTS_PATH   = Path("data/eval_results.json")
HISTORY_PATH   = Path("data/eval_history.jsonl")
K_VALUES       = [1, 3, 5]
RETRIEVE_K     = 10   # fetch more than we evaluate so MRR is meaningful

# The DB has two naming conventions for the same sections because 10-K uses
# structural names ("Item 1A") while 10-Q used human-readable names ("Risk
# Factors") before the ingest normalization fix.  Both are correct answers.
SECTION_ALIASES: dict[str, set[str]] = {
    "Item 1A": {"Item 1A", "Risk Factors"},
    "Item 7":  {"Item 7", "MD&A", "Results of Operations"},
    "Item 7A": {"Item 7A", "Market Risk"},
    "Item 8":  {"Item 8", "Financial Statements"},
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EvalQuestion:
    id:            int
    question:      str
    ticker:        str | None   # None = cross-ticker question
    section:       str          # e.g. "Item 1A", "Item 7"
    cross_ticker:  bool = field(init=False)

    def __post_init__(self):
        self.cross_ticker = self.ticker is None


@dataclass
class EvalResult:
    question_id:    int
    question:       str
    gold_ticker:    str | None
    gold_section:   str
    retrieved:      list[dict]        # full chunk dicts in rank order
    skipped:        bool = False      # True if ticker not in DB

    # computed after init
    hit_at:         dict[int, bool]   = field(default_factory=dict)
    mrr:            float             = 0.0
    ticker_prec:    dict[int, float]  = field(default_factory=dict)
    section_prec:   dict[int, float]  = field(default_factory=dict)

    def compute(self):
        for k in K_VALUES:
            top_k = self.retrieved[:k]
            self.hit_at[k]       = any(self._is_hit(c) for c in top_k)
            valid_sections = SECTION_ALIASES.get(self.gold_section, {self.gold_section})
            self.section_prec[k] = self._prec(top_k, lambda c, vs=valid_sections: c["section"] in vs)
            if self.gold_ticker:
                self.ticker_prec[k] = self._prec(top_k, lambda c: c["ticker"] == self.gold_ticker)

        for rank, chunk in enumerate(self.retrieved, start=1):
            if self._is_hit(chunk):
                self.mrr = 1.0 / rank
                break

    def _is_hit(self, chunk: dict) -> bool:
        valid_sections = SECTION_ALIASES.get(self.gold_section, {self.gold_section})
        section_ok = chunk["section"] in valid_sections
        if self.gold_ticker is None:
            return section_ok
        return section_ok and chunk["ticker"] == self.gold_ticker

    @staticmethod
    def _prec(chunks: list[dict], pred) -> float:
        if not chunks:
            return 0.0
        return round(sum(1 for c in chunks if pred(c)) / len(chunks), 4)


# ---------------------------------------------------------------------------
# Load eval set
# ---------------------------------------------------------------------------

def load_eval_set(path: Path) -> list[EvalQuestion]:
    data = json.loads(path.read_text())
    return [
        EvalQuestion(
            id=item["id"],
            question=item["question"],
            ticker=item.get("ticker"),
            section=item["section"],
        )
        for item in data
    ]


# ---------------------------------------------------------------------------
# Evaluate a single question
# ---------------------------------------------------------------------------

async def evaluate_question(
    question: EvalQuestion,
    db,
) -> EvalResult:
    # Skip if ticker not indexed (avoids trivially-empty results polluting metrics)
    if question.ticker:
        has_data = await ticker_has_data(question.ticker, db)
        if not has_data:
            logger.warning("Q%d: ticker %s not in DB — skipping", question.id, question.ticker)
            return EvalResult(
                question_id=question.id,
                question=question.question,
                gold_ticker=question.ticker,
                gold_section=question.section,
                retrieved=[],
                skipped=True,
            )

    # Retrieve WITHOUT ticker filter — measures raw embedding quality
    chunks = await retrieve_chunks(
        query=question.question,
        db=db,
        ticker=None,
        k=RETRIEVE_K,
    )

    result = EvalResult(
        question_id=question.id,
        question=question.question,
        gold_ticker=question.ticker,
        gold_section=question.section,
        retrieved=chunks,
    )
    result.compute()
    return result


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------

def compute_metrics(results: list[EvalResult]) -> dict:
    active  = [r for r in results if not r.skipped]
    ticker_q = [r for r in active if r.gold_ticker]  # ticker-specific only
    n = len(active)
    if n == 0:
        return {"error": "no evaluable questions (all skipped — are tickers indexed?)"}

    def avg(items, fn):
        vals = [fn(r) for r in items]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    metrics: dict = {
        "total":   len(results),
        "active":  n,
        "skipped": len(results) - n,
    }
    for k in K_VALUES:
        metrics[f"hit@{k}"]          = avg(active,   lambda r, k=k: float(r.hit_at.get(k, False)))
        metrics[f"section_prec@{k}"] = avg(active,   lambda r, k=k: r.section_prec.get(k, 0.0))
        if ticker_q:
            metrics[f"ticker_prec@{k}"] = avg(ticker_q, lambda r, k=k: r.ticker_prec.get(k, 0.0))

    metrics["mrr"]            = avg(active,   lambda r: r.mrr)
    metrics["ticker_q_count"] = len(ticker_q)
    return metrics


# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------

def print_results(results: list[EvalResult], metrics: dict) -> None:
    print("\n" + "=" * 72)
    print("RETRIEVAL EVALUATION RESULTS")
    print("=" * 72)

    print(f"\n{'ID':<4} {'Ticker':<6} {'Section':<10} "
          f"{'@1':<4} {'@3':<4} {'@5':<4} {'MRR':<6} Question")
    print("-" * 72)

    for r in sorted(results, key=lambda x: x.question_id):
        if r.skipped:
            print(f"{r.question_id:<4} {(r.gold_ticker or 'ALL'):<6} "
                  f"{r.gold_section:<10} {'—':<4} {'—':<4} {'—':<4} {'—':<6} "
                  f"[SKIPPED — not indexed] {r.question[:30]}")
            continue

        h1 = "✅" if r.hit_at.get(1) else "❌"
        h3 = "✅" if r.hit_at.get(3) else "❌"
        h5 = "✅" if r.hit_at.get(5) else "❌"
        q  = r.question[:38] + "..." if len(r.question) > 38 else r.question
        print(f"{r.question_id:<4} {(r.gold_ticker or 'ALL'):<6} "
              f"{r.gold_section:<10} {h1:<4} {h3:<4} {h5:<4} "
              f"{r.mrr:<6.2f} {q}")

    misses = [r for r in results if not r.skipped and not r.hit_at.get(5)]
    if misses:
        print(f"\n--- Top-5 misses ({len(misses)}) ---")
        for r in misses:
            print(f"\n  Q{r.question_id} [{r.gold_ticker or 'cross'} / {r.gold_section}]: {r.question}")
            print(f"  Retrieved: " + ", ".join(
                f"{c['ticker']}/{c['section']}({c['score']:.2f})"
                for c in r.retrieved[:5]
            ))

    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    if "error" in metrics:
        print(f"  ERROR: {metrics['error']}")
        return

    print(f"  Questions  : {metrics['active']} active, {metrics['skipped']} skipped")
    print(f"  hit@1      : {metrics['hit@1']:.1%}")
    print(f"  hit@3      : {metrics['hit@3']:.1%}")
    print(f"  hit@5      : {metrics['hit@5']:.1%}")
    print(f"  MRR        : {metrics['mrr']:.4f}")
    print(f"  section_prec@5  : {metrics.get('section_prec@5', 0):.1%}  (any section match in top-5)")
    if "ticker_prec@5" in metrics:
        print(f"  ticker_prec@5   : {metrics.get('ticker_prec@5', 0):.1%}  (right ticker in top-5, n={metrics['ticker_q_count']})")
    print("=" * 72)

    h3 = metrics["hit@3"]
    print("\nDiagnosis:")
    if h3 >= 0.80:
        print(f"  ✅ hit@3 {h3:.0%} — retrieval is solid")
    elif h3 >= 0.65:
        print(f"  ⚠️  hit@3 {h3:.0%} — consider reducing chunk size or trying a stronger embedding model")
    else:
        print(f"  ❌ hit@3 {h3:.0%} — retrieval needs significant work (chunk size, overlap, or embedding model)")

    mrr = metrics["mrr"]
    if mrr >= 0.70:
        print(f"  ✅ MRR {mrr:.2f} — correct chunks rank highly")
    elif mrr >= 0.45:
        print(f"  ⚠️  MRR {mrr:.2f} — correct chunks appear but not near the top")
    else:
        print(f"  ❌ MRR {mrr:.2f} — correct chunks are buried; re-rank or tune embeddings")


# ---------------------------------------------------------------------------
# Persist results
# ---------------------------------------------------------------------------

def save_results(results: list[EvalResult], metrics: dict) -> None:
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "per_question": [
            {
                "id":            r.question_id,
                "question":      r.question,
                "gold_ticker":   r.gold_ticker,
                "gold_section":  r.gold_section,
                "skipped":       r.skipped,
                "hit@1":         r.hit_at.get(1, False),
                "hit@3":         r.hit_at.get(3, False),
                "hit@5":         r.hit_at.get(5, False),
                "mrr":           round(r.mrr, 4),
                "section_prec@5": r.section_prec.get(5, 0.0),
                "ticker_prec@5":  r.ticker_prec.get(5, 0.0),
                "top5_retrieved": [
                    {"ticker": c["ticker"], "section": c["section"], "score": c["score"]}
                    for c in r.retrieved[:5]
                ],
            }
            for r in results
        ],
    }
    RESULTS_PATH.write_text(json.dumps(output, indent=2))
    print(f"\nDetailed results → {RESULTS_PATH}")


def append_history(metrics: dict) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "script":    "retrieval",
        "metrics":   {k: v for k, v in metrics.items() if k not in ("total", "active", "skipped", "ticker_q_count")},
    }
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def print_trend() -> None:
    if not HISTORY_PATH.exists():
        return
    runs = [
        json.loads(line)
        for line in HISTORY_PATH.read_text().splitlines()
        if json.loads(line).get("script") == "retrieval"
    ]
    if len(runs) < 2:
        return

    recent = runs[-3:]
    print("\nTrend (last {} retrieval runs):".format(len(recent)))
    print(f"  {'Date':<22} {'hit@3':>6} {'hit@5':>6} {'MRR':>6}")
    for r in recent:
        ts = r["timestamp"][:16].replace("T", " ")
        m  = r["metrics"]
        print(f"  {ts:<22} {m.get('hit@3', 0):>6.1%} {m.get('hit@5', 0):>6.1%} {m.get('mrr', 0):>6.3f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    init_clients()

    if not EVAL_SET_PATH.exists():
        print(f"Eval set not found at {EVAL_SET_PATH}")
        return

    print(f"Loading eval set from {EVAL_SET_PATH}...")
    questions = load_eval_set(EVAL_SET_PATH)
    print(f"  {len(questions)} questions ({sum(1 for q in questions if q.ticker)} ticker-specific, "
          f"{sum(1 for q in questions if not q.ticker)} cross-ticker)")

    print(f"\nEvaluating (k={RETRIEVE_K}, no ticker filter)...")
    print("Each question makes one embedding API call.\n")

    results = []
    async with AsyncSessionLocal() as db:
        for i, q in enumerate(questions, 1):
            label = q.ticker or "ALL"
            print(f"  [{i:02d}/{len(questions)}] Q{q.id} [{label}/{q.section}]: {q.question[:55]}...")
            result = await evaluate_question(q, db)
            results.append(result)

            if result.skipped:
                print(f"    — skipped (not indexed)")
            else:
                h5 = "✅" if result.hit_at.get(5) else "❌"
                print(f"    {h5} hit@5  MRR={result.mrr:.2f}  "
                      f"top1={result.retrieved[0]['ticker']}/{result.retrieved[0]['section']} "
                      f"({result.retrieved[0]['score']:.2f})" if result.retrieved else "    — no results")

    metrics = compute_metrics(results)
    print_results(results, metrics)
    save_results(results, metrics)
    if "error" not in metrics:
        append_history(metrics)
    print_trend()


if __name__ == "__main__":
    asyncio.run(main())
