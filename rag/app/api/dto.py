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
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.models import DocumentRecord, User


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


class ChunkText(BaseModel):
    """The wire shape of `GET /chunks` — a resolved citation.

    A thin DTO copy of `app.domain.models.StoredChunk` rather than reusing it
    directly, unlike `FactCheckedResponse`'s exception: `StoredChunk` means
    "a chunk on its way to being indexed" and happens to share this field set
    today, but the two types answer different questions and would be the
    wrong thing to conflate the moment either one needs a field the other
    should not carry — e.g. this DTO gaining a `score` if evidence ranking is
    ever exposed here, which would have no meaning for a chunk mid-ingestion.
    """

    document_id: int
    chunk_index: int
    text: str


# --- Auth (WS-1, frontend_plan.md §3) ---------------------------------------

# Registration-only password floor (frontend_plan.md open item #2). Not
# configurable via settings: it's a wire-validation rule, not infrastructure
# policy, and app.api may not read the environment anyway (core/config.py is
# the only module that can) — see tests/test_architecture.py.
MIN_PASSWORD_LENGTH = 8


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1)
    # Policy lives here, not at login: registration is the only place a new
    # password is chosen, so it is the only place a minimum can be enforced
    # without side effects. See LoginRequest below for why login stays at 1.
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        description=f"Minimum {MIN_PASSWORD_LENGTH} characters.",
    )


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    # Deliberately NOT MIN_PASSWORD_LENGTH: enforcing today's policy here
    # would reject a pre-existing shorter password with a 422 instead of a
    # clean 401, and it leaks the policy to an unauthenticated endpoint.
    # Do not "fix" this to match RegisterRequest.
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    """`POST /auth/login` and `POST /auth/refresh`'s shared response shape.

    The refresh token itself never appears here — it only ever travels as
    the `knowrag_refresh` httpOnly cookie (§3.2's whole point is that the
    frontend never holds it in readable storage), so this DTO carries only
    what a caller is meant to keep in memory.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    """The wire shape of `GET /auth/me` and `POST /auth/register`.

    Field-by-field from `User`, same reasoning as `IngestStatus.from_record()`:
    `User` also carries `password_hash` and `token_version`, neither of which
    has any business leaving this process just because a route serializes
    the object that happens to hold them.
    """

    id: int
    username: str
    created_at: datetime

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(id=user.id, username=user.username, created_at=user.created_at)
