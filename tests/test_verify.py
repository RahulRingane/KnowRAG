"""Unit tests for `app.domain.verification` — the claim verification core.

These cover all five `verify_claim()` paths from KNowRAG_SPEC.md §6.2 using
handwritten (chunk, claim) fixtures. The bulk of the suite injects a stub
`EntailmentScorer` into `ClaimVerifier`, so the threshold math and branch
selection are verifiable fast, offline, with no model download and no
network/DB/Qdrant/ES calls. A separate, explicitly-marked class at the bottom
exercises the real NLI model (`settings.nli_model`, loaded by
`app.infrastructure.ml.nli`) to validate that the stubbed assumptions actually
hold in practice.

The scorer is a constructor argument rather than a module global, which is
what lets the decision logic live in the domain layer with no torch anywhere
near it — see `app/domain/verification.py`.

Note what stubbing the scorer cannot catch: a label order that disagrees with
the loaded checkpoint inverts every verdict in production while every stubbed
test here stays green. `TestNliLabelOrder` exists for that blind spot.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.domain import verification as verify
from app.domain.assembly import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    assemble_answer_text,
    assemble_response,
)
from app.domain.models import Chunk, Claim, ClaimVerdict, chunk_key
from app.domain.verification import ClaimVerifier, build_chunk_tag_map
from app.infrastructure.ml import nli

# ---------------------------------------------------------------------
# Fixtures: handwritten chunks reused across tests.
# ---------------------------------------------------------------------

RISC_CHUNK = Chunk(
    source="qdrant",
    document_id=1,
    chunk_index=0,
    text=(
        "RISC architectures use fewer, simpler instructions than CISC "
        "architectures, which lets each instruction execute in a single "
        "clock cycle."
    ),
)

UNRELATED_CHUNK = Chunk(
    source="elastic",
    document_id=2,
    chunk_index=5,
    text="The quarterly board meeting is scheduled for the second Tuesday of every month.",
)


def _tags(*chunks: Chunk) -> dict[str, Chunk]:
    return build_chunk_tag_map(list(chunks))


# ---------------------------------------------------------------------
# Substituting the model.
#
# The NLI model is a constructor argument (`ClaimVerifier(scorer=...)`), not a
# module global, so these tests supply a stub scorer instead of monkeypatching
# `predict_nli`. The wrappers below keep each test body about the *decision*
# under test rather than about wiring a verifier.
# ---------------------------------------------------------------------

_verifier: ClaimVerifier | None = None


def _use_scorer(scorer):
    """Build the verifier this module's wrappers delegate to."""
    global _verifier
    _verifier = ClaimVerifier(scorer=scorer)
    return scorer


def verify_claim(claim, chunks_by_tag):
    return _verifier.verify_claim(claim, chunks_by_tag)


def verify_claims(claims, chunks):
    return _verifier.verify_claims(claims, chunks)


def score_with_long_premise_fallback(premise, hypothesis):
    return _verifier.score(premise, hypothesis)


def _patch_nli(monkeypatch: pytest.MonkeyPatch, table: dict[str, tuple[str, float]]):
    """Replace `predict_nli` with a deterministic lookup keyed by hypothesis text.

    Keeps fixtures declarative: each test says what the model *would* say
    for a given claim, without needing the real ~400MB model loaded.
    """

    def fake_predict_nli(premise: str, hypothesis: str):
        return table[hypothesis]

    return _use_scorer(fake_predict_nli)


# ---------------------------------------------------------------------
# Path 1: no citations -> UNSUPPORTED, reason="no citation provided"
# ---------------------------------------------------------------------


def test_no_citations_is_unsupported_with_fixed_reason(monkeypatch):
    claim = Claim(text="Something was claimed with no backing.", citations=[])
    chunks_by_tag = _tags(RISC_CHUNK)

    # Sanity: predict_nli must never be called for this path.
    def boom(*a, **k):
        raise AssertionError("predict_nli should not be called when there are no citations")

    _use_scorer(boom)

    verdict = verify_claim(claim, chunks_by_tag)

    assert verdict.status == "UNSUPPORTED"
    assert verdict.reason == "no citation provided"
    assert verdict.citations == []
    assert verdict.chunk_ids == []
    assert verdict.evidence_score is None


# ---------------------------------------------------------------------
# Path 2: cited tag with no matching chunk -> scored 0.0, must not crash
# ---------------------------------------------------------------------


def test_missing_chunk_tag_does_not_crash_and_scores_zero(monkeypatch):
    claim = Claim(text="RISC uses fewer instructions than CISC.", citations=["C99"])
    chunks_by_tag = _tags(RISC_CHUNK)  # only has "C1", not "C99"

    def boom(*a, **k):
        raise AssertionError("predict_nli should not be called for a missing chunk")

    _use_scorer(boom)

    verdict = verify_claim(claim, chunks_by_tag)  # must not raise

    assert verdict.status == "UNSUPPORTED"
    assert verdict.evidence_score == 0.0
    assert verdict.chunk_ids == []  # no chunk actually resolved
    assert "C99" in verdict.reason


def test_missing_chunk_among_others_is_ignored_not_fatal(monkeypatch):
    """A mix of a real citation and a bogus one: the real one still counts."""
    claim = Claim(text="RISC uses fewer instructions than CISC.", citations=["C1", "C99"])
    chunks_by_tag = _tags(RISC_CHUNK)

    _patch_nli(monkeypatch, {claim.text: ("entailment", 0.97)})

    verdict = verify_claim(claim, chunks_by_tag)

    assert verdict.status == "SUPPORTED"
    assert verdict.chunk_ids == [chunk_key(RISC_CHUNK.document_id, RISC_CHUNK.chunk_index)]


# ---------------------------------------------------------------------
# Path 3: best entailment >= threshold -> SUPPORTED
# ---------------------------------------------------------------------


def test_entailed_claim_is_supported(monkeypatch):
    claim = Claim(
        text="RISC architectures use fewer instructions than CISC architectures.",
        citations=["C1"],
    )
    chunks_by_tag = _tags(RISC_CHUNK)
    _patch_nli(monkeypatch, {claim.text: ("entailment", 0.93)})

    verdict = verify_claim(claim, chunks_by_tag)

    assert verdict.status == "SUPPORTED"
    assert verdict.reason is None
    assert verdict.evidence_score == pytest.approx(0.93)
    assert verdict.chunk_ids == [chunk_key(RISC_CHUNK.document_id, RISC_CHUNK.chunk_index)]


def test_entailment_exactly_at_threshold_is_supported(monkeypatch):
    """Boundary check: `>=`, not `>`."""
    claim = Claim(text="Boundary claim.", citations=["C1"])
    chunks_by_tag = _tags(RISC_CHUNK)
    _patch_nli(monkeypatch, {claim.text: ("entailment", settings.claim_verification_threshold)})

    verdict = verify_claim(claim, chunks_by_tag)

    assert verdict.status == "SUPPORTED"


def test_entailment_just_below_threshold_is_unsupported(monkeypatch):
    claim = Claim(text="Almost-there claim.", citations=["C1"])
    chunks_by_tag = _tags(RISC_CHUNK)
    just_below = settings.claim_verification_threshold - 0.01
    _patch_nli(monkeypatch, {claim.text: ("entailment", just_below)})

    verdict = verify_claim(claim, chunks_by_tag)

    assert verdict.status == "UNSUPPORTED"
    assert verdict.evidence_score == pytest.approx(just_below)
    assert "threshold" in verdict.reason


# ---------------------------------------------------------------------
# Path 4: fact absent from the chunk -> UNSUPPORTED (neutral, not entailed)
# ---------------------------------------------------------------------


def test_claim_about_absent_fact_is_unsupported(monkeypatch):
    claim = Claim(
        text="The company reported record profits in Q3.",
        citations=["C1"],
    )
    chunks_by_tag = _tags(RISC_CHUNK)  # chunk is about RISC/CISC, unrelated to the claim
    _patch_nli(monkeypatch, {claim.text: ("neutral", 0.41)})

    verdict = verify_claim(claim, chunks_by_tag)

    assert verdict.status == "UNSUPPORTED"
    assert verdict.reason == "no cited chunk entails this claim"
    assert verdict.chunk_ids == [chunk_key(RISC_CHUNK.document_id, RISC_CHUNK.chunk_index)]


# ---------------------------------------------------------------------
# Path 5: any contradiction among cited chunks -> CONTRADICTED
# ---------------------------------------------------------------------


def test_contradicting_claim_is_contradicted_and_logs_warning(monkeypatch, caplog):
    claim = Claim(
        text="RISC architectures use more instructions than CISC architectures.",
        citations=["C1"],
    )
    chunks_by_tag = _tags(RISC_CHUNK)
    _patch_nli(monkeypatch, {claim.text: ("contradiction", 0.88)})

    with caplog.at_level(logging.WARNING, logger="app.domain.verification"):
        verdict = verify_claim(claim, chunks_by_tag)

    assert verdict.status == "CONTRADICTED"
    assert verdict.evidence_score == pytest.approx(0.88)
    assert "C1" in verdict.reason
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_contradiction_beats_unrelated_entailment_below_threshold(monkeypatch):
    """If one citation contradicts and another is weakly-entailed (below
    threshold), CONTRADICTED still wins over UNSUPPORTED per §6.2's ordering
    (entailment-above-threshold checked first, then contradiction, then
    fallback to UNSUPPORTED)."""
    claim = Claim(text="Mixed-signal claim.", citations=["C1", "C2"])
    chunks_by_tag = _tags(RISC_CHUNK, UNRELATED_CHUNK)
    _patch_nli(
        monkeypatch,
        {claim.text: ("contradiction", 0.7)},  # same fake fn used for both calls
    )

    verdict = verify_claim(claim, chunks_by_tag)

    assert verdict.status == "CONTRADICTED"


# ---------------------------------------------------------------------
# Batch entry point + tag-map construction
# ---------------------------------------------------------------------


def test_build_chunk_tag_map_orders_c1_first():
    tag_map = build_chunk_tag_map([RISC_CHUNK, UNRELATED_CHUNK])
    assert tag_map == {"C1": RISC_CHUNK, "C2": UNRELATED_CHUNK}


def test_verify_claims_batches_and_preserves_order(monkeypatch):
    claims = [
        Claim(text="entailed", citations=["C1"]),
        Claim(text="no citation", citations=[]),
        Claim(text="contradicted", citations=["C2"]),
    ]
    _patch_nli(
        monkeypatch,
        {"entailed": ("entailment", 0.9), "contradicted": ("contradiction", 0.9)},
    )

    verdicts = verify_claims(claims, [RISC_CHUNK, UNRELATED_CHUNK])

    assert [v.status for v in verdicts] == ["SUPPORTED", "UNSUPPORTED", "CONTRADICTED"]
    assert [v.text for v in verdicts] == ["entailed", "no citation", "contradicted"]


# ---------------------------------------------------------------------
# §6.3 response assembly
# ---------------------------------------------------------------------


def test_assemble_answer_text_keeps_only_supported_with_citations():
    verdicts = [
        ClaimVerdict(
            text="Kept sentence.",
            status="SUPPORTED",
            citations=["C1"],
            evidence_score=0.9,
            chunk_ids=["1:0"],
        ),
        ClaimVerdict(
            text="Dropped sentence.",
            status="UNSUPPORTED",
            citations=["C2"],
            evidence_score=0.1,
            chunk_ids=["2:5"],
            reason="no cited chunk entails this claim",
        ),
    ]

    text = assemble_answer_text(verdicts)

    assert text == "Kept sentence. [C1]"
    assert "Dropped sentence" not in text


def test_assemble_response_ok_state_when_some_supported():
    verdicts = [
        ClaimVerdict(
            text="Kept.",
            status="SUPPORTED",
            citations=["C1"],
            evidence_score=0.9,
            chunk_ids=["1:0"],
        ),
        ClaimVerdict(
            text="Rejected.",
            status="UNSUPPORTED",
            citations=[],
            evidence_score=None,
            chunk_ids=[],
            reason="no citation provided",
        ),
    ]

    response = assemble_response("What is RISC?", verdicts, ["1:0"], {"total": 12.3})

    assert response.state == "ok"
    assert response.answer == "Kept. [C1]"
    # Full audit trail retained, including the rejected claim and its reason.
    assert len(response.claims) == 2
    rejected = [c for c in response.claims if c.status == "UNSUPPORTED"]
    assert rejected[0].reason == "no citation provided"


def test_assemble_response_all_unsupported_is_insufficient_evidence_not_empty_string():
    verdicts = [
        ClaimVerdict(
            text="Rejected one.",
            status="UNSUPPORTED",
            citations=[],
            evidence_score=None,
            chunk_ids=[],
            reason="no citation provided",
        ),
        ClaimVerdict(
            text="Rejected two.",
            status="CONTRADICTED",
            citations=["C1"],
            evidence_score=0.8,
            chunk_ids=["1:0"],
            reason="cited chunk [C1] contradicts the claim",
        ),
    ]

    response = assemble_response("Unanswerable question?", verdicts, ["1:0"])

    assert response.state == "insufficient_evidence"
    assert response.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert response.answer != ""
    # Audit trail still carries both rejected claims with their reasons.
    assert {c.text for c in response.claims} == {"Rejected one.", "Rejected two."}


def test_assemble_response_no_claims_at_all_is_insufficient_evidence():
    response = assemble_response("Nothing generated?", [], [])
    assert response.state == "insufficient_evidence"
    assert response.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert response.claims == []


def test_assemble_response_contradicted_logs_warning(caplog):
    verdicts = [
        ClaimVerdict(
            text="Flipped sign.",
            status="CONTRADICTED",
            citations=["C1"],
            evidence_score=0.8,
            chunk_ids=["1:0"],
            reason="cited chunk [C1] contradicts the claim",
        ),
    ]
    with caplog.at_level(logging.WARNING, logger="app.domain.verification"):
        assemble_response("q", verdicts, ["1:0"])

    assert any(record.levelno == logging.WARNING for record in caplog.records)


# ---------------------------------------------------------------------
# Real-model integration checks (Option B validation).
#
# These load the actual ~400MB cross-encoder/nli-deberta-v3-base model and
# are here to validate that the mocked `predict_nli` behavior used above
# actually reflects reality: entailment for a genuinely-entailed claim,
# contradiction for a flipped-sign claim, neutral for an absent fact. They
# are separated out and marked so the fast/offline suite above can run in
# CI without a model download; skip automatically if the dependency isn't
# installed.
# ---------------------------------------------------------------------

sentence_transformers = pytest.importorskip(
    "sentence_transformers", reason="sentence-transformers not installed; real-model checks skipped"
)


# ---------------------------------------------------------------------
# PLAN.md OQ-2 — citation tags must not reach the NLI model.
#
# §5.2 has the generator end each factual sentence with the tags of the
# chunks it drew from, so `claim.text` arrives carrying "[C1]". That tag is
# metadata, not part of the proposition, and scoring it made the hypothesis
# a non-sentence: 6 of 21 false-rejection items were caused by nothing else.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("RISC uses fewer instructions [C1].", "RISC uses fewer instructions."),
        ("Revenue grew 12% [C2][C4].", "Revenue grew 12%."),
        ("A trailing tag with no period [C1]", "A trailing tag with no period"),
        ("Tagged mid-text [C1]. A second sentence.", "Tagged mid-text. A second sentence."),
        ("No tags at all.", "No tags at all."),
        ("[C1] Leading tag.", "Leading tag."),
    ],
)
def test_strip_citation_tags(text, expected):
    assert verify.strip_citation_tags(text) == expected


def test_tagged_and_untagged_claims_score_identically(monkeypatch):
    """The regression guard: the tag must not change what the model sees.

    Asserted on the hypothesis actually passed to `predict_nli` rather than
    on the verdict alone, because a verdict could match for unrelated
    reasons while the tag still reaches the model.
    """
    seen = []

    def fake_predict_nli(premise, hypothesis):
        seen.append(hypothesis)
        return "entailment", 0.99

    _use_scorer(fake_predict_nli)

    tagged = Claim(text="RISC uses fewer instructions than CISC [C1].", citations=["C1"])
    untagged = Claim(text="RISC uses fewer instructions than CISC.", citations=["C1"])
    chunks = {"C1": RISC_CHUNK}

    tagged_verdict = verify_claim(tagged, chunks)
    untagged_verdict = verify_claim(untagged, chunks)

    assert seen[0] == seen[1] == "RISC uses fewer instructions than CISC."
    assert tagged_verdict.status == untagged_verdict.status == "SUPPORTED"
    assert tagged_verdict.evidence_score == untagged_verdict.evidence_score


def test_the_verdict_keeps_the_tags_in_its_text(monkeypatch):
    # Scoring-side fix only. §6.4 serializes `ClaimVerdict.text`, and callers
    # already receive human-readable inline citations there; stripping them
    # from the response would be a contract change, not a bug fix.
    _use_scorer(lambda premise, hypothesis: ("entailment", 0.99))

    claim = Claim(text="RISC uses fewer instructions than CISC [C1].", citations=["C1"])
    verdict = verify_claim(claim, {"C1": RISC_CHUNK})

    assert verdict.text == "RISC uses fewer instructions than CISC [C1]."


@pytest.mark.slow
class TestNliLabelOrder:
    """Label order is a property of the checkpoint, never a constant.

    The three checkpoints measured for OQ-3 Direction D expose three different
    orders. A wrong order does not raise — it silently reports entailment as
    contradiction, inverting every verdict in the system while the mocked tests
    stay green. That failure mode is why these exist.
    """

    @staticmethod
    def _fake(id2label):
        return SimpleNamespace(model=SimpleNamespace(config=SimpleNamespace(id2label=id2label)))

    @pytest.mark.parametrize(
        "id2label, expected",
        [
            # cross-encoder/nli-deberta-v3-base (the previous incumbent)
            ({0: "contradiction", 1: "entailment", 2: "neutral"},
             ("contradiction", "entailment", "neutral")),
            # MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli (adopted)
            ({0: "entailment", 1: "neutral", 2: "contradiction"},
             ("entailment", "neutral", "contradiction")),
            # facebook/bart-large-mnli
            ({0: "contradiction", 1: "neutral", 2: "entailment"},
             ("contradiction", "neutral", "entailment")),
            # Casing and decoration vary between publishers.
            ({0: "ENTAILMENT", 1: "NEUTRAL", 2: "CONTRADICTION"},
             ("entailment", "neutral", "contradiction")),
        ],
    )
    def test_order_is_read_from_the_checkpoint(self, id2label, expected):
        assert nli._derive_labels(self._fake(id2label)) == expected

    def test_unrecognised_head_raises_rather_than_guessing(self):
        # A positional guess here is exactly how the silent inversion happens.
        with pytest.raises(RuntimeError, match="not one of entailment"):
            nli._derive_labels(self._fake({0: "LABEL_0", 1: "LABEL_1", 2: "LABEL_2"}))

    def test_non_three_way_head_raises(self):
        with pytest.raises(RuntimeError, match="3-way NLI head"):
            nli._derive_labels(self._fake({0: "entailment", 1: "neutral"}))


class TestRealNliModel:
    def test_derived_label_order_matches_the_adopted_checkpoint(self):
        # Guards the swap itself: if NLI_MODEL_NAME changes without this being
        # re-derived, the assertion below catches it before any verdict does.
        nli.get_nli_model()
        assert nli._nli_labels == ("entailment", "neutral", "contradiction")

    def test_model_is_float32_not_the_published_float16(self):
        # fp16 is emulated on x86: ~6x slower for byte-identical scores.
        import torch

        model = nli.get_nli_model()
        assert next(model.model.parameters()).dtype is torch.float32

    def test_long_multi_topic_premise_still_entails_a_verbatim_sentence(self):
        """OQ-3's regression test, and the reason the checkpoint was swapped.

        The premise bundles several unrelated topics and the hypothesis is a
        sentence taken from inside it. The previous checkpoint returned
        *confident* neutral (0.994) on exactly this shape.
        """
        from app.infrastructure.ml.nli import predict_nli

        premise = (
            "4. Maintainability: Deals with support and maintenance to the end user or client "
            "in case of technical issues and product failures or on the basis of a routine "
            "system checkup. Reliability and maintainability are complementary to each other. "
            "5. Security: Confidentiality, Integrity and Availability are the three major "
            "measures of information security. Confidentiality deals with protection of data "
            "and application from unauthorized disclosure. Integrity deals with the protection "
            "of data and application from unauthorized modification. Availability deals with "
            "protection of data and application from unauthorized users. "
            "6. Safety: Deals with the possible damages that can happen to the operators, "
            "public and the environment due to the breakdown of an embedded system."
        )
        hypothesis = (
            "Confidentiality deals with protection of data and application from "
            "unauthorized disclosure."
        )
        label, score = predict_nli(premise=premise, hypothesis=hypothesis)
        assert label == "entailment"
        assert score >= settings.claim_verification_threshold

    def test_entailed_claim_scores_entailment(self):
        from app.infrastructure.ml.nli import predict_nli

        label, score = predict_nli(
            premise=RISC_CHUNK.text,
            hypothesis="RISC architectures use fewer instructions than CISC architectures.",
        )
        assert label == "entailment"
        assert score >= settings.claim_verification_threshold

    def test_contradicting_claim_scores_contradiction(self):
        from app.infrastructure.ml.nli import predict_nli

        label, score = predict_nli(
            premise=RISC_CHUNK.text,
            hypothesis="RISC architectures use more instructions than CISC architectures.",
        )
        assert label == "contradiction"

    def test_unrelated_claim_scores_neutral(self):
        from app.infrastructure.ml.nli import predict_nli

        label, score = predict_nli(
            premise=RISC_CHUNK.text,
            hypothesis="The company reported record profits in Q3.",
        )
        assert label == "neutral"

    def test_verify_claim_end_to_end_with_real_model(self):
        """Full verify_claim() path with the real model, no stubbing at all.

        Builds its own verifier around `predict_nli` rather than using this
        module's wrappers, which are wired to whatever stub the last test
        installed. That distinction is the point of these two tests — they are
        the only ones here that exercise the real scorer.
        """
        from app.infrastructure.ml.nli import predict_nli

        claim = Claim(
            text="RISC architectures use fewer instructions than CISC architectures.",
            citations=["C1"],
        )
        verdict = ClaimVerifier(scorer=predict_nli).verify_claim(
            claim, build_chunk_tag_map([RISC_CHUNK])
        )
        assert verdict.status == "SUPPORTED"

    def test_verify_claim_contradiction_end_to_end_with_real_model(self):
        from app.infrastructure.ml.nli import predict_nli

        claim = Claim(
            text="RISC architectures use more instructions than CISC architectures.",
            citations=["C1"],
        )
        verdict = ClaimVerifier(scorer=predict_nli).verify_claim(
            claim, build_chunk_tag_map([RISC_CHUNK])
        )
        assert verdict.status == "CONTRADICTED"


# ---------------------------------------------------------------------
# OQ-3R: long-premise fallback
# ---------------------------------------------------------------------


class TestLongPremiseFallback:
    """Narrowed retry on long premises the verifier already declined.

    The invariant that makes this safe: the full premise is always scored
    first and its verdict stands unless a *narrower* window entails. So the
    fallback can only add entailments, never remove one, and CONTRADICTED
    keeps seeing the whole premise.
    """

    SHORT = "A short premise well under the gate."
    LONG = "Sentence one. " * 200  # ~2800 chars

    def test_short_premise_never_windows(self, monkeypatch):
        calls = []

        def fake(premise, hypothesis):
            calls.append(premise)
            return ("neutral", 0.9)

        _use_scorer(fake)
        label, score = score_with_long_premise_fallback(self.SHORT, "h")

        assert (label, score) == ("neutral", 0.9)
        assert calls == [self.SHORT]  # exactly one call, no windows

    def test_entailing_full_premise_never_windows(self, monkeypatch):
        calls = []

        def fake(premise, hypothesis):
            calls.append(premise)
            return ("entailment", 0.8)

        _use_scorer(fake)
        assert score_with_long_premise_fallback(self.LONG, "h") == ("entailment", 0.8)
        assert len(calls) == 1  # short-circuits before any window is built

    def test_window_entailment_wins_over_full_premise_neutral(self, monkeypatch):
        def fake(premise, hypothesis):
            return ("neutral", 0.99) if premise == self.LONG else ("entailment", 0.93)

        _use_scorer(fake)
        assert score_with_long_premise_fallback(self.LONG, "h") == ("entailment", 0.93)

    def test_best_window_wins_not_the_first(self, monkeypatch):
        scores = iter([0.61, 0.97, 0.72])

        def fake(premise, hypothesis):
            if premise == self.LONG:
                return ("neutral", 0.99)
            return ("entailment", next(scores, 0.5))

        _use_scorer(fake)
        _, score = score_with_long_premise_fallback(self.LONG, "h")
        assert score == pytest.approx(0.97)

    def test_no_entailing_window_leaves_the_original_verdict(self, monkeypatch):
        _use_scorer(lambda premise, hypothesis: ("neutral", 0.88))
        assert score_with_long_premise_fallback(self.LONG, "h") == ("neutral", 0.88)

    def test_contradiction_is_not_overridden_by_a_neutral_window(self, monkeypatch):
        # A contradiction the whole chunk establishes must not be downgraded
        # because one sentence in isolation looks merely unrelated.
        def fake(premise, hypothesis):
            return ("contradiction", 0.95) if premise == self.LONG else ("neutral", 0.99)

        _use_scorer(fake)
        assert score_with_long_premise_fallback(self.LONG, "h") == ("contradiction", 0.95)

    def test_gate_is_the_corpus_tail_not_a_general_strategy(self):
        # 2200 covers 4 of 42 chunks. Dropping it materially turns this into
        # Direction C, whose compute cost is why C does not ship.
        assert verify.LONG_PREMISE_CHARS == 2200

    def test_stride_cuts_window_count_without_dropping_coverage(self):
        text = " ".join(f"Sentence number {i}." for i in range(60))
        windows = verify._windows(text)
        units = [u for u in verify._SENTENCE_SPLIT.split(text) if u.strip()]

        # Stride 3: roughly a third as many windows as units...
        assert len(windows) <= len(units) // 2
        # ...but every unit still appears in at least one window.
        assert all(any(unit in w for w in windows) for unit in units)


# ---------------------------------------------------------------------
# OQ-5: refusal is a distinct claim kind, never scored as evidence
# ---------------------------------------------------------------------


class TestRefusalClaims:
    """A refusal describes the *context*, not the world.

    Before `kind` existed, a refusal was an ordinary claim that happened to
    land in UNSUPPORTED because the model usually left it uncited. When the
    model cited one, NLI entailed it (0.904 observed) and "I cannot answer
    this" became supporting evidence, taking `state` to "ok". These tests pin
    the structural fix, not the accident.
    """

    REFUSAL = Claim(
        kind="refusal",
        text="The context does not specify the release year.",
        citations=["C1"],
    )

    def test_refusal_is_never_sent_to_nli(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("a refusal must never be scored against evidence")

        _use_scorer(boom)
        verdict = verify_claim(self.REFUSAL, _tags(RISC_CHUNK))

        assert verdict.status == "REFUSAL"
        assert verdict.evidence_score is None
        assert verdict.reason is None

    def test_cited_refusal_cannot_become_supported(self, monkeypatch):
        # The exact observed failure: the model cites its own refusal and the
        # verifier entails it at 0.904.
        _patch_nli(monkeypatch, {self.REFUSAL.text: ("entailment", 0.904)})
        assert verify_claim(self.REFUSAL, _tags(RISC_CHUNK)).status == "REFUSAL"

    def test_refusal_keeps_its_chunk_ids_for_the_audit_trail(self, monkeypatch):
        _use_scorer(lambda **k: ("neutral", 0.0))
        verdict = verify_claim(self.REFUSAL, _tags(RISC_CHUNK))
        assert verdict.chunk_ids == [chunk_key(RISC_CHUNK.document_id, RISC_CHUNK.chunk_index)]

    def test_uncited_refusal_is_still_a_refusal_not_unsupported(self, monkeypatch):
        # The old code reached "no citation provided" first. Kind wins now, so
        # the classification no longer depends on citation behaviour at all.
        _use_scorer(lambda **k: ("neutral", 0.0))
        claim = Claim(kind="refusal", text="The context does not address this.", citations=[])
        verdict = verify_claim(claim, _tags(RISC_CHUNK))
        assert verdict.status == "REFUSAL"
        assert verdict.chunk_ids == []

    def test_assertion_still_verifies_exactly_as_before(self, monkeypatch):
        claim = Claim(text="RISC uses fewer instructions than CISC.", citations=["C1"])
        _patch_nli(monkeypatch, {claim.text: ("entailment", 0.93)})
        assert verify_claim(claim, _tags(RISC_CHUNK)).status == "SUPPORTED"

    def test_claim_defaults_to_assertion(self):
        assert Claim(text="x", citations=[]).kind == "assertion"


class TestRefusalAssembly:
    def _verdict(self, status, text, score=None):
        return ClaimVerdict(
            text=text, status=status, citations=[], evidence_score=score, chunk_ids=[]
        )

    def test_refusal_alone_does_not_make_state_ok(self):
        r = assemble_response("q", [self._verdict("REFUSAL", "Not specified.")], [])
        assert r.state == "insufficient_evidence"

    def test_refusal_text_reaches_the_answer(self):
        # The whole point: without this the reader cannot tell they were given
        # something adjacent to what they asked for.
        r = assemble_response("q", [self._verdict("REFUSAL", "No mid-range figure is given.")], [])
        assert r.answer == "No mid-range figure is given."

    def test_supported_claim_plus_refusal_shows_both(self):
        verdicts = [
            self._verdict("SUPPORTED", "The largest FPGA provides eight million gates.", 0.9),
            self._verdict("REFUSAL", "No mid-range figure is given."),
        ]
        r = assemble_response("q", verdicts, [])
        assert r.state == "ok"
        assert "eight million gates" in r.answer
        assert "No mid-range figure is given." in r.answer

    def test_refusals_are_not_listed_as_rejected_claims(self):
        # Contract change: a refusal is not a filtered-out claim. Listing it
        # beside genuine rejections misreports what happened.
        verdicts = [
            self._verdict("REFUSAL", "Not specified."),
            self._verdict("UNSUPPORTED", "Something unsupported."),
        ]
        r = assemble_response("q", verdicts, [])
        assert [c.text for c in r.rejected_claims] == ["Something unsupported."]
        assert [c.text for c in r.refusals] == ["Not specified."]

    def test_no_claims_at_all_still_uses_the_generic_message(self):
        assert assemble_response("q", [], []).answer == INSUFFICIENT_EVIDENCE_MESSAGE
