"""§6.3 — turning verdicts into the response a caller receives.

Split out of the old `app/verify.py` because it answers a different question
from verification. Verification decides *whether* a claim is supported;
assembly decides what the caller is shown and what the audit trail records.
The rules here are the ones that make the system honest — that a claim's
verdict is never dropped from `claims`, and that "no evidence" is reported as
`insufficient_evidence` rather than as an empty string or an error.

**The two routes weigh a verdict differently, and that asymmetry lives here
(changed 2026-08-09).** On the fact route the verdict *is* the result: the
caller asserted something and asked whether the corpus bears it out, so the
threshold decides the answer. On the question route the verdict is advisory:
the caller asked a question, an answer was generated from retrieved context,
and a sub-threshold entailment score means "this NLI model could not confirm
the phrasing", not "this is wrong". Withholding the answer over that was
losing correct, grounded answers — a paraphrase that no single chunk entails
verbatim scores ~0.49 and vanished. So `assemble_response` (questions) now
shows the answer and records the scores; `assemble_fact_check_response`
(facts) is unchanged and still strict.

What is *not* advisory on either route: a CONTRADICTED claim is still kept
out of the answer, and a refusal is still not evidence. Those are findings,
not failures to confirm.

Pure functions over domain types: no I/O, no config, no HTTP.
"""

from __future__ import annotations

import logging
from typing import Literal

from app.domain.models import ClaimVerdict, FactCheckedResponse

logger = logging.getLogger(__name__)

INSUFFICIENT_EVIDENCE_MESSAGE = (
    "Insufficient evidence in the retrieved context to answer this question."
)

# The fact route's three outcomes, phrased as statements about the evidence
# rather than about the claim. "The retrieved evidence contradicts this" is
# something this system can actually establish; "this is false" is not — the
# corpus is 28 pages of one document, and absence or disagreement in it is a
# fact about the corpus, not about the world.
FACT_SUPPORTED_MESSAGE = "The retrieved evidence supports this statement."
FACT_CONTRADICTED_MESSAGE = "The retrieved evidence contradicts this statement."
FACT_UNSUPPORTED_MESSAGE = (
    "The retrieved evidence neither supports nor contradicts this statement."
)


# The statuses whose text the question route puts in front of the reader.
# SUPPORTED is self-explanatory. UNSUPPORTED is included because on this route
# it means "the NLI model did not confirm this", which is not the same as "this
# is false" — see the module docstring. CONTRADICTED is deliberately absent:
# the evidence actively disagrees with it, which usually means the generator
# misread a chunk (a sign flip, a transposed date), and that is the one failure
# mode worth withholding text over. REFUSAL is absent because it is handled
# separately by `assemble_response` — it is appended but never counted as an
# answer.
ANSWERABLE_STATUSES = ("SUPPORTED", "UNSUPPORTED")


def render_claims(verdicts: list[ClaimVerdict]) -> str:
    """Render every verdict passed in as one answer string, with citations.

    Each sentence gets its citation tags appended, e.g. "Revenue grew 12% in
    Q3. [C2][C4]" — mirroring the tagging convention `Claim.text` was stripped
    of when the generator produced structured claims (§5.2).

    Selects nothing: the caller decides what belongs in the answer, which is
    the whole of the routes' disagreement about what a verdict means. Keeping
    that decision in the caller is what lets both routes share this renderer
    without either inheriting the other's policy.
    """
    sentences = []
    for v in verdicts:
        tags = "".join(f"[{c}]" for c in v.citations)
        text = v.text.rstrip()
        sentences.append(f"{text} {tags}" if tags else text)
    return " ".join(sentences)


def assemble_answer_text(verdicts: list[ClaimVerdict]) -> str:
    """Reassemble answer text from SUPPORTED claims only.

    The strict rendering, kept as its own function because it is a meaningful
    thing to ask for — "what part of this answer is actually entailed by the
    evidence" — and because eval scripts and callers outside the query
    pipeline still want exactly that. `assemble_response` no longer uses it;
    see the module docstring for why the question route stopped filtering.
    """
    return render_claims([v for v in verdicts if v.status == "SUPPORTED"])


def assemble_response(
    question: str,
    verdicts: list[ClaimVerdict],
    retrieved_chunk_ids: list[str],
    latency_ms: dict[str, float] | None = None,
) -> FactCheckedResponse:
    """Build the final `FactCheckedResponse` per §6.3 / §6.4 — the **question**
    route only. Statements go through `assemble_fact_check_response`.

    - SUPPORTED and UNSUPPORTED claims are reassembled into `answer` with
      citations. The entailment threshold does not gate what the caller sees
      on this route (changed 2026-08-09 — see the module docstring); it grades
      it, and the grade is on every verdict in `claims`.
    - CONTRADICTED claims are still kept out of `answer`. The evidence
      disagrees with them, which is a finding rather than an unconfirmed
      phrasing.
    - Every claim is present in `claims` with its `evidence_score` and
      `reason`, whichever way it went. That is unchanged and is the part that
      makes the answer auditable: a reader can see that a sentence scored
      0.489 without being denied the sentence.
    - If nothing answerable was generated, `state` is "insufficient_evidence"
      and `answer` is `INSUFFICIENT_EVIDENCE_MESSAGE` — never an empty string,
      so the caller can tell "no evidence exists" apart from "something
      broke."
    """
    answerable = [v for v in verdicts if v.status in ANSWERABLE_STATUSES]
    refusals = [v for v in verdicts if v.status == "REFUSAL"]

    # OQ-5: refusals are surfaced in `answer`, not stripped from it. The
    # failure this fixes is a *non-responsive* answer — the system returning a
    # true, cited fact about a neighbouring question with nothing marking the
    # substitution, so the reader cannot tell they were given something
    # adjacent. The refusal text is the only thing that marks it, so dropping
    # it from the answer would leave the bug in place while the audit trail
    # quietly recorded the truth.
    #
    # A refusal is not evidence, so an answer made only of refusals remains
    # "insufficient_evidence" — that did not change when the threshold stopped
    # gating the answer. The two are different events: a refusal is the model
    # declining because the context does not cover the question, and there is
    # genuinely nothing to show. A sub-threshold score is the model having
    # answered and a *second* model failing to confirm the wording.
    refusal_text = " ".join(v.text.rstrip() for v in refusals)

    if answerable:
        answer = render_claims(answerable)
        if refusal_text:
            answer = f"{answer} {refusal_text}"
        state: Literal["ok", "insufficient_evidence"] = "ok"
    elif refusals:
        # More useful than the generic message: it names what is missing.
        answer = refusal_text
        state = "insufficient_evidence"
    else:
        answer = INSUFFICIENT_EVIDENCE_MESSAGE
        state = "insufficient_evidence"

    contradicted = [v for v in verdicts if v.status == "CONTRADICTED"]
    if contradicted:
        logger.warning(
            "%d claim(s) contradicted by cited evidence for question=%r: %s",
            len(contradicted),
            question,
            [v.text for v in contradicted],
        )

    return FactCheckedResponse(
        input_type="question",
        question=question,
        answer=answer,
        state=state,
        claims=verdicts,
        retrieved_chunk_ids=retrieved_chunk_ids,
        latency_ms=latency_ms or {},
    )


def assemble_fact_check_response(
    statement: str,
    verdict: ClaimVerdict,
    retrieved_chunk_ids: list[str],
    latency_ms: dict[str, float] | None = None,
) -> FactCheckedResponse:
    """Build the response for the fact route: one statement, one verdict.

    The counterpart to `assemble_response`, and structurally simpler for one
    reason — there is nothing to select. `assemble_response` exists to strip
    unsupported claims out of an answer the *model* wrote while keeping them
    in the audit trail. Here the caller wrote the only claim there is, so
    nothing can be filtered: the single verdict is the whole result, and it
    goes into `claims` regardless of which way it went.

    `answer` is a statement about the evidence, not the statement echoed back
    with citation tags appended. Appending them was the obvious move and is
    wrong: the claim is scored against *every* retrieved chunk, so its
    `citations` list is "what was considered", not "what supports this", and
    rendering all of them as `[C1][C2][C3]...` would assert corroboration
    that was never established. The precise attribution a caller needs is on
    the verdict itself — `evidence_score` for the strength, `chunk_ids` for
    the evidence — and stays exact there.

    `state` mapping, and the one place it differs from the question route:

    - SUPPORTED    -> "ok"
    - CONTRADICTED -> "contradicted" (see `FactCheckedResponse.state`)
    - UNSUPPORTED  -> "insufficient_evidence"

    REFUSAL is unreachable here: the caller's statement is constructed as an
    assertion, and only a generator can produce a refusal.
    """
    if verdict.status == "SUPPORTED":
        answer = FACT_SUPPORTED_MESSAGE
        state: Literal["ok", "insufficient_evidence", "contradicted"] = "ok"
    elif verdict.status == "CONTRADICTED":
        answer = FACT_CONTRADICTED_MESSAGE
        state = "contradicted"
        # Same warning the question route emits, and for a different reason.
        # There, a contradiction means the generator misread a chunk. Here it
        # means the corpus refutes something a caller asserted — the fact
        # route's most consequential outcome, and worth a log line at the
        # level an operator actually reads.
        logger.warning(
            "Statement contradicted by retrieved evidence: statement=%r score=%s chunks=%s",
            statement,
            verdict.evidence_score,
            verdict.chunk_ids,
        )
    else:
        answer = FACT_UNSUPPORTED_MESSAGE
        state = "insufficient_evidence"

    return FactCheckedResponse(
        input_type="fact",
        question=statement,
        answer=answer,
        state=state,
        claims=[verdict],
        retrieved_chunk_ids=retrieved_chunk_ids,
        latency_ms=latency_ms or {},
    )
