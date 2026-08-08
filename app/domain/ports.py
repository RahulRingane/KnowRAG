"""The interfaces the domain and service layers depend on.

This module is what makes the layering real rather than decorative. Without
it, "the service layer orchestrates retrieval" means `query_service.py`
imports `qdrant_client`, and the arrow points from the inside of the
application out to a vendor SDK. With it, the service depends on `Retriever`
— declared here, in the domain — and infrastructure supplies something that
satisfies it. The arrow points inward from every layer.

`typing.Protocol` rather than ABCs on purpose. Nothing has to inherit from
these, so:

- a test passes a plain lambda or a small stub class and it type-checks,
- `app.infrastructure` never imports `app.domain.ports` at all unless it
  wants the annotation, so the dependency stays one-directional even in the
  import graph, and
- adding a second implementation of a port costs nothing at the definition
  site.

The scorer and generator ports are declared as *callable* protocols
(`__call__`) rather than named methods. That is not stylistic: the things
that satisfy them are single-operation functions, and a callable protocol
means a test substitutes `lambda premise, hypothesis: ("entailment", 0.99)`
directly instead of wrapping it in a class that exists only to hold one
method.
"""

from __future__ import annotations

from typing import Iterator, Literal, Protocol, runtime_checkable

from app.domain.models import (
    Chunk,
    Claim,
    DocumentRecord,
    IngestionResult,
    StoredChunk,
)

# The three NLI classes, and the sentinel `_score_citations` records when a
# claim cites a tag that resolves to no chunk.
NLILabel = Literal["contradiction", "entailment", "neutral"]


# --- Retrieval ---------------------------------------------------------------


@runtime_checkable
class Retriever(Protocol):
    """Turns a question into the evidence chunks that may answer it.

    Deliberately one method. The service layer has no business knowing that
    the implementation behind this runs a vector search, a BM25 search, and
    a cross-encoder rerank — swapping that for a single dense search, or for
    a fixture list in a test, must not require touching the caller.
    """

    def search(self, query: str, k: int) -> list[Chunk]:
        """Return at most `k` chunks, best first."""
        ...


class Reranker(Protocol):
    """Rescores `(query, passage)` pairs by relevance.

    Exactly `sentence_transformers.CrossEncoder`'s shape, so the real model
    satisfies it with no wrapper — and so does a list-returning stub.
    """

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]: ...


# --- Generation --------------------------------------------------------------


class ClaimGenerator(Protocol):
    """Turns (question, formatted context) into structured claims.

    Returns validated `Claim` objects rather than the provider's raw dict, so
    no caller ever parses a provider payload. Implementations raise
    `app.core.exceptions.GenerationUnavailable` — never an SDK-specific
    error type — which is what keeps a vendor SDK from leaking upward
    through an `except` clause.
    """

    def generate(
        self, question: str, context_block: str, chunk_ids: list[str] | None = None
    ) -> list[Claim]:
        """Generate the full claim set in one call."""
        ...

    def stream(self, question: str, context_block: str) -> Iterator[str]:
        """Yield raw text deltas of the same structured output.

        Separate from `generate` because the two have genuinely different
        semantics, not just different plumbing: the streaming path has no
        retry (a retry cannot un-send bytes already on the client's socket)
        and no cache (a cache hit has nothing to stream).
        """
        ...


# --- Verification ------------------------------------------------------------


class EntailmentScorer(Protocol):
    """Scores whether a premise entails a hypothesis.

    The single piece of infrastructure claim verification needs. Declaring it
    here is what lets `ClaimVerifier` — which holds all of §6.2's decision
    logic — live in the domain layer with no torch import anywhere near it,
    and lets the entire verification test suite run offline against a stub.
    """

    def __call__(self, premise: str, hypothesis: str) -> tuple[NLILabel, float]:
        """Return `(label, confidence)`; confidence is a probability in [0, 1]."""
        ...


# --- Persistence -------------------------------------------------------------


class DocumentRepository(Protocol):
    """Persistence for the document tracking table.

    Every method takes and returns domain types. No ORM instance, session, or
    query object crosses this boundary — that is the point of it existing
    rather than the service layer opening a session itself.
    """

    def reserve(self, filename: str) -> int:
        """Allocate (or reuse) the row for `filename`, mark it pending, return its id."""
        ...

    def get(self, document_id: int) -> DocumentRecord | None: ...

    def find_by_filename(self, filename: str) -> DocumentRecord | None: ...

    def set_status(self, document_id: int, status: str, error: str | None = None) -> None: ...

    def record_ingestion(
        self,
        filename: str,
        content_hash: str,
        chunk_count: int,
        document_id: int | None = None,
    ) -> int:
        """Create or update the row for a completed chunking pass; return its id."""
        ...


class ChunkRepository(Protocol):
    """Persistence for chunk text."""

    def replace_for_document(self, document_id: int, texts: list[str]) -> int:
        """Atomically swap this document's chunks for `texts`; return the count."""
        ...

    def list_for_document(self, document_id: int | None = None) -> list[StoredChunk]:
        """All chunks for one document, or every chunk when `document_id` is None."""
        ...


# --- Search indexes ----------------------------------------------------------


class SearchIndex(Protocol):
    """A searchable index chunks are written into.

    Qdrant and Elasticsearch differ in almost every respect except this
    interface, which is the entire reason the ingestion service can hold a
    `Sequence[SearchIndex]` and be indifferent to how many there are or what
    they do. Adding a third index becomes a wiring change in
    `app.api.dependencies`, not an edit to the ingestion path.
    """

    name: str

    def index(self, chunks: list[StoredChunk], full_reindex: bool = False) -> int:
        """Write `chunks` idempotently; return how many were written."""
        ...


# --- Health ------------------------------------------------------------------


class HealthProbe(Protocol):
    """A readiness check against one external dependency.

    Returns `(ok, detail)` and never raises: the whole job of a health
    endpoint is reporting *which* dependency is down, and a probe that
    propagated its own exception would take out the report and say nothing
    about the others.
    """

    def __call__(self) -> tuple[bool, str | None]: ...


# --- Chunking ----------------------------------------------------------------


class DocumentLoader(Protocol):
    """Reads a file off disk: its chunk texts, and its content identity.

    Both operations belong to one port because both are "what this loader
    knows about a file", and the ingestion service needs them together — the
    hash decides whether the chunking is worth doing at all.
    """

    def chunk(self, path: str) -> list[str]: ...

    def content_hash(self, path: str) -> str: ...


__all__ = [
    "ChunkRepository",
    "ClaimGenerator",
    "DocumentLoader",
    "DocumentRepository",
    "EntailmentScorer",
    "HealthProbe",
    "IngestionResult",
    "NLILabel",
    "Reranker",
    "Retriever",
    "SearchIndex",
]
