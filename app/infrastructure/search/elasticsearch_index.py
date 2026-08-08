"""Elasticsearch as a `SearchIndex`: indexes chunks for BM25 keyword search.

From the old `app/es.py`, minus the database session and the CLI. Per
KNowRAG_SPEC.md §4: `index()` calls are keyed on the canonical `chunk_key()`
as the document `_id`, so re-running indexing overwrites rather than
duplicates. The old drop/recreate behavior is preserved as an explicit
`full_reindex` opt-in.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.core.observability import get_logger, observe, retrieval_latency_seconds
from app.domain.models import Chunk, StoredChunk, chunk_key
from app.infrastructure.search.clients import get_es_client

logger = get_logger(__name__)


class ElasticsearchIndex:
    """The Elasticsearch adapter — both halves of it.

    Satisfies `SearchIndex` (write) and `Retriever` (read), for the same
    reason `QdrantIndex` does: the two halves have to agree on the index name
    and the field names, so they belong in one class.
    """

    name = "elasticsearch"

    def __init__(self, index_name: str | None = None):
        self.index_name = index_name or settings.es_index

    # --- read (Retriever) ---------------------------------------------------

    def search(self, query: str, k: int = 0) -> list[Chunk]:
        """BM25 keyword search over the index."""
        k = k or settings.top_k_retrieval

        with observe(retrieval_latency_seconds, path="keyword"):
            response = get_es_client().search(
                index=self.index_name,
                size=k,
                query={"match": {"text": query}},
            )

            docs = [
                Chunk(
                    source="elastic",
                    score=float(hit["_score"]),
                    document_id=hit["_source"]["document_id"],
                    chunk_index=hit["_source"]["chunk_index"],
                    text=hit["_source"]["text"],
                )
                for hit in response["hits"]["hits"]
            ]

        logger.info(
            "keyword_search",
            index=self.index_name,
            k=k,
            count=len(docs),
            chunk_ids=[chunk_key(d.document_id, d.chunk_index) for d in docs],
        )
        return docs

    # --- write (SearchIndex) ------------------------------------------------

    def create_index(self) -> None:
        get_es_client().indices.create(
            index=self.index_name,
            mappings={
                "properties": {
                    "document_id": {"type": "integer"},
                    "chunk_index": {"type": "integer"},
                    "text": {"type": "text"},
                }
            },
        )

    def ensure_index(self, recreate: bool = False) -> None:
        es = get_es_client()
        exists = es.indices.exists(index=self.index_name)

        if exists and recreate:
            es.indices.delete(index=self.index_name)
            exists = False

        if not exists:
            self.create_index()

    def index(self, chunks: list[StoredChunk], full_reindex: bool = False) -> int:
        """Index `chunks` for BM25 search; return the number written."""
        if not chunks:
            logger.info("No chunks to index into Elasticsearch.")
            return 0

        self.ensure_index(recreate=full_reindex)

        es = get_es_client()
        for chunk in chunks:
            es.index(
                index=self.index_name,
                id=chunk_key(chunk.document_id, chunk.chunk_index),
                document={
                    "document_id": chunk.document_id,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                },
            )

        logger.info("Indexed %d chunks into Elasticsearch index %s.", len(chunks), self.index_name)
        return len(chunks)
