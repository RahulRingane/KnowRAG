"""`python -m app.cli.retrieve` — interactive hybrid search, for eyeballing retrieval."""

from __future__ import annotations

from app.core.config import settings
from app.domain.models import chunk_key
from app.infrastructure.search.hybrid_retriever import build_default_retriever


def main() -> None:
    question = input("Question: ")

    results = build_default_retriever().search(question, k=settings.top_k_rerank)

    print()

    for i, doc in enumerate(results, start=1):
        print(f"Result {i}")
        print(f"Chunk Key       : {chunk_key(doc.document_id, doc.chunk_index)}")
        print(f"Retriever Score : {doc.score:.4f}")
        print(f"Rerank Score    : {doc.rerank_score:.4f}")
        print(f"Source          : {doc.source}")
        print("-" * 80)
        print(doc.text)
        print("-" * 80)
        print()


if __name__ == "__main__":
    main()
