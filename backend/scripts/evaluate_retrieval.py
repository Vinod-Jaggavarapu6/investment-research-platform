"""
evaluate_retrieval.py — Measure RAG retrieval accuracy against the eval set

Metrics computed:
  top-1  accuracy: correct chunk appears as rank 1
  top-3  accuracy: correct chunk appears in ranks 1-3
  top-5  accuracy: correct chunk appears in ranks 1-5
  MRR    (Mean Reciprocal Rank): average of 1/rank for first correct hit
         MRR of 1.0 = always rank 1, MRR of 0.33 = correct chunk at rank 3 on average
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from dataclasses import dataclass

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import AsyncSessionLocal
from app.rag.embedder import get_client, EMBEDDING_MODEL
from app.rag.index import load_index, search_index

logging.basicConfig(
    level=logging.WARNING,   # suppress INFO noise during eval
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

EVAL_SET_PATH = Path("data/eval_set.json")
K_VALUES      = [1, 3, 5]    # measure accuracy at these cutoffs


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EvalQuestion:
    id:                int
    question:          str
    ticker:            str | None
    section:           str
    gold_faiss_indices: list[int]


@dataclass
class EvalResult:
    question_id:   int
    question:      str
    ticker:        str | None
    gold_indices:  list[int]
    retrieved:     list[int]    # faiss indices returned, in rank order
    scores:        list[float]
    hit_at:        dict[int, bool]   # {1: True, 3: False, 5: True}
    reciprocal_rank: float           # 1/rank of first correct hit, 0 if miss


# ---------------------------------------------------------------------------
# Load eval set
# ---------------------------------------------------------------------------

def load_eval_set(path: Path) -> list[EvalQuestion]:
    data = json.loads(path.read_text())
    questions = []
    for item in data:
        questions.append(EvalQuestion(
            id=item["id"],
            question=item["question"],
            ticker=item.get("ticker"),
            section=item.get("section", ""),
            gold_faiss_indices=item["gold_faiss_indices"],
        ))
    return questions


# ---------------------------------------------------------------------------
# Embed query
# ---------------------------------------------------------------------------

def embed_query(query: str, client) -> np.ndarray:
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
    )
    vector = np.array(response.data[0].embedding, dtype=np.float32)
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
    return vector


# ---------------------------------------------------------------------------
# Evaluate one question
# ---------------------------------------------------------------------------

def evaluate_question(
    question: EvalQuestion,
    index,
    client,
    k: int = 5,
) -> EvalResult:
    """
    Embed the question, search FAISS, check if gold chunks appear in results.

    Note: we do NOT apply ticker filtering during eval.
    Reason: filtering would artificially inflate scores by reducing the
    search space. We want to measure raw retrieval quality.
    If AAPL chunks appear for NVDA questions, that's a real failure.
    """
    query_vector = embed_query(question.question, client)
    results      = search_index(index, query_vector, k=k)

    retrieved_indices = [r["faiss_index"] for r in results]
    retrieved_scores  = [r["score"]       for r in results]

    gold_set = set(question.gold_faiss_indices)

    # hit@k: did any gold chunk appear in top-k results?
    hit_at = {}
    for k_val in K_VALUES:
        top_k = set(retrieved_indices[:k_val])
        hit_at[k_val] = bool(top_k & gold_set)

    # MRR: find the rank of the first correct hit
    reciprocal_rank = 0.0
    for rank, idx in enumerate(retrieved_indices, start=1):
        if idx in gold_set:
            reciprocal_rank = 1.0 / rank
            break

    return EvalResult(
        question_id=question.id,
        question=question.question,
        ticker=question.ticker,
        gold_indices=question.gold_faiss_indices,
        retrieved=retrieved_indices,
        scores=retrieved_scores,
        hit_at=hit_at,
        reciprocal_rank=reciprocal_rank,
    )


# ---------------------------------------------------------------------------
# Compute aggregate metrics
# ---------------------------------------------------------------------------

def compute_metrics(results: list[EvalResult]) -> dict:
    n = len(results)
    if n == 0:
        return {}

    metrics = {}

    # Accuracy at each k
    for k_val in K_VALUES:
        hits = sum(1 for r in results if r.hit_at[k_val])
        metrics[f"top_{k_val}_accuracy"] = round(hits / n, 4)
        metrics[f"top_{k_val}_hits"]     = hits

    # MRR
    metrics["mrr"]   = round(sum(r.reciprocal_rank for r in results) / n, 4)
    metrics["total"] = n

    return metrics


# ---------------------------------------------------------------------------
# Print detailed results
# ---------------------------------------------------------------------------

def print_results(results: list[EvalResult], metrics: dict) -> None:

    print("\n" + "=" * 70)
    print("RETRIEVAL EVALUATION RESULTS")
    print("=" * 70)

    # Per-question breakdown
    print("\nPer-question results:")
    print(f"{'ID':<4} {'Ticker':<6} {'@1':<4} {'@3':<4} {'@5':<4} {'MRR':<6} Question")
    print("-" * 70)

    for r in sorted(results, key=lambda x: x.question_id):
        hit1 = "✅" if r.hit_at[1] else "❌"
        hit3 = "✅" if r.hit_at[3] else "❌"
        hit5 = "✅" if r.hit_at[5] else "❌"
        ticker = r.ticker or "ALL"
        question_preview = r.question[:45] + "..." if len(r.question) > 45 else r.question
        print(
            f"{r.question_id:<4} {ticker:<6} {hit1:<4} {hit3:<4} {hit5:<4} "
            f"{r.reciprocal_rank:.2f}   {question_preview}"
        )

    # Misses — most useful for debugging
    misses = [r for r in results if not r.hit_at[5]]
    if misses:
        print(f"\n--- Top-5 misses ({len(misses)} questions) ---")
        for r in misses:
            print(f"\n  Q{r.question_id}: {r.question}")
            print(f"  Gold indices:      {r.gold_indices}")
            print(f"  Retrieved indices: {r.retrieved}")
            print(f"  Retrieved scores:  {[round(s, 3) for s in r.scores]}")

    # Summary metrics
    print("\n" + "=" * 70)
    print("SUMMARY METRICS")
    print("=" * 70)
    print(f"  Total questions : {metrics['total']}")
    print(f"  Top-1  accuracy : {metrics['top_1_accuracy']:.1%}  ({metrics['top_1_hits']}/{metrics['total']} hits)")
    print(f"  Top-3  accuracy : {metrics['top_3_accuracy']:.1%}  ({metrics['top_3_hits']}/{metrics['total']} hits)")
    print(f"  Top-5  accuracy : {metrics['top_5_accuracy']:.1%}  ({metrics['top_5_hits']}/{metrics['total']} hits)")
    print(f"  MRR             : {metrics['mrr']:.4f}")
    print("=" * 70)

    # Resume bullet guidance
    print("\nResume bullet guidance:")
    top3 = metrics['top_3_accuracy']
    if top3 >= 0.78:
        print(f"  ✅ Top-3 accuracy {top3:.0%} — use this number directly")
    elif top3 >= 0.65:
        print(f"  ⚠️  Top-3 accuracy {top3:.0%} — iterate on chunk size or embedding model")
    else:
        print(f"  ❌ Top-3 accuracy {top3:.0%} — significant tuning needed")


# ---------------------------------------------------------------------------
# Save results to JSON for LangSmith logging (Phase 2 step 6)
# ---------------------------------------------------------------------------

def save_results(results: list[EvalResult], metrics: dict) -> None:
    output = {
        "metrics": metrics,
        "per_question": [
            {
                "id":               r.question_id,
                "question":         r.question,
                "ticker":           r.ticker,
                "gold_indices":     r.gold_indices,
                "retrieved":        r.retrieved,
                "scores":           [round(s, 4) for s in r.scores],
                "hit_at_1":         r.hit_at[1],
                "hit_at_3":         r.hit_at[3],
                "hit_at_5":         r.hit_at[5],
                "reciprocal_rank":  round(r.reciprocal_rank, 4),
            }
            for r in results
        ]
    }
    out_path = Path("data/eval_results.json")
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nDetailed results saved to {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not EVAL_SET_PATH.exists():
        print(f"Eval set not found at {EVAL_SET_PATH}")
        print("Complete Step 10 first — write your 50 questions with gold indices.")
        return

    print("Loading eval set...")
    questions = load_eval_set(EVAL_SET_PATH)
    print(f"  {len(questions)} questions loaded")

    print("Loading FAISS index...")
    index = load_index()
    print(f"  Index has {index.ntotal} vectors")

    print("Loading OpenAI client...")
    client = get_client()

    print(f"\nEvaluating {len(questions)} questions (k=5)...")
    print("This makes one API call per question — expect ~30 seconds for 50 questions\n")

    results = []
    for i, question in enumerate(questions, 1):
        print(f"  [{i:02d}/{len(questions)}] Q{question.id}: {question.question[:55]}...")
        result = evaluate_question(question, index, client, k=5)
        results.append(result)

    metrics = compute_metrics(results)
    print_results(results, metrics)
    save_results(results, metrics)


if __name__ == "__main__":
    main()