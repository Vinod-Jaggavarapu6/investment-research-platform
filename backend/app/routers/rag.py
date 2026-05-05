from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.filings_agent import answer_filing_question
from app.database import get_db
from app.models import FilingsRequest
from app.tools.retrieval import retrieve_chunks, format_retrieval_response

router = APIRouter(tags=["RAG"])


@router.get(
    "/retrieve",
    summary="Retrieve relevant SEC filing chunks",
    description=(
        "Embeds the query, searches the FAISS index, and returns the "
        "most relevant 10-K chunks from PostgreSQL. "
        "Optionally filter by ticker."
    ),
)
async def retrieve(
    query:  str           = Query(..., description="Question to search for"),
    ticker: Optional[str] = Query(None, description="Filter by ticker e.g. AAPL"),
    k:      int           = Query(5, description="Number of results", ge=1, le=20),
    db:     AsyncSession  = Depends(get_db),
):
    chunks = await retrieve_chunks(query=query, db=db, ticker=ticker, k=k)
    return format_retrieval_response(chunks)


@router.post(
    "/filings/ask",
    summary="Ask a question about SEC filings",
    description=(
        "Retrieves relevant 10-K chunks and generates a grounded "
        "answer with citations. Optionally filter by ticker."
    ),
)
async def ask_filings(
    request: FilingsRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await answer_filing_question(
        question=request.question,
        db=db,
        ticker=request.ticker,
        k=request.k,
    )
    return {
        "question": result.question,
        "ticker":   result.ticker,
        "answer":   result.answer,
        "model":    result.model,
        "sources": [
            {
                "rank":    i + 1,
                "ticker":  s["ticker"],
                "year":    s["year"],
                "section": s["section"],
                "score":   s["score"],
            }
            for i, s in enumerate(result.sources)
        ],
    }
