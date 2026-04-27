"""
langsmith_eval.py — Run retrieval evals against the LangSmith dataset

Run:
  uv run python scripts/langsmith_eval.py

Each run creates a new experiment in LangSmith under the dataset
"investment-research-retrieval". Compare experiments side-by-side in the UI
to track how chunking, embedding, or retrieval changes affect quality.

Metrics per question:
  hit@1         — correct chunk is rank 1
  hit@3         — correct chunk is in top 3
  hit@5         — correct chunk is in top 5
  mrr           — 1/rank of first correct chunk
  ticker_prec@5 — fraction of top-5 from the right ticker (ticker questions only)
  section_prec@5— fraction of top-5 from the right section
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from langsmith import aevaluate
from langsmith.evaluation import EvaluationResult

from app.database import AsyncSessionLocal
from app.tools.retrieval import retrieve_chunks

DATASET_NAME = "investment-research-retrieval"
K            = 5

SECTION_ALIASES: dict[str, set[str]] = {
    "Item 1A": {"Item 1A", "Risk Factors"},
    "Item 7":  {"Item 7", "MD&A", "Results of Operations"},
    "Item 7A": {"Item 7A", "Market Risk"},
    "Item 8":  {"Item 8", "Financial Statements"},
}


# ── Target ─────────────────────────────────────────────────────────────────
# LangSmith calls this once per example. Returns a dict that evaluators read.

async def target(inputs: dict) -> dict:
    question = inputs["question"]
    async with AsyncSessionLocal() as db:
        chunks = await retrieve_chunks(
            query=question,
            db=db,
            ticker=None,   # no filter — measures raw embedding quality
            k=K,
        )
    return {"chunks": chunks}


# ── Helpers ────────────────────────────────────────────────────────────────

def _is_hit(
    chunk: dict,
    gold_ticker: str | None,
    gold_section: str,
    gold_filing_type: str | None = None,
) -> bool:
    valid_sections = SECTION_ALIASES.get(gold_section, {gold_section})
    section_ok      = chunk["section"] in valid_sections
    filing_type_ok  = gold_filing_type is None or chunk.get("filing_type") == gold_filing_type
    if gold_ticker is None:
        return section_ok and filing_type_ok
    return section_ok and filing_type_ok and chunk["ticker"] == gold_ticker


def _chunks_and_gold(run, example) -> tuple[list[dict], str | None, str, str | None]:
    chunks           = (run.outputs or {}).get("chunks", [])
    gold_ticker      = example.outputs.get("ticker")
    gold_section     = example.outputs.get("section", "")
    gold_filing_type = example.outputs.get("filing_type")
    return chunks, gold_ticker, gold_section, gold_filing_type


# ── Evaluators ─────────────────────────────────────────────────────────────

def eval_hit_at_1(run, example) -> EvaluationResult:
    chunks, gt, gs, gft = _chunks_and_gold(run, example)
    hit = bool(chunks) and _is_hit(chunks[0], gt, gs, gft)
    return EvaluationResult(key="hit@1", score=int(hit))


def eval_hit_at_3(run, example) -> EvaluationResult:
    chunks, gt, gs, gft = _chunks_and_gold(run, example)
    hit = any(_is_hit(c, gt, gs, gft) for c in chunks[:3])
    return EvaluationResult(key="hit@3", score=int(hit))


def eval_hit_at_5(run, example) -> EvaluationResult:
    chunks, gt, gs, gft = _chunks_and_gold(run, example)
    hit = any(_is_hit(c, gt, gs, gft) for c in chunks[:5])
    return EvaluationResult(key="hit@5", score=int(hit))


def eval_mrr(run, example) -> EvaluationResult:
    chunks, gt, gs, gft = _chunks_and_gold(run, example)
    for rank, chunk in enumerate(chunks, start=1):
        if _is_hit(chunk, gt, gs, gft):
            return EvaluationResult(key="mrr", score=round(1.0 / rank, 4))
    return EvaluationResult(key="mrr", score=0.0)


def eval_section_prec_at_5(run, example) -> EvaluationResult:
    chunks, _, gs, _ = _chunks_and_gold(run, example)
    top = chunks[:5]
    if not top:
        return EvaluationResult(key="section_prec@5", score=0.0)
    valid = SECTION_ALIASES.get(gs, {gs})
    prec  = sum(1 for c in top if c["section"] in valid) / len(top)
    return EvaluationResult(key="section_prec@5", score=round(prec, 4))


def eval_ticker_prec_at_5(run, example) -> EvaluationResult:
    chunks, gt, _, _ = _chunks_and_gold(run, example)
    if gt is None:
        return EvaluationResult(key="ticker_prec@5", score=None)
    top  = chunks[:5]
    if not top:
        return EvaluationResult(key="ticker_prec@5", score=0.0)
    prec = sum(1 for c in top if c["ticker"] == gt) / len(top)
    return EvaluationResult(key="ticker_prec@5", score=round(prec, 4))


def eval_filing_type_prec_at_5(run, example) -> EvaluationResult:
    chunks, _, _, gft = _chunks_and_gold(run, example)
    if gft is None:
        return EvaluationResult(key="filing_type_prec@5", score=None)
    top  = chunks[:5]
    if not top:
        return EvaluationResult(key="filing_type_prec@5", score=0.0)
    prec = sum(1 for c in top if c.get("filing_type") == gft) / len(top)
    return EvaluationResult(key="filing_type_prec@5", score=round(prec, 4))


def eval_top1_cosine(run, example) -> EvaluationResult:
    chunks = (run.outputs or {}).get("chunks", [])
    score  = chunks[0]["score"] if chunks else 0.0
    return EvaluationResult(key="top1_cosine_score", score=round(score, 4))


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    print(f"Running retrieval eval against dataset '{DATASET_NAME}' (k={K})...")
    print("Each question makes one embedding API call.\n")

    results = await aevaluate(
        target,
        data=DATASET_NAME,
        evaluators=[
            eval_hit_at_1,
            eval_hit_at_3,
            eval_hit_at_5,
            eval_mrr,
            eval_section_prec_at_5,
            eval_ticker_prec_at_5,
            eval_filing_type_prec_at_5,
            eval_top1_cosine,
        ],
        experiment_prefix="retrieval",
        max_concurrency=3,   # stay within OpenAI embedding rate limits
    )

    # Print aggregate summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    async for result in results:
        pass   # aevaluate streams results; iterate to completion

    print("\nFull results and per-question drill-down:")
    print(f"  https://smith.langchain.com → Datasets → {DATASET_NAME} → Experiments")


if __name__ == "__main__":
    asyncio.run(main())
