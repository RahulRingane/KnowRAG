"""`ClaimVerifier`'s composition guard and its scoring trace.

Split from `test_verify.py` rather than appended to it because that module is
organised around §6.2's original five paths and shares one module-global
verifier between its wrappers. These tests need per-test verifiers (an
observer, a mixed scorer keyed on premise rather than hypothesis), so they
build their own.

Everything here is offline: the scorer is a stub, and the guard is pure string
inspection. The premises are the real ones from `data.pdf`, with the real NLI
labels and scores hardcoded into the stub — so this exercises the decision
change against the exact numbers that motivated it without loading a model.
"""

from __future__ import annotations

import pytest

from app.domain.models import Chunk, Claim, ScoringEvent
from app.domain.verification import ClaimVerifier, build_chunk_tag_map

# The three chunks that decided the original verdict, verbatim, with the label
# and score the real model produced for "embedded system is an operating
# system". C1 contradicts; C2 and C4 entail only by composition.
PROSE = Chunk(
    source="qdrant",
    document_id=1,
    chunk_index=3,
    text=(
        "Unit 1 Introduction to Embedded Systems An embedded system is a combination "
        "of hardware and software with some attached peripherals to perform a specific "
        "task or a narrow range of tasks with restricted resources."
    ),
)
TABLE_ROW_1 = Chunk(
    source="qdrant",
    document_id=1,
    chunk_index=5,
    text=(
        "Table 1.1: Embedded Systems vs. General Computing Systems Row 1 | "
        "Embedded Systems: A system which is a combination of special purpose "
        "hardware and embedded OS for executing a specific set of applications"
    ),
)
TABLE_ROW_2 = Chunk(
    source="elastic",
    document_id=1,
    chunk_index=6,
    text=(
        "Table 1.1: Embedded Systems vs. General Computing Systems Row 2 | "
        "Embedded Systems: May or may not contain an operating system for functioning"
    ),
)

MEASURED = {
    PROSE.text: ("contradiction", 0.802),
    TABLE_ROW_1.text: ("entailment", 0.987),
    TABLE_ROW_2.text: ("entailment", 0.685),
}

STATEMENT = "embedded system is an operating system"


def _scorer(premise: str, hypothesis: str):
    return MEASURED[premise]


def _claim(*, text: str = STATEMENT, citations=("C1", "C2", "C3")) -> Claim:
    return Claim(kind="assertion", text=text, citations=list(citations))


class TestCompositionGuardChangesTheVerdict:
    def test_conflated_entailment_loses_to_a_real_contradiction(self):
        """The whole point, end to end.

        Before the guard: best entailment 0.987 >= 0.55 -> SUPPORTED, and the
        0.802 contradiction §6.2 had already computed was never reached. After:
        both conflations leave the race and branch 4 decides.
        """
        tags = build_chunk_tag_map([PROSE, TABLE_ROW_1, TABLE_ROW_2])
        verdict = ClaimVerifier(scorer=_scorer, threshold=0.55).verify_claim(_claim(), tags)

        assert verdict.status == "CONTRADICTED"
        assert verdict.evidence_score == pytest.approx(0.802)
        assert "[C1]" in verdict.reason

    def test_the_verdict_is_backed_by_a_chunk_not_by_the_regex(self):
        """CONTRADICTED must cite the contradicting chunk, not the flagged one."""
        tags = build_chunk_tag_map([PROSE, TABLE_ROW_1, TABLE_ROW_2])
        verdict = ClaimVerifier(scorer=_scorer, threshold=0.55).verify_claim(_claim(), tags)

        assert verdict.chunk_ids == ["1:3", "1:5", "1:6"]
        assert verdict.evidence_score == pytest.approx(0.802)

    def test_all_entailments_flagged_and_no_contradiction_is_unsupported(self):
        """Without a contradiction to fall through to, the guard yields
        UNSUPPORTED — never a CONTRADICTED invented from a pattern match."""
        tags = build_chunk_tag_map([TABLE_ROW_1, TABLE_ROW_2])
        verdict = ClaimVerifier(scorer=_scorer, threshold=0.55).verify_claim(
            _claim(citations=("C1", "C2")), tags
        )

        assert verdict.status == "UNSUPPORTED"
        # The score reported is the *rejected* entailment, not whichever
        # citation happened to sort first once every key tied at -1.
        assert verdict.evidence_score == pytest.approx(0.987)
        assert "rejected" in verdict.reason
        assert "composition" in verdict.reason

    def test_an_unflagged_entailment_still_wins_normally(self):
        """The guard must be inert on everything it has no opinion about."""
        tags = build_chunk_tag_map([PROSE])
        verdict = ClaimVerifier(
            scorer=lambda premise, hypothesis: ("entailment", 0.91), threshold=0.55
        ).verify_claim(
            _claim(text="an embedded system is a combination of hardware and software",
                   citations=("C1",)),
            tags,
        )

        assert verdict.status == "SUPPORTED"
        assert verdict.evidence_score == pytest.approx(0.91)
        assert verdict.reason is None

    def test_guard_does_not_run_on_non_entailment_labels(self):
        """A finding against a neutral pair could not change any verdict.

        Reporting one anyway would put a rejection in the trace that rejected
        nothing — see `_score_citations`.
        """
        seen: list[ScoringEvent] = []
        tags = build_chunk_tag_map([TABLE_ROW_1])
        ClaimVerifier(
            scorer=lambda premise, hypothesis: ("neutral", 0.93),
            threshold=0.55,
            observer=seen.append,
        ).verify_claim(_claim(citations=("C1",)), tags)

        assert [e.flagged for e in seen] == [False]


class TestScoringTrace:
    def test_one_event_per_citation_in_citation_order(self):
        seen: list[ScoringEvent] = []
        tags = build_chunk_tag_map([PROSE, TABLE_ROW_1, TABLE_ROW_2])
        ClaimVerifier(scorer=_scorer, threshold=0.55, observer=seen.append).verify_claim(
            _claim(), tags
        )

        assert [e.tag for e in seen] == ["C1", "C2", "C3"]
        assert [e.label for e in seen] == ["contradiction", "entailment", "entailment"]
        assert [e.flagged for e in seen] == [False, True, True]

    def test_event_carries_the_full_premise_not_a_preview(self):
        """Truncation is the renderer's job. A record that cut the premise
        would misreport what was scored."""
        seen: list[ScoringEvent] = []
        tags = build_chunk_tag_map([TABLE_ROW_1])
        ClaimVerifier(scorer=_scorer, threshold=0.55, observer=seen.append).verify_claim(
            _claim(citations=("C1",)), tags
        )

        assert seen[0].premise == TABLE_ROW_1.text
        assert seen[0].document_id == 1
        assert seen[0].chunk_index == 5

    def test_hypothesis_is_the_claim_with_citation_tags_stripped(self):
        """What the model read, not what the caller sent — the two differ, and
        the difference is itself a documented bug class."""
        seen: list[ScoringEvent] = []
        tags = build_chunk_tag_map([PROSE])
        ClaimVerifier(
            scorer=lambda premise, hypothesis: ("neutral", 0.5), observer=seen.append
        ).verify_claim(_claim(text="Embedded systems are cheap [C1].", citations=("C1",)), tags)

        assert seen[0].hypothesis == "Embedded systems are cheap."

    def test_missing_chunk_is_traced_too(self):
        seen: list[ScoringEvent] = []
        ClaimVerifier(scorer=_scorer, threshold=0.55, observer=seen.append).verify_claim(
            _claim(citations=("C9",)), build_chunk_tag_map([PROSE])
        )

        assert [(e.tag, e.label) for e in seen] == [("C9", "MISSING_CHUNK")]
        assert seen[0].document_id is None

    def test_a_raising_observer_does_not_break_verification(self):
        """The observer is a diagnostic side channel. A broken renderer costs a
        trace line, not a verdict."""

        def boom(event):
            raise RuntimeError("renderer exploded")

        tags = build_chunk_tag_map([PROSE, TABLE_ROW_1, TABLE_ROW_2])
        verdict = ClaimVerifier(scorer=_scorer, threshold=0.55, observer=boom).verify_claim(
            _claim(), tags
        )

        assert verdict.status == "CONTRADICTED"

    def test_no_observer_is_the_production_default(self):
        tags = build_chunk_tag_map([PROSE, TABLE_ROW_1, TABLE_ROW_2])
        assert ClaimVerifier(scorer=_scorer, threshold=0.55).verify_claim(
            _claim(), tags
        ).status == "CONTRADICTED"


class TestBothRoutesSeeTheGuard:
    """The guard lives in `verify_claim`, which both routes share.

    The fact route is where it was aimed, but the question route runs the same
    procedure over generated claims, and there the consequence is different and
    worth pinning: an UNSUPPORTED claim still appears in `answer` (that changed
    2026-08-09), while a CONTRADICTED one is withheld. So a conflation that
    merely fails to confirm is shown with its score, and one the corpus
    actively refutes is pulled out of the answer.
    """

    class _StubRetriever:
        def search(self, query, k):
            return [PROSE, TABLE_ROW_1, TABLE_ROW_2]

    class _StubGenerator:
        def __init__(self, claims):
            self._claims = claims

        def generate(self, question, context_block, chunk_ids=None):
            return self._claims

        def stream(self, question, context_block):  # pragma: no cover - unused
            yield '{"claims": []}'

    def _service(self, claims):
        from app.services.query_service import QueryService

        return QueryService(
            retriever=self._StubRetriever(),
            generator=self._StubGenerator(claims),
            verifier=ClaimVerifier(scorer=_scorer, threshold=0.55),
            top_k=3,
        )

    def test_fact_route_reports_contradicted(self):
        response = self._service([]).run(STATEMENT)

        assert response.input_type == "fact"
        assert response.state == "contradicted"
        assert response.claims[0].evidence_score == pytest.approx(0.802)

    def test_question_route_withholds_the_contradicted_claim(self):
        claim = Claim(
            kind="assertion", text=f"{STATEMENT} [C1][C2][C3]", citations=["C1", "C2", "C3"]
        )

        response = self._service([claim]).run("what is an embedded system?")

        assert response.input_type == "question"
        assert response.claims[0].status == "CONTRADICTED"
        assert STATEMENT not in response.answer
        assert response.state == "insufficient_evidence"
