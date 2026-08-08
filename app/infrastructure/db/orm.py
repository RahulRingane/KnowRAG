"""The SQLAlchemy table definitions, and nothing else.

Named `ChunkRow`/`DocumentRow` rather than `Chunk`/`Document` on purpose.
`Chunk` is a domain type (`app.domain.models.Chunk`) meaning "a retrieved
passage"; the old code had a second, unrelated `Chunk` here meaning "a row in
the chunks table", so `from ... import Chunk` resolved to different classes
depending on which module you were reading. The `Row` suffix marks these as
what they are: a storage shape that never leaves this package.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.domain.models import DocumentStatus
from app.infrastructure.db.session import Base


class ChunkRow(Base):
    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)


class DocumentRow(Base):
    """Per §4: tracks one row per ingested file so re-ingesting an
    unchanged file (same `content_hash`) can short-circuit instead of
    re-chunking and re-inserting.

    `status`/`error` are additions beyond §4's stated column list, required
    by §7's `GET /ingest/{document_id}` route: background ingestion returns
    `202 Accepted` before any work has happened (§7.1), so the caller polls
    this row to find out how it went. Without a persisted status there is
    nothing for that route to report. `error` is nullable and only set
    alongside `status="failed"` — a failed ingest that records no reason
    forces the operator into the container logs to learn anything at all.
    """

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String, nullable=False, unique=True)
    ingested_at = Column(DateTime(timezone=True), nullable=False)
    chunk_count = Column(Integer, nullable=False)
    content_hash = Column(String, nullable=False)
    status = Column(String, nullable=False, default=DocumentStatus.PENDING)
    error = Column(Text, nullable=True)
