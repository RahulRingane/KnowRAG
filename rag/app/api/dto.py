"""Request and response shapes that belong to HTTP and nowhere else.

Separate from `app.domain.models` on purpose. These types exist because the
API has a wire format — a multipart upload becomes a `202` body, a question
arrives as a JSON object with a validated non-empty string. None of that is
domain vocabulary, and putting it in the domain would mean a CLI caller
imports `QueryRequest` to ask a question.

The one deliberate exception is `FactCheckedResponse`, which the query routes
return directly: §6.4 defines it as "the contract the FastAPI layer
serializes", so it is a domain type that happens to *be* the wire format.
Re-declaring a DTO copy of it would create two shapes that must agree, and
the failure mode is an audit trail that silently omits a field.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models import DocumentRecord


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


class IngestAccepted(BaseModel):
    document_id: int
    filename: str
    status: str


class IngestStatus(BaseModel):
    document_id: int
    filename: str
    status: str
    chunk_count: int
    ingested_at: datetime | None = None
    error: str | None = None

    @classmethod
    def from_record(cls, record: DocumentRecord) -> "IngestStatus":
        """Project a `DocumentRecord` onto the wire shape.

        Explicitly field-by-field rather than `**record.model_dump()`:
        `DocumentRecord` also carries `content_hash`, which is internal
        bookkeeping and has no business appearing in an API response just
        because someone added a column.
        """
        return cls(
            document_id=record.document_id,
            filename=record.filename,
            status=record.status,
            chunk_count=record.chunk_count,
            ingested_at=record.ingested_at,
            error=record.error,
        )
