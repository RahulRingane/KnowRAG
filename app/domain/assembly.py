"""§6.3 — turning verdicts into the response a caller receives.

Split out of the old `app/verify.py` because it answers a different question
from verification. Verification decides *whether* a claim is supported;
assembly decides what the caller is shown and what the audit trail records.
The rules here are the ones that make the system honest — that an
unsupported claim is stripped from the answer but never from `claims`, and
that "no evidence" is reported as `insufficient_evidence` rather than as an
empty string or an error.

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


def assemble_answer_text(verdicts: list[ClaimVerdict]) -> str:
    """Reassemble the final answer text from SUPPORTED claims only.

    Each kept sentence gets its citation tags appended, e.g. "Revenue grew
    12% in Q3. [C2][C4]" — mirroring the tagging convention `Claim.text`
    was stripped of when the generator produced structured claims (§5.2).
    UNSUPPORTED and CONTRADICTED claims are never passed in here; they are
    stripped from the answer but still recorded in the audit trail by the
    caller (`assemble_response`).
    """
    sentences = []
    for v in verdicts:
        if v.status != "SUPPORTED":
            continue
        tags = "".join(f"[{c}]" for c in v.citations)
        text = v.text.rstrip()
        sentences.append(f"{text} {tags}" if tags else text)
    return " ".join(sentences)


def assemble_response(
    question: str,
    verdicts: list[ClaimVerdict],
    retrieved_chunk_ids: list[str],
    latency_ms: dict[str, float] | None = None,
) -> FactCheckedResponse:
    """Build the final `FactCheckedResponse` per §6.3 / §6.4.

    - SUPPORTED claims are reassembled into `answer` with citations.
    - UNSUPPORTED and CONTRADICTED claims are stripped from `answer` but
      are still present in `claims` (the full audit trail) along with
      their `reason` — silent dropping would defeat the entire audit
      purpose of this system.
    - If no claim ends up SUPPORTED, `state` is "insufficient_evidence" and
      `answer` is `INSUFFICIENT_EVIDENCE_MESSAGE` — never an empty string,
      so the caller can tell "no evidence exists" apart from "something
      broke."
    """
    supported = [v for v in verdicts if v.status == "SUPPORTED"]
    refusals = [v for v in verdicts if v.status == "REFUSAL"]

    # OQ-5: refusals are surfaced in `answer`, not stripped from it. The
    # failure this fixes is a *non-responsive* answer — the system returning a
    # true, cited fact about a neighbouring question with nothing marking the
    # substitution, so the reader cannot tell they were given something
    # adjacent. The refusal text is the only thing that marks it, so dropping
    # it from the answer would leave the bug in place while the audit trail
    # quietly recorded the truth.
    #
    # `state` is unchanged and still keys on SUPPORTED alone: a refusal is not
    # evidence, so an answer made only of refusals remains
    # "insufficient_evidence".
    refusal_text = " ".join(v.text.rstrip() for v in refusals)

    if supported:
        answer = assemble_answer_text(supported)
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
        question=question,
        answer=answer,
        state=state,
        claims=verdicts,
        retrieved_chunk_ids=retrieved_chunk_ids,
        latency_ms=latency_ms or {},
    )
