"""`DocumentRepository` over the `documents` table.

Assembled from the loose functions the old `app/pg.py` exposed
(`reserve_document`, `set_document_status`, `get_document`, and the document
half of `ingest_pdf`). Gathering them behind the port does three things the
loose functions could not:

- routes stop importing a storage module to reserve an id (`app/main.py`
  used to call `pg.reserve_document` directly),
- the ingestion service's idempotency rule becomes testable against an
  in-memory fake instead of a live Postgres, and
- no ORM instance escapes: every method returns `DocumentRecord`, so a
  caller cannot accidentally touch a detached row after its session closed.

Each method owns its own short session. That is the right granularity here —
the operations are single-statement and independent, and a longer-lived
session would have to be threaded through the service layer, which would put
SQLAlchemy back in the layer this refactor removed it from.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from app.domain.models import DocumentRecord, DocumentStatus
from app.infrastructure.db.orm import DocumentRow
from app.infrastructure.db.session import SessionLocal


def _to_record(row: DocumentRow) -> DocumentRecord:
    return DocumentRecord(
        document_id=row.id,
        filename=row.filename,
        status=row.status,
        chunk_count=row.chunk_count,
        content_hash=row.content_hash,
        ingested_at=row.ingested_at,
        error=row.error,
    )


class SqlDocumentRepository:
    """Postgres-backed `app.domain.ports.DocumentRepository`."""

    def __init__(self, session_factory: sessionmaker = SessionLocal):
        self._session_factory = session_factory

    def get(self, document_id: int) -> DocumentRecord | None:
        db = self._session_factory()
        try:
            row = db.query(DocumentRow).filter(DocumentRow.id == document_id).first()
            return _to_record(row) if row is not None else None
        finally:
            db.close()

    def find_by_filename(self, filename: str) -> DocumentRecord | None:
        db = self._session_factory()
        try:
            row = db.query(DocumentRow).filter(DocumentRow.filename == filename).first()
            return _to_record(row) if row is not None else None
        finally:
            db.close()

    def reserve(self, filename: str) -> int:
        """Allocate (or reuse) the row for `filename`, mark it pending, return its id.

        Called synchronously by `POST /ingest` *before* the background task is
        scheduled, so the id handed back in the 202 response is already durable
        and immediately pollable via `GET /ingest/{document_id}`.

        A pre-existing row keeps its `content_hash`: that is what lets a
        re-upload of a byte-identical file still short-circuit during ingestion
        rather than re-chunking. A brand-new row is created with an empty hash,
        which cannot match any real SHA-256, so first-time ingestion always
        proceeds.
        """
        db = self._session_factory()
        try:
            existing = db.query(DocumentRow).filter(DocumentRow.filename == filename).first()

            if existing is not None:
                existing.status = DocumentStatus.PENDING
                existing.error = None
                doc_id = existing.id
            else:
                row = DocumentRow(
                    filename=filename,
                    ingested_at=datetime.now(timezone.utc),
                    chunk_count=0,
                    content_hash="",  # sentinel: never equal to a real hash
                    status=DocumentStatus.PENDING,
                )
                db.add(row)
                db.flush()
                doc_id = row.id

            db.commit()
            return doc_id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def set_status(self, document_id: int, status: str, error: str | None = None) -> None:
        """Record the terminal (or in-progress) state of a background ingest."""
        db = self._session_factory()
        try:
            row = db.query(DocumentRow).filter(DocumentRow.id == document_id).first()
            if row is None:
                return
            row.status = status
            row.error = error
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record_ingestion(
        self,
        filename: str,
        content_hash: str,
        chunk_count: int,
        document_id: int | None = None,
    ) -> int:
        """Create or update the tracking row for a completed chunking pass.

        Status goes to `pending`, never `indexed`: Postgres holding the chunks
        does not make the document searchable. Only `IngestionService`, after
        both indexes have been written, is entitled to call it indexed.
        """
        db = self._session_factory()
        try:
            existing = db.query(DocumentRow).filter(DocumentRow.filename == filename).first()
            now = datetime.now(timezone.utc)

            if existing is not None:
                # Reuse the existing document's id regardless of an explicit
                # `document_id` — a filename identifies one logical document,
                # and reassigning its primary key out from under existing
                # chunk rows would orphan them.
                doc_id = existing.id
                existing.content_hash = content_hash
                existing.chunk_count = chunk_count
                existing.ingested_at = now
                # Postgres chunks are about to change, which makes whatever is
                # currently in Qdrant/ES stale.
                existing.status = DocumentStatus.PENDING
                existing.error = None
            else:
                row = DocumentRow(
                    filename=filename,
                    ingested_at=now,
                    chunk_count=chunk_count,
                    content_hash=content_hash,
                    status=DocumentStatus.PENDING,
                )
                if document_id is not None:
                    row.id = document_id
                db.add(row)
                db.flush()  # populate row.id when autoincrementing
                doc_id = row.id

            db.commit()
            return doc_id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_all(self) -> list[DocumentRecord]:
        """Every ingested document, newest first — the read side of `GET /documents`.

        Ordered by `ingested_at` descending so a dashboard's default view is
        "what changed most recently" without the caller having to sort. This
        is a full-table `SELECT`, which is fine at this corpus's scale — the
        cap-and-single-query concern that shaped `ChunkRepository.get_by_keys()`
        does not apply here because there is no caller-supplied id list to
        bound; a document count large enough for that to matter would call
        for pagination, which is out of scope for this pass.
        """
        db = self._session_factory()
        try:
            rows = db.query(DocumentRow).order_by(DocumentRow.ingested_at.desc()).all()
            return [_to_record(row) for row in rows]
        finally:
            db.close()
