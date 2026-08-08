"""D1 / D4 scoring for the adversarial set (PLAN.md OQ-4).

**What D1 replaces and why.** The old primary metric was `state == "ok"` — an
adversarial item counted as a false support if *any* claim survived
verification. That asks "did any supported claim survive", not "did the system
assert something unsupported", and the two came apart badly: in 5 flagged items
the baited combination was explicitly rejected and what flipped `state` was a
separate true, cited, on-topic claim standing beside the refusal. The metric was
counting correct behavior as a trust failure.

D1 scores the proposition instead. Each adversarial item carries a `trap`: the
specific bad assertion it was authored to bait. The item counts as a false
support only when a **SUPPORTED** claim actually asserts that proposition.

**Matching is explicit regex and nothing else** — never NLI, never model-judged
equivalence. The constraint is that the model under test must not sit inside the
metric that grades it, or a verification change moves the score through the
matching path instead of through the verifier. If a trap cannot be matched with
explicit logic, the trap gets rewritten more precisely; the matcher never gets
fuzzier.

Two matching rules exist because both were learned from real misfires:

- **Citation tags are stripped first.** `[C1]` contains a `1`, so a bare `\\d`
  is satisfied on any cited claim and constrains nothing at all. Two traps
  relied on one and were silently reduced to two-token matches. This is OQ-2's
  defect — tags participating invisibly in scoring — recurring one layer up.
- **A negation marker suppresses the trap.** A supported claim that *denies*
  the baited proposition is correct behavior, not an assertion of it.

**D1 has a known blind spot, and it is structural.** The `adjacent_absent`
items bait answer *substitution*: the corpus holds a figure adjacent to the one
asked for. Where the substituted figure is itself false, the trap catches it.
Where it is true and cited — `adv-002` answering a mid-range FPGA question with
the largest FPGA's real figure — the claim is false only *relative to the
question*, and that relation is not a property of the claim. No claim-level
regex can express it, and widening a trap until it did would mean firing on a
true proposition, which is the one thing that would make D1 indefensible. Those
items are flagged `d1_blind` in the golden set and **a zero on them is silence,
not health**; `report_d1` prints them as such rather than folding them into a
clean number.

D4 (bait-rejected rate) is reported alongside permanently, not just through the
transition, precisely because of that blind spot: D1 read 0.000 on both measured
runs and so has no headroom to discriminate between them.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

# Stripped before matching — see the module docstring. `[C1]`, `[C12]`.
CITATION_TAG = re.compile(r"\[C\d+\]")

# A supported claim carrying one of these denies the trap rather than asserting
# it. Substring match on the lowercased, tag-stripped claim.
NEGATION_MARKERS: tuple[str, ...] = (
    "does not",
    "do not",
    "did not",
    "not specify",
    "not specified",
    "not mention",
    "not state",
    "not provide",
    "not contain",
    "not address",
    "no information",
    "cannot",
    "does n't",
    "doesn't",
    "insufficient evidence",
)


def asserts_trap(claim_text: str, groups: list[list[str]]) -> bool:
    """True when `claim_text` asserts the trap proposition.

    A trap fires when the claim matches *every* pattern in *any one* group;
    groups are alternative phrasings of the same bad assertion.
    """
    text = CITATION_TAG.sub("", claim_text)
    if any(marker in text.lower() for marker in NEGATION_MARKERS):
        return False
    return any(
        all(re.search(pattern, text, re.IGNORECASE) for pattern in group) for group in groups
    )


def claim_texts(claims: Iterable[Any]) -> list[str]:
    """Claim text from either record schema.

    Runs before 2026-08-03 stored `supported` as a list of bare strings; the
    current format stores dicts carrying the evidence trail (`citations`,
    `chunk_ids`, `evidence_score`) so a false rejection can be investigated.
    Both must score, because rescoring *archival* runs is the entire reason
    `--rescore` exists — a scorer that only reads records written after it was
    itself written cannot re-derive a single historical number.
    """
    return [c if isinstance(c, str) else c["text"] for c in claims]


def item_asserts_trap(record: dict[str, Any], trap: dict[str, Any]) -> bool:
    """True when any SUPPORTED claim in `record` asserts `trap`.

    Only supported claims count. A rejected claim that asserts the bait is the
    system working: generation proposed it and verification caught it.
    """
    groups = trap["groups"]
    return any(asserts_trap(text, groups) for text in claim_texts(record.get("supported", [])))


def bait_rejected(record: dict[str, Any], trap: dict[str, Any]) -> bool:
    """D4: the system visibly declined the bait on this item.

    True when the trap is not asserted by any supported claim *and* the system
    explicitly refused — i.e. emitted at least one `kind: "refusal"` claim.

    **Reads the explicit signal since OQ-5 (2026-08-03).** It used to accept
    "any rejected claim exists, or the state is insufficient_evidence" as proof
    of a refusal, which was an inference, not a measurement: it held only
    because the model usually left refusals uncited, so they fell into
    `rejected` by accident. When the model cited a refusal, the verifier
    entailed it as evidence and D4 read the item as never having refused —
    the metric inverted on exactly the case it exists to catch.

    Records written before that change carry no `refusals` key. For those the
    old inference is used, so historical runs still rescore — rescoring
    archival records is what `--rescore` exists for — but it is a
    reconstruction of the old behaviour, not the current definition.
    """
    if item_asserts_trap(record, trap):
        return False
    if "refusals" in record:
        return bool(record["refusals"])
    # Legacy path: pre-OQ-5 records, scored the way they were scored then.
    return record.get("actual_state") == "insufficient_evidence" or bool(record.get("rejected"))


def score(records: Iterable[dict], items: Iterable[dict]) -> dict[str, Any]:
    """Compute D1, D4 and the old `state == "ok"` bridge number together.

    All three come back from one pass so they are always reported on identical
    data. Reporting a redefined primary metric without the number it replaced,
    measured on the same run, is not something a reader can check.
    """
    traps = {i["id"]: i["trap"] for i in items if "trap" in i}
    blind = {i["id"] for i in items if i.get("d1_blind")}

    scored = [
        r
        for r in records
        if r.get("error") is None
        and r.get("expected_state") == "insufficient_evidence"
        and r["id"] in traps
    ]
    n = len(scored)

    d1_hits = [r for r in scored if item_asserts_trap(r, traps[r["id"]])]
    old_hits = [r for r in scored if r.get("actual_state") == "ok"]
    d4_hits = [r for r in scored if bait_rejected(r, traps[r["id"]])]

    return {
        "n": n,
        "d1_rate": len(d1_hits) / n if n else 0.0,
        "d1_items": [r["id"] for r in d1_hits],
        "old_rate": len(old_hits) / n if n else 0.0,
        "old_items": [r["id"] for r in old_hits],
        "d4_rate": len(d4_hits) / n if n else 0.0,
        "by_category": {
            category: {
                "d1": sum(1 for r in d1_hits if r.get("category") == category),
                "old": sum(1 for r in old_hits if r.get("category") == category),
                "total": total,
                "blind": sum(1 for r in scored if r.get("category") == category and r["id"] in blind),
            }
            for category, total in Counter(r.get("category") for r in scored).items()
        },
        "blind_ids": sorted(i for i in blind if any(r["id"] == i for r in scored)),
    }


def report_d1(result: dict[str, Any]) -> None:
    """Print D1 as primary with the old number beside it, and the caveats."""
    n = result["n"]
    if not n:
        return

    print(f"\nFALSE-SUPPORT RATE — D1 (primary): {result['d1_rate']:.3f}  "
          f"({len(result['d1_items'])}/{n} items asserted their trap)")
    print(f"  bridge — old metric (state == 'ok'): {result['old_rate']:.3f}  "
          f"({len(result['old_items'])}/{n})")
    print(f"  D4 bait-rejected rate:               {result['d4_rate']:.3f}")

    if result["d1_items"]:
        print(f"  D1 flagged: {', '.join(result['d1_items'])}")
    reclassified = sorted(set(result["old_items"]) - set(result["d1_items"]))
    if reclassified:
        print(
            f"  counted by the old metric, NOT by D1: {', '.join(reclassified)}\n"
            f"    (a true, cited claim survived beside an explicit refusal of the bait)"
        )

    print("\n  by category:")
    for category, c in sorted(result["by_category"].items()):
        blind = f"  [{c['blind']}/{c['total']} d1_blind]" if c["blind"] else ""
        print(f"    {category}: D1 {c['d1']}/{c['total']}   old {c['old']}/{c['total']}{blind}")

    if result["blind_ids"]:
        print(
            f"\n  ! {len(result['blind_ids'])} items are d1_blind "
            f"({', '.join(result['blind_ids'])}).\n"
            f"    D1 cannot express their primary failure mode — answer substitution\n"
            f"    with a figure that is itself true and cited. A zero on these is\n"
            f"    silence, not evidence of health. Covered by D4 and, once it\n"
            f"    exists, by OQ-5's responsiveness metric."
        )
