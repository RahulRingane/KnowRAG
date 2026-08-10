"""The domain vocabulary — every type the layers speak to each other in.

Per KNowRAG_SPEC.md §6.4 (data contract) plus the types the rest of the
system needs: `Claim` (the shape a generator's structured output returns,
§5.2), `Chunk` (a retrieved passage), and the document-tracking records the
ingestion path passes across the repository boundary.

This module declares contracts only — no retrieval, generation,
verification, persistence, or HTTP behavior lives here, and it imports
nothing from any other layer. That is what lets every layer depend on it
without any of them depending on each other.

Two naming rules this layer enforces, both of which were previously
violated:

- `Chunk` means *a retrieved passage*, full stop. The SQLAlchemy row that
  happens to store one is `ChunkRow`, and it lives in
  `app.infrastructure.db.orm`. The two used to share a name across two
  modules, so `from ... import Chunk` meant different things depending on
  which file you were in.
- A repository returns these types, never ORM instances. `DocumentRecord`
  exists so `GET /ingest/{id}` serializes a plain domain object rather than
  a detached SQLAlchemy instance that raises on attribute access once its
  session has closed.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, computed_field


# What kind of thing the caller sent. "question" asks something and is
# answered from the corpus; "fact" asserts something and is checked against
# it. Decided by `app.domain.classification`, acted on by
# `app.services.query_service`, and reported on the wire so a caller can see
# which of the two routes produced what it is holding.
InputType = Literal["question", "fact"]


def chunk_key(document_id: int, chunk_index: int) -> str:
    """The one canonical string key for a chunk, everywhere.

    §4 already establishes this format as the Elasticsearch `_id`. Retrieval,
    verification, and the API response all reuse it so `chunk_ids` in a
    `ClaimVerdict` joins directly against `retrieved_chunk_ids` in a
    `FactCheckedResponse` and against the ES document id. Do not build this
    string by hand anywhere else — call this function.
    """
    return f"{document_id}:{chunk_index}"


class ClaimVerdict(BaseModel):
    """Per §6.4, plus `reason`.

    §6.2's pseudocode constructs this with `reason=`, `evidence=`, and
    `score=` keywords that §6.4's field list does not define. §6.4 wins — it
    is explicitly "the contract the FastAPI layer serializes" — so §6.2's
    `evidence` maps onto `chunk_ids` and its `score` onto `evidence_score`.
    `reason` is carried over as an optional field because §6.3 requires
    surfacing *why* a claim was rejected; without it the audit trail records
    that something was dropped but not what the caller needs to act on.
    """

    text: str
    # "REFUSAL" added 2026-08-03 (OQ-5). A refusal is not a proposition about
    # the world, so it is neither supported nor rejected — it is the system
    # saying it cannot answer. It was previously indistinguishable from an
    # UNSUPPORTED assertion, and only landed there *by accident*: the model
    # usually left refusals uncited, so they failed the "no citation provided"
    # path. When the model did cite one, the verifier scored it as evidence and
    # entailed it (observed at 0.904), turning "I cannot answer" into a
    # supported claim. See PLAN.md OQ-5.
    status: Literal["SUPPORTED", "UNSUPPORTED", "CONTRADICTED", "REFUSAL"]
    citations: list[str]
    evidence_score: float | None = None  # always None for REFUSAL — never scored
    chunk_ids: list[str]  # canonical keys from `chunk_key()`
    reason: str | None = None


class ScoringEvent(BaseModel):
    """One `(premise, hypothesis)` pair as it was actually handed to NLI.

    Diagnostic, and deliberately **not** part of §6.4 — nothing here reaches
    the wire. It exists because the single most useful question about a
    surprising verdict ("what exactly did the model read?") was previously
    unanswerable without a debugger: `ClaimVerdict` records the score and the
    chunk id, but not the premise text, not the tag-resolved pairing, and not
    the fact that the hypothesis is the claim with its `[Cn]` tags stripped.

    `premise` is the full chunk text, uncut. Truncation is a presentation
    decision and belongs to whoever renders this, not to the record of what
    was scored.

    `mismatch_reason` is `app.domain.conflation`'s finding, already rendered,
    or None when the pair drew no objection.
    """

    tag: str
    premise: str
    hypothesis: str
    label: str  # NLILabel, or "MISSING_CHUNK" when the tag resolved to nothing
    score: float
    document_id: int | None = None
    chunk_index: int | None = None
    mismatch_reason: str | None = None

    @property
    def flagged(self) -> bool:
        return self.mismatch_reason is not None


class FactCheckedResponse(BaseModel):
    """Per §6.4, verbatim, plus §6.3's `rejected_claims` as a derived field.

    §6.3 requires UNSUPPORTED claims to be "returned in a separate
    `rejected_claims` field so the caller can see what was filtered and
    why", but §6.4's field list — the contract this layer serializes — does
    not include one. Rather than pick a side, `rejected_claims` is computed
    from `claims`: it serializes into the API response exactly as §6.3
    asks, while `claims` stays the single stored source of truth.

    Storing it as a second real field was the alternative and is worse. Two
    lists that must agree will eventually disagree, and the failure mode is
    an audit trail that omits a rejection — precisely the silent dropping
    §6.3 exists to prevent.
    """

    # Which route produced this response (added 2026-08-09 with query/fact
    # routing). One response type serves both routes rather than a union: the
    # fields below mean the same thing on either path — `question` holds what
    # the caller sent, `claims` is the audit trail, `retrieved_chunk_ids` is
    # the evidence — so a second type would duplicate all of them to vary one.
    # Every existing client keeps working unchanged, and one that cares about
    # the distinction reads this field.
    #
    # Defaulted to "question" because that is exactly what every response
    # meant before the fact route existed, so an older cached or hand-built
    # payload deserializes to its original meaning rather than to a guess.
    input_type: InputType = "question"
    question: str  # the question asked, or on the fact route the statement checked
    # The question route reassembles this from the generated claims — SUPPORTED
    # and UNSUPPORTED both, since on that route the entailment score grades an
    # answer rather than gating it (see `app.domain.assembly`). The fact route
    # puts a sentence about the evidence here instead. Either way, what a claim
    # scored is on the claim, in `claims`.
    answer: str
    # "contradicted" is emitted by the fact route only, and is the reason
    # `state` grew a third value instead of folding into the existing two.
    # A statement the corpus actively refutes is not the same finding as one
    # the corpus is silent on, and reporting a refutation as
    # "insufficient_evidence" would understate it in exactly the direction
    # this system exists to prevent. The question route's `state` logic is
    # unchanged and still keys on SUPPORTED alone, so it never emits this.
    state: Literal["ok", "insufficient_evidence", "contradicted"]
    claims: list[ClaimVerdict]  # full audit trail, including rejected ones
    retrieved_chunk_ids: list[str]  # canonical keys from `chunk_key()`
    latency_ms: dict[str, float]  # per-stage timing for observability

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rejected_claims(self) -> list[ClaimVerdict]:
        """Assertions that failed verification — UNSUPPORTED and CONTRADICTED
        — each carrying the `reason` it failed for (§6.3).

        "Failed verification" and "kept out of the answer" were the same set
        until 2026-08-09 and no longer are. On the fact route they still
        coincide. On the question route only CONTRADICTED is withheld; an
        UNSUPPORTED claim appears in `answer` *and* here, which is the intended
        reading — this list is what the NLI model could not confirm, so a
        caller that wants to hedge, footnote, or re-ask has it, and a caller
        that just wants the answer is no longer denied one. See
        `app.domain.assembly`.

        **Contract change 2026-08-03 (OQ-5): REFUSAL claims are not included
        here.** They were, when a refusal was just an assertion that happened
        to fail. A refusal is not a filtered-out claim: nothing was dropped and
        there is no `reason`, so listing it beside genuine rejections
        misreports what happened. Refusals are on `refusals`.
        """
        return [c for c in self.claims if c.status in ("UNSUPPORTED", "CONTRADICTED")]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def refusals(self) -> list[ClaimVerdict]:
        """What the system explicitly declined to answer (§6.3, OQ-5).

        Distinct from `rejected_claims` in the one way that matters to a
        caller: a rejection means "the model asserted this and the evidence
        did not support it", a refusal means "the model declined, on purpose".
        Conflating them is what made a non-responsive answer indistinguishable
        from a well-evidenced one.
        """
        return [c for c in self.claims if c.status == "REFUSAL"]


class ClassifiedInput(BaseModel):
    """One classification decision: what the input is, and the input itself.

    Carries `text` alongside the verdict rather than returning a bare
    `InputType` so the classifier is the single thing that decides what the
    downstream route operates on. A future implementation that normalises its
    input — strips a "hey, " preamble, unwraps a quoted statement — changes
    what the pipeline sees by returning it here, with no caller learning that
    normalisation happened at all. A bare label would leave every call site
    reaching back for the original string and quietly bypassing that.
    """

    input_type: InputType
    text: str


class Claim(BaseModel):
    """Matches the JSON shape the generator returns per §5.2:

    {"kind": "assertion", "text": "Revenue grew 12% in Q3.", "citations": ["C2", "C4"]}

    `kind` is declared by the model at generation time rather than inferred
    afterwards from whether citations happen to be present (OQ-5). The
    inference was wrong whenever the model cited its own refusal.

    It defaults to "assertion" so that a payload from an older cache entry, or
    any caller constructing a `Claim` directly, keeps the pre-2026-08-03
    meaning — the strict path is the *schema*, which requires the field.
    """

    kind: Literal["assertion", "refusal"] = "assertion"
    text: str
    citations: list[str]


class Chunk(BaseModel):
    """One retrieved passage, as every layer above retrieval sees it.

    `source` records which index produced it, `score` is that retriever's own
    similarity/BM25 score, and `rerank_score` is added only after the
    cross-encoder rerank step. The latter two are optional so chunks from
    either retrieval path — pre-rerank or post-rerank — fit this same model.

    Produced by `app.infrastructure.search`; consumed by verification,
    context formatting, and the query service. None of those know which
    datastore it came from beyond the `source` label.
    """

    source: Literal["qdrant", "elastic"]
    score: float | None = None
    rerank_score: float | None = None
    document_id: int
    chunk_index: int
    text: str


# --- Ingestion-side vocabulary ----------------------------------------------


class DocumentStatus:
    """The three ingestion states §7 requires `GET /ingest/{document_id}`
    to report.

    A plain string constant holder rather than an `enum.Enum`: these values
    are persisted in a `String` column and serialized straight into a JSON
    response, so keeping them as `str` avoids `.value` noise at every use
    site.
    """

    PENDING = "pending"
    INDEXED = "indexed"
    FAILED = "failed"


class StoredChunk(BaseModel):
    """A chunk as it sits in Postgres, on its way to being indexed.

    Distinct from `Chunk` because it is a different thing at a different
    stage: this one has no score and no source (nothing has retrieved it
    yet), and it is what a `ChunkRepository` hands to a `SearchIndex`.
    Collapsing the two would give `Chunk` two optional-everything shapes and
    lose the ability to tell "not yet retrieved" from "retrieved with no
    score".
    """

    document_id: int
    chunk_index: int
    text: str


class DocumentRecord(BaseModel):
    """One row of the ingestion tracking table, detached from the ORM.

    Returned by `DocumentRepository` instead of a SQLAlchemy instance: the
    consumer is a route that serializes it after the session has already
    closed, and a detached ORM object raises on attribute access.
    """

    document_id: int
    filename: str
    status: str
    chunk_count: int
    content_hash: str = ""
    ingested_at: datetime | None = None
    error: str | None = None


class IngestionResult(BaseModel):
    """What one run of the ingestion pipeline did.

    `status` is the terminal state of the *whole* pipeline (Postgres plus
    both indexes), while `pg_status` records what the Postgres stage alone
    did — `"ingested"` or `"skipped"` when an unchanged `content_hash` let it
    short-circuit. Callers need both: a re-upload of an identical file is a
    skip that still ends `indexed`, which is not something one field can say.
    """

    document_id: int
    status: str
    pg_status: str
    chunk_count: int
    content_hash: str
    vector_count: int = 0
    keyword_count: int = 0
