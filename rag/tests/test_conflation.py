"""Unit tests for `app.domain.conflation` — the identity/composition guard.

Two things are being pinned here, and they pull in opposite directions.

**Recall**, in `TestDetectsRealConflations`: the premises are verbatim from
`data.pdf` and each is annotated with the entailment score the real NLI model
gave it. Those numbers are why this module exists, and a change that stops
flagging them has reintroduced the bug regardless of what the rest of the
suite says.

**Precision**, in `TestDoesNotFlag`: the guard suppresses evidence, so a false
positive silently turns a correct SUPPORTED verdict into an UNSUPPORTED one —
a strictly worse failure than the one it fixes, because a caller has no way to
see that a true statement was refused on a technicality. Every case there is a
claim the corpus genuinely supports.

Pure string inspection, so this whole file is offline and instant. No model,
no datastore, no fixtures beyond the corpus text inlined below.
"""

from __future__ import annotations

from app.domain.conflation import detect_composition_mismatch

# ---------------------------------------------------------------------
# Corpus text, verbatim from data.pdf (document 1). The chunk index and the
# real NLI score are in the name or the comment of each test that uses them,
# so a failure points at a measurement rather than at an invented example.
# ---------------------------------------------------------------------

# 1:5 — a linearized table row. Scores entailment 0.987 for "embedded system
# is an operating system", the highest score in the retrieved set and the one
# that produced the original SUPPORTED verdict.
TABLE_ROW_1 = (
    "Table 1.1: Embedded Systems vs. General Computing Systems Row 1 | "
    "Embedded Systems: A system which is a combination of special purpose "
    "hardware and embedded OS for executing a specific set of applications | "
    "General Computing Systems: A system which is a combination of generic "
    "hardware and General Purpose Operating System for executing a variety "
    "of applications"
)

# 1:6 — scores entailment 0.685 on the same hypothesis. Note "May or may not",
# which is about as weak as a composition claim gets and still entails.
TABLE_ROW_2 = (
    "Table 1.1: Embedded Systems vs. General Computing Systems Row 2 | "
    "Embedded Systems: May or may not contain an operating system for "
    "functioning | General Computing Systems: Contain a General Purpose "
    "Operating System (GPOS)"
)

# 1:3 — the prose definition. Scores *contradiction* 0.802 on the same
# hypothesis, and is the chunk whose verdict the guard lets through.
PROSE_DEFINITION = (
    "Unit 1 Introduction to Embedded Systems An embedded system is a "
    "combination of hardware and software with some attached peripherals to "
    "perform a specific task or a narrow range of tasks with restricted "
    "resources. It is an electronic system that is not directly programmed "
    "by the user, unlike a personal computer. An embedded system is a device "
    "that incorporates a computer within its implementation, primarily as a "
    "means to simplify the system design, and to provide flexibility."
)


class TestDetectsRealConflations:
    """The measured failures. Each of these was scored as entailment."""

    def test_flags_combination_of_hardware_and_embedded_os(self):
        """1:5, entailment 0.987 — the verdict that started this."""
        found = detect_composition_mismatch(
            TABLE_ROW_1, "embedded system is an operating system"
        )
        assert found is not None
        assert found.subject == "embedded system"
        assert found.predicate == "operating system"
        assert found.verb == "combination of"

    def test_flags_may_or_may_not_contain_an_operating_system(self):
        """1:6, entailment 0.685 — below the 0.85 gate originally proposed."""
        found = detect_composition_mismatch(
            TABLE_ROW_2, "embedded system is an operating system"
        )
        assert found is not None
        assert found.verb == "contain"

    def test_matches_the_alias_not_just_the_literal_predicate(self):
        """1:5 spells the predicate "embedded OS", never "operating system".

        This is the single reason the highest-scoring offender is caught at
        all, and it is the module's most fragile assumption — see `_ALIASES`.
        A regression here reads as "the guard works" everywhere except on the
        one pair that mattered.
        """
        assert "operating system" not in TABLE_ROW_1.split("|")[1]
        assert detect_composition_mismatch(
            TABLE_ROW_1, "embedded system is an operating system"
        )

    def test_binds_the_subject_to_its_own_table_column(self):
        """Both columns describe an OS; the finding must name the right row.

        Without the pipe split the subject in one column binds to a predicate
        in the other, and the reported segment describes a system the caller
        never asked about.
        """
        found = detect_composition_mismatch(
            TABLE_ROW_2, "a general computing system is an operating system"
        )
        assert found is not None
        assert "general computing system" in found.segment
        assert "may or may not" not in found.segment

    def test_flags_built_around_a_microcontroller(self):
        """1:13, entailment 0.664. Built *around* a microcontroller, not one."""
        premise = (
            "The early embedded systems built around 8-bit microprocessors "
            "like 8085 and Z80 and 4-bit microcontrollers."
        )
        assert detect_composition_mismatch(premise, "an embedded system is a microcontroller")

    def test_normalizes_plurals_and_case(self):
        """The corpus says "Embedded Systems"; a caller writes "embedded system"."""
        assert detect_composition_mismatch(
            "EMBEDDED SYSTEMS contain an Operating System.",
            "an embedded system is an operating system",
        )


class TestDoesNotFlag:
    """Precision. Every claim here is one the corpus genuinely supports."""

    def test_composition_predicate_is_skipped_outright(self):
        """The measured false positive: correctly SUPPORTED at 0.995.

        The hypothesis asserts composition itself, so there is no identity to
        confuse it with. Guarded by predicate shape rather than by the premise,
        so it holds against *any* premise — which is what makes it reliable.
        """
        for premise in (PROSE_DEFINITION, TABLE_ROW_1, TABLE_ROW_2):
            assert (
                detect_composition_mismatch(
                    premise, "an embedded system is a combination of hardware and software"
                )
                is None
            )

    def test_premise_asserting_the_identity_outright_is_not_flagged(self):
        """"An embedded system is a device that incorporates a computer…"

        The same sentence contains a composition verb ("incorporates") and the
        identity. The identity wins, and must, or the guard rejects the
        corpus's own definitions.
        """
        assert (
            detect_composition_mismatch(PROSE_DEFINITION, "an embedded system is a device") is None
        )

    def test_property_predication_is_not_an_identity_claim(self):
        """"are pre-programmed" has no article, so it is not "is a kind of"."""
        assert (
            detect_composition_mismatch(
                "The firmware of the embedded system is pre-programmed.",
                "embedded systems are pre-programmed",
            )
            is None
        )

    def test_composition_hypothesis_is_left_alone(self):
        """A caller asserting composition is asking the right question."""
        assert (
            detect_composition_mismatch(
                TABLE_ROW_2, "an embedded system has an operating system"
            )
            is None
        )

    def test_question_shaped_input_is_not_an_identity_claim(self):
        assert detect_composition_mismatch(TABLE_ROW_1, "what is an operating system?") is None

    def test_absent_subject_does_not_bind(self):
        """The premise must attach the predicate to *this* subject."""
        assert (
            detect_composition_mismatch(
                "A washing machine contains an operating system.",
                "an embedded system is an operating system",
            )
            is None
        )

    def test_unrelated_predicate_does_not_bind(self):
        assert (
            detect_composition_mismatch(
                TABLE_ROW_2, "an embedded system is a quarterly board meeting"
            )
            is None
        )

    def test_subject_starting_with_a_is_not_decapitated(self):
        """"apples are a fruit" — the leading-article match needs a boundary.

        A bare `(?:a|an|the)?` prefix eats the "a" of "apples" and leaves the
        subject as "pples", which then binds to nothing. Cheap to get wrong and
        invisible except as silent under-detection.
        """
        found = detect_composition_mismatch("Apples contain a fruit sugar.", "apples are a fruit")
        # Singularized to "apple" by `_normalize`, which is the point of that
        # function; what must not happen is the leading "a" being eaten as an
        # article, leaving "pples".
        assert found is not None
        assert found.subject == "apple"
