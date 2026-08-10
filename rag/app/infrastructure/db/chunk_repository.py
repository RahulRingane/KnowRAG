"""`ChunkRepository` over the `chunks` table.

This is what lets the two search indexes stop being database clients. Both
`app/es.py` and `app/vdb.py` used to open their own session and run
`db.query(Chunk).filter(...)` before indexing — so "index this document"
meant "know how chunks are stored", and adding a third index would have
meant a third copy of the same query.

They now receive `list[StoredChunk]` and know nothing about Postgres.
"""

from __future__ import annotations

from sqlalchemy.orm import sessionmaker

from app.domain.models import StoredChunk
from app.infrastructure.db.orm import ChunkRow
from app.infrastructure.db.session import SessionLocal


class SqlChunkRepository:
    """Postgres-backed `app.domain.ports.ChunkRepository`."""

    def __init__(self, session_factory: sessionmaker = SessionLocal):
        self._session_factory = session_factory

    def replace_for_document(self, document_id: int, texts: list[str]) -> int:
        """Atomically swap this document's chunks for `texts`.

        Delete-then-insert inside one transaction, so a reader never observes
        a document with half its chunks — and a failure part-way leaves the
        previous chunk set intact rather than a truncated one.
        """
        db = self._session_factory()
        try:
            db.query(ChunkRow).filter(ChunkRow.document_id == document_id).delete()

            for index, text in enumerate(texts):
                db.add(
                    ChunkRow(
                        document_id=document_id,
                        chunk_index=index,
                        chunk_text=text,
                    )
                )

            db.commit()
            return len(texts)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_for_document(self, document_id: int | None = None) -> list[StoredChunk]:
        """All chunks for one document, or every chunk when `document_id` is None."""
        db = self._session_factory()
        try:
            query = db.query(ChunkRow)
            if document_id is not None:
                query = query.filter(ChunkRow.document_id == document_id)

            return [
                StoredChunk(
                    document_id=row.document_id,
                    chunk_index=row.chunk_index,
                    text=row.chunk_text,
                )
                for row in query.all()
            ]
        finally:
            db.close()
