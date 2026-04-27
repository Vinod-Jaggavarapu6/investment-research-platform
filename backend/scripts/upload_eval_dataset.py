"""
upload_eval_dataset.py — Push eval_set.json to LangSmith as a Dataset

Run once (or after adding new questions):
  uv run python scripts/upload_eval_dataset.py

Creates a dataset named "investment-research-retrieval" in your LangSmith
project with one example per question. Safe to re-run — deletes and recreates
the dataset so it stays in sync with the local file.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from langsmith import Client

DATASET_NAME = "investment-research-retrieval"
EVAL_SET_PATH = Path("data/eval_set.json")


def main():
    if not EVAL_SET_PATH.exists():
        print(f"ERROR: {EVAL_SET_PATH} not found. Run from backend/ directory.")
        sys.exit(1)

    questions = json.loads(EVAL_SET_PATH.read_text())
    print(f"Loaded {len(questions)} questions from {EVAL_SET_PATH}")

    client = Client()

    # Delete existing dataset so we stay in sync with local file
    existing = [d for d in client.list_datasets() if d.name == DATASET_NAME]
    if existing:
        client.delete_dataset(dataset_id=existing[0].id)
        print(f"Deleted existing dataset '{DATASET_NAME}'")

    dataset = client.create_dataset(
        DATASET_NAME,
        description=(
            "Gold-label retrieval eval: 50 financial questions with expected "
            "(ticker, section) pairs covering 20 tickers across 10-K sections."
        ),
    )
    print(f"Created dataset '{DATASET_NAME}' (id={dataset.id})")

    client.create_examples(
        inputs=[
            {
                "question": q["question"],
                "id":       q["id"],
            }
            for q in questions
        ],
        outputs=[
            {
                "ticker":      q.get("ticker"),       # None for cross-ticker questions
                "section":     q["section"],
                "filing_type": q.get("filing_type"),  # None means any type acceptable
            }
            for q in questions
        ],
        dataset_id=dataset.id,
    )

    print(f"Uploaded {len(questions)} examples")
    print(f"\nView at: https://smith.langchain.com → Datasets → {DATASET_NAME}")


if __name__ == "__main__":
    main()
