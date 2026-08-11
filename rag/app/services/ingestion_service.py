"""The ingestion use case — chunk into Postgres, then index for search (§7.2).

Merges the old `app/ingest.py` (which sequenced the three steps) with the
orchestration half of `app/pg.py`'s `ingest_pdf` (which owned idempotency
and the document row). They were split across two modules for historical
reasons and the split cost something real: `ingest_pdf` decided whether to
short-circuit, but only `ingest_and_index` knew whether the document was
actually searchable afterwards, so neither function could answer "is this
document ready?" on its own.

§7.2 requires that "no route reimplement logic already in the storage
modules" and that each CLI entrypoint call *the same* functions the routes
call, "so there's exactly one code path for ingestion and one for
retrieval, exercised by both the CLI and the API." This class is that one
path; `app.api.routes.ingest` and `app.cli.ingest` both hold one and do
nothing else.

The `indexed` transition lives here and nowhere else, deliberately. Postgres
holding the chunks does not mean the document is searchable; it is only
"indexed" once every configured `SearchIndex` has been written, which is
exactly the boundary this service owns.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from app.core.exceptions import DocumentNotFound
from app.domain.models import DocumentRecord, DocumentStatus, IngestionResult
from app.domain.ports import ChunkRepository, DocumentLoader, DocumentRepository, SearchIndex

logger = logging.getLogger(__name__)


class IngestionService:
    """Puts a document into the corpus and reports on it."""

    def __init__(
        self,
        documents: DocumentRepository,
        chunks: ChunkRepository,
        loader: DocumentLoader,
        indexes: Sequence[SearchIndex],
    ):
        self._documents = documents
        self._chunks = chunks
        self._loader = loader
        self._indexes = list(indexes)

    # --- reads --------------------------------------------------------------

    def reserve(self, filename: str) -> int:
        """Allocate the document id `POST /ingest` returns before doing any work."""
        return self._documents.reserve(filename)

    def get_status(self, document_id: int) -> DocumentRecord:
        """Report `pending` | `indexed` | `failed` for a previously accepted upload."""
        record = self._documents.get(document_id)
        if record is None:
            raise DocumentNotFound(document_id)
        return record

    def list_documents(self) -> list[DocumentRecord]:
        """Enumerate every ingested document, newest first — `GET /documents`.

        Same concern as `get_status()`, many rows instead of one: both are
        read paths over the same tracking table, so this lives beside it
        rather than in a service of its own.
        """
        return self._documents.list_all()

    # --- the pipeline -------------------------------------------------------

    def ingest(
        self,
        path: str,
        document_id: int | None = None,
        force: bool = False,
        full_reindex: bool = False,
    ) -> IngestionResult:
        """Chunk `path` into Postgres, then write it to every search index.

        Idempotent by construction (§4): an unchanged `content_hash`
        short-circuits the chunking, and every index upserts on deterministic
        ids. Re-running this on an unchanged file therefore re-writes the same
        points/documents rather than duplicating them — and still ends at
        `status="indexed"`, which matters when Postgres is warm but Qdrant/ES
        were wiped or rebuilt underneath it.

        On failure the document is recorded as `failed` (with the exception
        text) *before* the exception is re-raised, so a caller polling
        `GET /ingest/{document_id}` learns what happened rather than watching a
        row sit at `pending` forever.
        """
        filename = Path(path).name
        content_hash = self._loader.content_hash(path)

        existing = self._documents.find_by_filename(filename)
        unchanged = existing is not None and existing.content_hash == content_hash and not force

        if unchanged:
            logger.info(
                "Skipping chunking of %r: content unchanged (content_hash=%s...); document_id=%s.",
                filename,
                content_hash[:12],
                existing.document_id,
            )
            doc_id = existing.document_id
            chunk_count = existing.chunk_count
            pg_status = "skipped"
        else:
            texts = self._loader.chunk(path)
            doc_id = self._documents.record_ingestion(
                filename=filename,
                content_hash=content_hash,
                chunk_count=len(texts),
                document_id=document_id,
            )
            chunk_count = self._chunks.replace_for_document(doc_id, texts)
            pg_status = "ingested"
            logger.info("Inserted %d chunks for document_id=%s (%r).", chunk_count, doc_id, filename)

        # Indexing runs even on the short-circuit path: "Postgres already has
        # this" says nothing about whether the search indexes do, and the
        # upserts are idempotent so re-running them is free of consequence.
        counts: dict[str, int] = {}
        try:
            stored = self._chunks.list_for_document(doc_id)
            for index in self._indexes:
                counts[index.name] = index.index(stored, full_reindex=full_reindex)
        except Exception as exc:
            logger.exception("Indexing failed for document_id=%s (%s)", doc_id, path)
            self._documents.set_status(doc_id, DocumentStatus.FAILED, error=str(exc))
            raise

        self._documents.set_status(doc_id, DocumentStatus.INDEXED)

        logger.info(
            "Ingested document_id=%s (%s): %s chunks, indexes=%s",
            doc_id,
            path,
            chunk_count,
            counts,
        )

        return IngestionResult(
            document_id=doc_id,
            status=DocumentStatus.INDEXED,
            pg_status=pg_status,
            chunk_count=chunk_count,
            content_hash=content_hash,
            vector_count=counts.get("qdrant", 0),
            keyword_count=counts.get("elasticsearch", 0),
        )


def build_default_ingestion_service() -> IngestionService:
    """The production object graph, in one place. See `QueryService`'s twin."""
    from app.infrastructure.db.chunk_repository import SqlChunkRepository
    from app.infrastructure.db.document_repository import SqlDocumentRepository
    from app.infrastructure.pdf.reader import PdfDocumentLoader
    from app.infrastructure.search.elasticsearch_index import ElasticsearchIndex
    from app.infrastructure.search.qdrant_index import QdrantIndex

    return IngestionService(
        documents=SqlDocumentRepository(),
        chunks=SqlChunkRepository(),
        loader=PdfDocumentLoader(),
        indexes=[QdrantIndex(), ElasticsearchIndex()],
    )
