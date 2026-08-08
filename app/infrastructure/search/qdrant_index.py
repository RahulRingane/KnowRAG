"""Qdrant as a `SearchIndex`: embeds chunks and upserts them.

From the old `app/vdb.py`, minus the database session and the CLI. Per
KNowRAG_SPEC.md §4 / PLAN.md "A2 — Storage & Incremental Indexing": upserts
are keyed by a deterministic point ID derived from `(document_id,
chunk_index)`, so re-running indexing on the same chunks overwrites rather
than duplicates. The old drop/recreate behavior is preserved as an explicit
`full_reindex` opt-in.
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client.models import Distance, PointStruct, VectorParams

from app.core.config import settings
from app.core.observability import get_logger, observe, retrieval_latency_seconds
from app.domain.models import Chunk, StoredChunk, chunk_key
from app.infrastructure.ml.embeddings import get_embedding_model
from app.infrastructure.search.clients import get_qdrant_client

logger = get_logger(__name__)

EMBEDDING_DIM = 768

# Fixed namespace for deriving deterministic Qdrant point IDs. Qdrant point
# IDs must be an unsigned int or a UUID — a raw "doc:chunk" string is not
# accepted, so a stable UUIDv5 (deterministic given the same input, unlike
# uuid4) is derived from the canonical chunk_key() instead.
_POINT_ID_NAMESPACE = uuid.UUID("6f8f8e2a-8b2b-4f2a-9c3d-1a2b3c4d5e6f")


def point_id_for(document_id: int, chunk_index: int) -> str:
    """Deterministic Qdrant point ID for a given (document_id, chunk_index).

    Same input always produces the same UUID string (across processes and
    runs), so upserting is idempotent: re-indexing the same chunk overwrites
    the existing point instead of creating a duplicate.
    """
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, chunk_key(document_id, chunk_index)))


class QdrantIndex:
    """The Qdrant adapter — both halves of it.

    Satisfies `SearchIndex` (write) and `Retriever` (read). Keeping the two
    together is deliberate: they must agree on the collection name and on the
    payload keys, and splitting them across modules is what let the old
    `vdb.py`/`retriver.py` pair drift into holding two separate clients
    against the same collection.
    """

    name = "qdrant"

    def __init__(self, collection: str | None = None):
        self.collection = collection or settings.qdrant_collection

    # --- read (Retriever) ---------------------------------------------------

    def search(self, query: str, k: int = 0) -> list[Chunk]:
        """Dense vector search over the collection."""
        k = k or settings.top_k_retrieval

        with observe(retrieval_latency_seconds, path="semantic"):
            query_embedding = get_embedding_model().encode(
                query,
                normalize_embeddings=True,
            ).tolist()

            response = get_qdrant_client().query_points(
                collection_name=self.collection,
                query=query_embedding,
                limit=k,
            )

            docs = [
                Chunk(
                    source="qdrant",
                    score=float(hit.score),
                    document_id=hit.payload["document_id"],
                    chunk_index=hit.payload["chunk_index"],
                    text=hit.payload["text"],
                )
                for hit in response.points
            ]

        logger.info(
            "semantic_search",
            collection=self.collection,
            k=k,
            count=len(docs),
            chunk_ids=[chunk_key(d.document_id, d.chunk_index) for d in docs],
        )
        return docs

    # --- write (SearchIndex) ------------------------------------------------

    def ensure_collection(self, recreate: bool = False) -> None:
        qdrant = get_qdrant_client()
        exists = qdrant.collection_exists(self.collection)

        if exists and recreate:
            qdrant.delete_collection(self.collection)
            exists = False

        if not exists:
            qdrant.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=EMBEDDING_DIM,
                    distance=Distance.COSINE,
                ),
            )

    def index(self, chunks: list[StoredChunk], full_reindex: bool = False) -> int:
        """Embed `chunks` and upsert them; return the number of points written.

        Default (idempotent) mode: the collection is created if missing and
        points are upserted with a deterministic id, so running this twice on
        the same chunks does not grow the collection.

        `full_reindex=True` restores the old drop-and-recreate behavior,
        explicitly opt-in per §4.
        """
        if not chunks:
            logger.info("No chunks to index into Qdrant.")
            return 0

        self.ensure_collection(recreate=full_reindex)

        model = get_embedding_model()

        points = [
            PointStruct(
                id=point_id_for(chunk.document_id, chunk.chunk_index),
                vector=model.encode(chunk.text, normalize_embeddings=True).tolist(),
                payload={
                    "document_id": chunk.document_id,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                },
            )
            for chunk in chunks
        ]

        get_qdrant_client().upsert(
            collection_name=self.collection,
            points=points,
            wait=True,
        )

        logger.info("Upserted %d vectors into Qdrant collection %s.", len(points), self.collection)
        return len(points)
