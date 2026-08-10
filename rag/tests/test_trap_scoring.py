"""D1/D4 trap scoring (PLAN.md OQ-4).

These lock the two rules that were learned from real misfires rather than
reasoned out in advance — citation-tag stripping and the negation guard — plus
the property that makes D1 defensible at all: it grades what was *asserted*,
not what survived. Every claim string below that is labelled "true" is real
text from the 2026-08-03 runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import trap_scoring

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
ADVERSARIAL_SET = EVAL_DIR / "adversarial_set.jsonl"


@pytest.fixture(scope="module")
def items():
    return [json.loads(line) for line in ADVERSARIAL_SET.read_text().splitlines() if line.strip()]


def record(item_id, supported=(), rejected=(), state="ok", category="unsupported_combination"):
    return {
        "id": item_id,
        "category": category,
        "expected_state": "insufficient_evidence",
        "actual_state": state,
        "supported": [{"text": t, "citations": ["C1"]} for t in supported],
        "rejected": [{"text": t} for t in rejected],
        "error": None,
    }


# --- the two matching rules --------------------------------------------------


def test_citation_tag_does_not_satisfy_a_digit_pattern():
    # THE bug this module exists to not repeat: `[C1]` contains a `1`, so a
    # bare \d matched every cited claim and two traps were silently reduced to
    # two-token patterns. OQ-2's defect, one layer up.
    assert not trap_scoring.asserts_trap("CoolRunner CPLDs have many gates [C1].", [["\\d"]])


def test_a_real_digit_still_matches():
    assert trap_scoring.asserts_trap("CoolRunner CPLDs offer 10,000 gates [C1].", [["\\d"]])


def test_negation_marker_suppresses_the_trap():
    # A supported claim that DENIES the proposition is correct behavior.
    groups = [["Paris"]]
    assert trap_scoring.asserts_trap("The capital of France is Paris [C1].", groups)
    assert not trap_scoring.asserts_trap(
        "The context does not specify that the capital of France is Paris.", groups
    )


def test_all_patterns_in_a_group_must_match():
    groups = [["ARM", "DSP"]]
    assert trap_scoring.asserts_trap("An ARM-based DSP is fast [C1].", groups)
    assert not trap_scoring.asserts_trap("ARM processors are low power [C1].", groups)


def test_groups_are_alternatives():
    groups = [["Kubernetes"], ["container orchestration"]]
    assert trap_scoring.asserts_trap("The document describes container orchestration [C1].", groups)


def test_matching_is_case_insensitive():
    assert trap_scoring.asserts_trap("the tms 1000 shipped in 1974 [C1].", [["TMS\\s*1000"]])


# --- what counts as a false support ------------------------------------------


def test_only_supported_claims_can_fire_a_trap():
    # A rejected claim asserting the bait is the system WORKING: generation
    # proposed it, verification caught it. Counting that as a false support is
    # what the old metric effectively did.
    trap = {"groups": [["Paris"]]}
    r = record("adv-015", supported=(), rejected=["The capital of France is Paris."])
    assert not trap_scoring.item_asserts_trap(r, trap)


def test_true_claim_beside_a_refusal_is_not_a_false_support(items):
    # adv-011's real observed output — the exact case that made the old metric
    # read 0.312 while the system had asserted nothing unsupported.
    trap = next(i for i in items if i["id"] == "adv-011")["trap"]
    r = record(
        "adv-011",
        supported=[
            "Embedded systems are described as safety-critical, meaning they must not "
            "endanger human life and the environment [C3].",
            "Application Specific Integrated Circuits (ASICs) reduce system development "
            "cost by integrating several functions into a single chip [C2].",
        ],
    )
    assert not trap_scoring.item_asserts_trap(r, trap)


def test_a_claim_actually_asserting_the_bait_is_caught(items):
    trap = next(i for i in items if i["id"] == "adv-011")["trap"]
    r = record("adv-011", supported=["Using an ASIC makes an embedded system safer [C1]."])
    assert trap_scoring.item_asserts_trap(r, trap)


# --- D4 ----------------------------------------------------------------------


def test_d4_credits_an_explicit_refusal():
    trap = {"groups": [["Paris"]]}
    r = record("adv-015", rejected=["No information about France."], state="insufficient_evidence")
    assert trap_scoring.bait_rejected(r, trap)


def test_d4_does_not_credit_an_item_that_asserted_the_bait():
    trap = {"groups": [["Paris"]]}
    r = record("adv-015", supported=["The capital of France is Paris [C1]."], rejected=["x"])
    assert not trap_scoring.bait_rejected(r, trap)


def test_d4_does_not_credit_silence():
    # Neither a refusal nor a rejected claim: the system said nothing at all.
    trap = {"groups": [["Paris"]]}
    assert not trap_scoring.bait_rejected(record("adv-015", state="ok"), trap)


# --- the bridge --------------------------------------------------------------


def test_both_numbers_come_from_one_pass_over_the_same_records(items):
    # A redefined primary metric reported without the number it replaced, on
    # identical data, is not something a reader can check — and this
    # redefinition takes the figure to zero.
    trap_011 = next(i for i in items if i["id"] == "adv-011")
    records = [
        record(
            "adv-011",
            supported=["Embedded systems are described as safety-critical [C3]."],
            rejected=["Using an ASIC makes a system safer."],
        )
    ]
    result = trap_scoring.score(records, [trap_011])

    assert result["d1_rate"] == 0.0
    assert result["old_rate"] == 1.0
    assert result["old_items"] == ["adv-011"]
    assert result["d1_items"] == []


def test_errored_items_are_excluded_from_both_numbers(items):
    trap_011 = next(i for i in items if i["id"] == "adv-011")
    r = record("adv-011")
    r["error"] = "429 rate limit"
    assert trap_scoring.score([r], [trap_011])["n"] == 0


# --- archival records --------------------------------------------------------


def test_scores_the_pre_2026_08_03_record_schema():
    # Runs before 2026-08-03 stored `supported` as bare strings, not dicts
    # carrying the evidence trail. Rescoring archival runs is the whole reason
    # --rescore exists, so a scorer that only reads records written after
    # itself cannot re-derive a single historical number.
    trap = {"groups": [["Paris"]]}
    old = {"id": "adv-015", "supported": ["The capital of France is Paris [C1]."]}
    new = {"id": "adv-015", "supported": [{"text": "The capital of France is Paris [C1]."}]}

    assert trap_scoring.item_asserts_trap(old, trap)
    assert trap_scoring.item_asserts_trap(new, trap)


def test_claim_texts_handles_both_schemas_and_empties():
    assert trap_scoring.claim_texts(["a", {"text": "b"}]) == ["a", "b"]
    assert trap_scoring.claim_texts([]) == []


def test_persisted_baseline_run_still_reproduces_its_numbers(items):
    # The 2026-08-02 baseline, persisted so the bridge table's row for it is
    # derivable from the repo instead of from a transcript. If this drifts, the
    # published row is wrong.
    path = EVAL_DIR / "results" / "records-2026-08-02-baseline.json"
    records = json.loads(path.read_text())
    result = trap_scoring.score(records, items)

    assert result["n"] == 16
    assert result["old_rate"] == pytest.approx(0.3125)
    assert result["old_items"] == ["adv-001", "adv-007", "adv-008", "adv-009", "adv-011"]
    assert result["d1_rate"] == 0.0
    assert result["d4_rate"] == 1.0


# --- golden-set integrity ----------------------------------------------------


def test_every_adversarial_item_carries_a_trap(items):
    missing = [i["id"] for i in items if "trap" not in i]
    assert not missing, f"items with no trap are invisible to D1: {missing}"


def test_every_trap_has_a_written_proposition(items):
    assert all(i["trap"].get("proposition") for i in items)


def test_trap_patterns_all_compile(items):
    import re

    for i in items:
        for group in i["trap"]["groups"]:
            for pattern in group:
                re.compile(pattern)


def test_the_blind_items_are_the_adjacent_absent_category(items):
    # If this drifts, `report_d1`'s caveat is pointing at the wrong items.
    blind = {i["id"] for i in items if i.get("d1_blind")}
    adjacent = {i["id"] for i in items if i["category"] == "adjacent_absent"}
    assert blind == adjacent


def test_blind_items_are_reported_not_folded_into_a_clean_number(items, capsys):
    result = trap_scoring.score([record("adv-002", category="adjacent_absent")], items)
    trap_scoring.report_d1(result)
    out = capsys.readouterr().out
    assert "d1_blind" in out
    assert "silence, not evidence of health" in out
