"""Cross-arm analysis for the Direction C experiment.

Two things the summary table cannot show on its own:

- **The superset property.** Windowing always scores the full chunk first and
  returns its verdict when nothing narrower entails, so every arm's SUPPORTED
  set must be a superset of the baseline's. If it is not, the harness changed
  something other than the premise and no number from it is attributable.
- **Which items each repair actually moved**, on both metrics, so a
  false-rejection gain and a false-support cost can be traced to specific
  items rather than to a rate.

    python -m eval.analyze_direction_c
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from eval import trap_scoring
from eval.run_faithfulness_eval import ADVERSARIAL_SET, load

RESULTS = Path(__file__).resolve().parent / "results"


def supported_texts(record: dict) -> set[str]:
    return set(trap_scoring.claim_texts(record.get("supported", [])))


def load_arm(name: str, prefix: str = "direction-c") -> dict[str, dict]:
    path = RESULTS / f"records-{prefix}-{name}.json"
    return {r["id"]: r for r in json.loads(path.read_text())}


def main(argv: list[str] | None = None) -> int:
    summary = json.loads((RESULTS / "direction-c-summary.json").read_text())
    arms = [r["arm"] for r in summary]
    if "baseline" not in arms:
        print("no baseline arm — nothing to compare against")
        return 2

    base = load_arm("baseline")
    items = {i["id"]: i for i in load(ADVERSARIAL_SET)}

    print("=" * 78)
    print("SUPERSET CHECK — windowing may only add entailments, never remove one")
    print("=" * 78)
    ok = True
    for name in arms:
        if name == "baseline":
            continue
        arm = load_arm(name)
        lost = {
            rid: sorted(supported_texts(base[rid]) - supported_texts(arm[rid]))
            for rid in base
            if supported_texts(base[rid]) - supported_texts(arm[rid])
        }
        print(f"  {name:10} claims lost vs baseline: {len(lost)}")
        for rid, texts in list(lost.items())[:3]:
            ok = False
            print(f"      ! {rid}: {texts[0][:80]}")
    print(f"  => {'holds' if ok else 'VIOLATED — results are not attributable'}")

    print("\n" + "=" * 78)
    print("PER-ITEM MOVEMENT vs baseline")
    print("=" * 78)
    for name in arms:
        if name == "baseline":
            continue
        arm = load_arm(name)
        recovered = [
            rid for rid, r in arm.items()
            if r.get("expected_state") == "ok"
            and base[rid].get("actual_state") == "insufficient_evidence"
            and r.get("actual_state") == "ok"
        ]
        newly_ok = [
            rid for rid, r in arm.items()
            if r.get("expected_state") == "insufficient_evidence"
            and base[rid].get("actual_state") != "ok"
            and r.get("actual_state") == "ok"
        ]
        newly_d1 = [
            rid for rid, r in arm.items()
            if rid in items
            and trap_scoring.item_asserts_trap(r, items[rid]["trap"])
            and not trap_scoring.item_asserts_trap(base[rid], items[rid]["trap"])
        ]
        print(f"\n  {name}")
        print(f"    false rejections recovered ({len(recovered)}): {', '.join(recovered) or '—'}")
        print(f"    adversarial items newly 'ok' ({len(newly_ok)}): {', '.join(newly_ok) or '—'}")
        print(f"    newly asserting a trap under D1 ({len(newly_d1)}): {', '.join(newly_d1) or '—'}")
        for rid in newly_d1:
            for text in sorted(supported_texts(arm[rid]) - supported_texts(base[rid])):
                print(f"        ! {rid}: {text[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
