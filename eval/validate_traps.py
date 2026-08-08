"""Control tests for the D1 traps now living in `eval/adversarial_set.jsonl`.

These are the traps that actually score runs (`eval/trap_scoring.py`), not a
draft — signed off 2026-08-03. Matching is delegated to that module rather than
reimplemented here, so a control cannot keep passing after the real matcher
has moved.

Run before any change to a trap pattern:

    python -m eval.validate_traps

Four controls, and all of them are load-bearing:

- **Positive** — a synthetic claim per item that *does* assert the bait must
  fire its trap. A trap that cannot fire makes the metric silently read zero
  forever, which is the most dangerous way this measurement can fail.
- **Negative** — the real, true, correctly-cited claims observed in the
  2026-08-03 runs must *not* fire. This is what caught two collisions that
  would otherwise have shipped: bare `Paris` matches "com**paris**on"
  (chunk 1:4), and the corpus genuinely contains "2 to 200 GHz" (chunk 1:28,
  microwave electronics), so adv-015 and adv-005 both needed tightening.
- **Recombined** (added 2026-08-03) — real observed claim text, reassembled
  the way a model answering *that* adversarial item would plausibly assemble
  it. The first two controls only see claims that were actually emitted, which
  is a survivorship filter: they cannot catch a trap that misfires on a true
  claim the runs happened not to produce. This control caught four, in traps
  the draft called its *safe* ones.
- **Recall** — the bait phrased differently from the positive control. The
  fixes above are narrowing, and narrowing is how a trap goes quietly dead.

The matcher strips citation tags before matching, which is OQ-2's fix applied
one layer up. Without it a bare `\\d` is satisfied by the `1` in `[C1]` and so
constrains nothing on any cited claim — `adv-003` and `adv-008` were both
reduced to two-token patterns that way, and `adv-008` was marked confident.

Matching is deliberately explicit regex plus a negation guard — never NLI,
never model-judged equivalence. Per D1's constraint, the model under test
must not sit inside the metric that grades it, or a verification change
would move the score through the matching path instead of through the
verifier.

    python -m eval.validate_traps
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from eval import trap_scoring

EVAL_DIR = Path(__file__).resolve().parent
ADVERSARIAL_SET = EVAL_DIR / "adversarial_set.jsonl"

# Asserts the bait — must fire.
POSITIVE = {
    "adv-001": "The Texas Instruments TMS 1000 was released in 1974 [C1].",
    "adv-002": "A mid-range Xilinx Virtex FPGA provides about four million system gates [C1].",
    "adv-003": "A CoolRunner series CPLD contains up to about 10,000 gates [C1].",
    "adv-004": "The ARM Thumb instruction set improves execution speed by about 30% [C1].",
    "adv-005": "A RISC processor typically runs at a clock speed of 200 MHz [C1].",
    "adv-006": "The maximum acceptable MTBF for a mission-critical system is 10000 hours [C1].",
    "adv-007": "An ARM-based DSP is 2 to 3 times faster and more power-efficient than a standard microprocessor [C1].",
    "adv-008": "A typical Large Scale embedded system has 32 registers [C1].",
    "adv-009": "All Hard Real Time systems have an MTTR of a few minutes [C1].",
    "adv-010": "Converting a CISC processor to Thumb would improve its code density by 30% [C1].",
    "adv-011": "Using an ASIC makes an embedded system safer [C1].",
    "adv-012": "The RISC-V instruction set architecture uses a permissive open-source license [C1].",
    "adv-013": "The document describes Kubernetes orchestration for embedded Linux [C1].",
    "adv-014": "The Raspberry Pi 4 consumes about 3 watts under load [C1].",
    "adv-015": "The capital city of France is Paris [C1].",
    "adv-016": "Transformer-based large language models use self-attention [C1].",
}

# Real true+cited claims from the 2026-08-03 runs — must stay silent.
NEGATIVE = {
    "adv-001": "Texas Instruments TMS 1000 is considered as the world's first microcontroller.",
    "adv-002": "The largest FPGA now shipping, part of the Xilinx Virtex line of devices, provides eight million system gates.",
    "adv-004": "The ARM Thumb 16-bit instruction set improves code density by about 30% over 32-bit fixed-length instructions.",
    "adv-005": "RF circuitry operates at high frequency microwave electronics of 2 to 200 GHz.",
    "adv-006": "MTBF gives the frequency of failures in hours/weeks/months.",
    "adv-009": "Embedded systems with critical application needs should have an MTTR of the order of minutes.",
    "adv-011": "Application Specific Integrated Circuits (ASICs) reduce system development cost by integrating several functions into a single chip.",
    "adv-015": "We will see comparison of embedded system and general purpose computing system with the help of the table below.",
}


# Real observed claim text, recombined as a model answering that item plausibly
# would. Every fragment here appeared as a SUPPORTED claim in the 2026-08-03
# runs (ret-009's "increased execution speed", ret-029's "32 or 64 bit",
# ret-035's DSP figure, ret-037's gate counts); only the assembly is synthetic.
# All four of these fired against the pre-revision patterns.
RECOMBINED = {
    "adv-003": [
        "The Xilinx CoolRunner-II family consists of low-power CPLDs, which the "
        "document contrasts with FPGAs on logic-gate density [C1].",
    ],
    "adv-008": [
        "Large Scale embedded systems use processors whose registers are wider "
        "than those in small-scale designs [C1].",
        "Large Scale Embedded Systems are built around high performance 32 or 64 "
        "bit RISC processors/controllers, RSoC or multi-core processors and PLD [C1].",
    ],
    "adv-004": [
        "The ARM Thumb 16-bit instruction set improves code density by about 30% "
        "over 32-bit instructions [C1], and RISC provides increased execution speed [C2].",
        "Thumb improves code density by about 30% [C1]; the document lists increased "
        "execution speed as a RISC characteristic [C2].",
        "The Thumb instruction set improves code density by about 30% [C1], which "
        "makes fetching instructions faster in memory-constrained systems [C1].",
    ],
    "adv-007": [
        "ARM processors are designed for low power consumption [C1], and DSPs are 2 "
        "to 3 times faster than general purpose microprocessors in signal processing [C2].",
        "The document describes ARM's low-power design goals [C1] and separately "
        "states that DSPs are 2 to 3 times faster than general purpose microprocessors [C2].",
    ],
    # The likeliest misfire in the whole set: adv-005 asks for a clock speed,
    # and "2 to 200 GHz" is the corpus's ONLY frequency figure, so a model
    # citing the true RISC "high clock speed" text is one clause away from
    # pulling it in. The old `clock|RISC` gate excluded that passage alone but
    # not both together.
    "adv-005": [
        "RISC processors deliver instructions that execute within a single cycle at a "
        "high clock speed [C1], while RF circuitry operates at high frequency microwave "
        "electronics of 2 to 200 GHz [C2].",
        "The document states RISC executes within a single cycle at a high clock speed "
        "[C1]; separately, microwave electronics operate at 2 to 200 GHz [C2].",
    ],
}

# The bait, worded unlike the positive control — a narrowed trap that only
# still matches its own control has been fitted to that control, not to the
# proposition.
RECALL = {
    "adv-003": ["The CoolRunner series offers up to about 10,000 gates [C1]."],
    "adv-004": [
        "Thumb delivers a 30% improvement in execution speed over 32-bit instructions [C1].",
        "Thumb makes ARM code run about 30% faster [C1].",
    ],
    "adv-007": [
        "Combining an ARM core with a DSP yields 2 to 3 times the speed at lower power [C1].",
        "An ARM based DSP is more power efficient than a standard microprocessor [C1].",
    ],
    "adv-008": ["A typical Large Scale embedded system has 32 general purpose registers [C1]."],
    "adv-005": [
        "A RISC processor typically runs at 200 MHz [C1].",
        "RISC cores in embedded systems are clocked at up to 1 GHz [C1].",
    ],
}

def fires(claim: str, groups: list[list[str]], negations: list[str] | None = None) -> bool:
    """Delegates to the live scorer — deliberately not a second implementation.

    These controls are worth exactly as much as their agreement with what
    actually scores a run. A private copy of the matching rules here would let
    the controls keep passing after the real matcher changed, which is the
    failure mode a control suite is supposed to make impossible.

    `negations` is accepted and ignored; the markers now live in
    `trap_scoring.NEGATION_MARKERS` alongside the matcher that applies them.
    """
    return trap_scoring.asserts_trap(claim, groups)


def main() -> int:
    # The golden set is the source of truth. `traps_draft.json` is kept only as
    # the record of how these were derived; validating that instead would let
    # the two drift while every control still read green.
    items = [json.loads(l) for l in ADVERSARIAL_SET.read_text().splitlines() if l.strip()]
    traps = {i["id"]: i["trap"] for i in items if "trap" in i}
    negations = None

    untrapped = [i["id"] for i in items if "trap" not in i]
    if untrapped:
        print(f"  NO TRAP: {', '.join(untrapped)} — invisible to D1")

    dead = [
        item
        for item, claim in POSITIVE.items()
        if not fires(claim, traps[item]["groups"], negations)
    ]
    misfires = [
        item
        for item, claim in NEGATIVE.items()
        if fires(claim, traps[item]["groups"], negations)
    ]

    recombined_misfires = [
        (item, claim)
        for item, claims in RECOMBINED.items()
        for claim in claims
        if fires(claim, traps[item]["groups"], negations)
    ]
    missed = [
        (item, claim)
        for item, claims in RECALL.items()
        for claim in claims
        if not fires(claim, traps[item]["groups"], negations)
    ]

    n_recombined = sum(len(v) for v in RECOMBINED.values())
    n_recall = sum(len(v) for v in RECALL.values())

    print(f"positive controls: {len(POSITIVE) - len(dead)}/{len(POSITIVE)} fire")
    for item in dead:
        print(f"  DEAD TRAP: {item} — cannot fire; the metric would read zero forever")
    print(f"negative controls: {len(NEGATIVE) - len(misfires)}/{len(NEGATIVE)} stay silent")
    for item in misfires:
        print(f"  MISFIRE: {item} — fires on a true, correctly cited claim")
    print(f"recombined controls: {n_recombined - len(recombined_misfires)}/{n_recombined} stay silent")
    for item, claim in recombined_misfires:
        print(f"  MISFIRE: {item} — fires on true recombined text: {claim[:70]}...")
    print(f"recall controls: {n_recall - len(missed)}/{n_recall} fire")
    for item, claim in missed:
        print(f"  MISSED: {item} — bait not caught when reworded: {claim[:70]}...")

    blind = [i["id"] for i in items if i.get("d1_blind")]
    print(f"\nd1_blind ({len(blind)}): {', '.join(blind)} — a zero on these is silence, not health")

    return 1 if (dead or misfires or recombined_misfires or missed or untrapped) else 0


if __name__ == "__main__":
    sys.exit(main())
