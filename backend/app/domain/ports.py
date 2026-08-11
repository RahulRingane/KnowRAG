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
    ClassifiedInput,
    DocumentRecord,
    IngestionResult,
    ScoringEvent,
    StoredChunk,
    User,
)

# The three NLI classes, and the sentinel `_score_citations` records when a
# claim cites a tag that resolves to no chunk.
NLILabel = Literal["contradiction", "entailment", "neutral"]


# --- Classification ----------------------------------------------------------


class InputClassifier(Protocol):
    """Decides whether an input is a question to answer or a fact to check.

    A callable protocol for the same reason `EntailmentScorer` is one: the
    thing that satisfies it is a single-operation function, so
    `app.domain.classification.classify_input` *is* an implementation with no
    wrapper class, and a test substitutes
    `lambda text: ClassifiedInput(input_type="fact", text=text)` directly.

    Declared here rather than beside the heuristic so the replacement path is
    already open. Today's implementation is pure string inspection and lives
    in the domain; a model- or LLM-backed one belongs in
    `app.infrastructure`, would satisfy this protocol identically, and would
    reach `QueryService` through the same constructor argument — which is the
    whole reason the seam exists before there is anything to swap.
    """

    def __call__(self, text: str) -> ClassifiedInput:
        """Classify `text`, returning the decision and the text to route."""
        ...


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


class ScoringObserver(Protocol):
    """Receives every `(premise, hypothesis)` pair the verifier scored.

    A port, and injected like every other collaborator, for the same reason
    `app.cli.query` decorates the `Retriever` instead of putting a print
    statement inside `HybridRetriever`: showing a person what the model read is
    a property of one entrypoint, and the verifier must keep having no opinion
    about whether anyone is watching. Production passes nothing.

    Called once per scored citation, in citation order, *before* the §6.2
    decision runs — so a rejected pair is reported whether or not it ended up
    mattering to the verdict, which is exactly the case worth seeing.

    Must not raise. `ClaimVerifier` treats this as a side channel and will not
    let a broken observer take down a verification pass; an observer that
    throws will have its exception swallowed rather than turn a diagnostic into
    an outage.
    """

    def __call__(self, event: ScoringEvent) -> None: ...


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

    def list_all(self) -> list[DocumentRecord]:
        """Every ingested document, newest first — the read side of `GET /documents`.

        Added for the frontend's document dashboard (WS-0, frontend_plan.md
        §2), which needs to enumerate what's in the corpus; `get()` only
        answers for one id it already knows.
        """
        ...


class ChunkRepository(Protocol):
    """Persistence for chunk text."""

    def replace_for_document(self, document_id: int, texts: list[str]) -> int:
        """Atomically swap this document's chunks for `texts`; return the count."""
        ...

    def list_for_document(self, document_id: int | None = None) -> list[StoredChunk]:
        """All chunks for one document, or every chunk when `document_id` is None."""
        ...

    def get_by_keys(self, keys: list[tuple[int, int]]) -> list[StoredChunk]:
        """Fetch chunks by their canonical `(document_id, chunk_index)` keys.

        Added for `GET /chunks` (WS-0, frontend_plan.md §2) — the evidence
        panel's only way to turn a citation like `"1:3"` back into text. A
        key with no matching row is simply absent from the result rather than
        an error: a citation list built from a slightly stale response should
        degrade, not fail the caller. One SELECT, not one per key — a `Chunk`
        panel with even a handful of citations must not become an N+1 query.
        """
        ...


# --- Auth (WS-1, frontend_plan.md §3) -----------------------------------------


class UserRepository(Protocol):
    """Persistence for the `users` table.

    `has_users()` is the entire mechanism behind "first user registers, then
    signup closes" — `AuthService.register()` checks it before writing a new
    row. It is its own method rather than `len(list_all()) > 0` so an
    implementation can answer with an indexed existence check instead of a
    full-table read, and so there never needs to be a `list_all()` on this
    port at all — nothing in §3 enumerates users.
    """

    def create(self, username: str, password_hash: str) -> User: ...

    def get_by_username(self, username: str) -> User | None: ...

    def get_by_id(self, user_id: int) -> User | None: ...

    def has_users(self) -> bool: ...

    def increment_token_version(self, user_id: int) -> None:
        """Revoke every outstanding token for this user — `POST /auth/logout`.

        `AuthService` and `get_current_user` both check a presented token's
        `ver` claim against the row this reads back, so incrementing it is
        enough to make every token issued before this call fail verification
        immediately, with no separate token/session table.
        """
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
    "InputClassifier",
    "NLILabel",
    "Reranker",
    "Retriever",
    "ScoringObserver",
    "SearchIndex",
    "UserRepository",
]
