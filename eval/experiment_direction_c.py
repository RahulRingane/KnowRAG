"""OQ-3 Direction C repairs, measured against the corrected D1/D4 metric.

**Experiment harness, not production.** It injects a substitute scorer into
`ClaimVerifier`
for the duration of an arm and restores it after. Nothing here ships; the point
is to decide whether anything *should*.

Direction C narrows the NLI premise: score the claim against sentence windows
inside the cited chunk, best entailment wins. The 2026-08-03 prototype
(400-char windows) fixed most of the false rejections and failed on two counts —
false support rose, and it cost **17.6x** the NLI compute, which put
verification at ~100-200s per query regardless of accuracy.

Two repairs, tested here together and separately:

- **Window floor** — 400 chars was arbitrary. A larger window entails less
  promiscuously, which is the whole false-support mechanism: the adversarial
  set baits combinations that a narrow window can satisfy and a wide one
  cannot.
- **Conditional windowing** — only window when the full chunk is both
  non-entailing *and* long. The prototype already short-circuits on
  entailment; the length gate is what removes the remaining waste, since a
  short chunk has nothing to narrow.

**Attribution discipline (OQ-4).** Whatever moves here has to be attributable
to verification alone, so:

- Generation is held constant across every arm by the LLM dev cache,
  which is warm for `gpt-4o-mini` on all 55 items. Every arm therefore sees
  byte-identical claim sets and the only thing varying is the NLI premise.
- Retrieval was checked identical to the recorded run on all 55 items before
  any of this was run.
- The **baseline is re-measured here**, under the same generator, rather than
  inherited from the Gemini rows in PLAN.md. Those rows are a different
  generator and are not a valid control for these.

**Compute is counted, not timed.** `stats["logical_calls"]` counts the NLI
calls production would make. A memo cache serves repeats within and across
arms — the model is deterministic, so this changes wall-clock only — and the
reported multiplier deliberately ignores it, because a memo cache is not part
of the change being evaluated.

    python -m eval.experiment_direction_c --arms baseline c400 c700 c900 cond700
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.domain.verification import ClaimVerifier
from app.infrastructure.llm.provider import LLMClaimGenerator
from app.infrastructure.ml.nli import predict_nli
from app.infrastructure.search.hybrid_retriever import build_default_retriever
from app.services.query_service import QueryService
from eval import trap_scoring
from eval.run_faithfulness_eval import ADVERSARIAL_SET, RETRIEVAL_SET, evaluate, load

EVAL_DIR = Path(__file__).resolve().parent
RESULTS = EVAL_DIR / "results"

_real_predict_nli = predict_nli

# Sentence-ish: real sentence enders, newlines, and the bullet character this
# corpus uses for definition lists ("•It is the measure of system independence.").
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|(?=•)")

stats: dict[str, int] = {}
_memo: dict[str, tuple[str, float]] = {}


def _reset_stats() -> None:
    stats.clear()
    stats.update({"claims": 0, "logical_calls": 0, "windowed": 0, "won_by_window": 0})


def _scored(premise: str, hypothesis: str) -> tuple[str, float]:
    """One logical NLI call, memoized.

    Counted before the memo lookup: the count must reflect what production
    would pay, and production has no memo.
    """
    stats["logical_calls"] += 1
    key = hashlib.sha256(f"{premise}\x00{hypothesis}".encode()).hexdigest()
    if key not in _memo:
        _memo[key] = _real_predict_nli(premise=premise, hypothesis=hypothesis)
    return _memo[key]


def _units(text: str) -> list[str]:
    return [u.strip() for u in _SPLIT.split(text) if u and u.strip()]


def _windows(text: str, floor: int) -> list[str]:
    """Sliding windows of consecutive units, each under `floor` chars.

    The full text is always a candidate, so windowing can only find an
    entailment the unwindowed scorer would also have found, plus more.
    """
    units = _units(text)
    windows: list[str] = []
    for start in range(len(units)):
        current: list[str] = []
        for unit in units[start:]:
            candidate = current + [unit]
            if current and len(" ".join(candidate)) > floor:
                break
            current = candidate
        if current:
            window = " ".join(current)
            if window not in windows:
                windows.append(window)
    if text not in windows:
        windows.append(text)
    return windows


def make_windowed(floor: int, min_premise: int = 0):
    """Direction C with a window floor and an optional length gate.

    `min_premise=0` reproduces the original prototype's gating (window
    whenever the full chunk does not entail). Above zero it is the
    conditional-windowing repair: a chunk shorter than this is left alone,
    because there is nothing in it to narrow.
    """

    def windowed_predict_nli(premise: str, hypothesis: str):
        stats["claims"] += 1
        full_label, full_score = _scored(premise, hypothesis)
        if full_label == "entailment":
            return full_label, full_score
        if len(premise) <= min_premise:
            return full_label, full_score

        stats["windowed"] += 1
        best: tuple[str, float] | None = None
        for window in _windows(premise, floor):
            if window == premise:
                continue
            label, score = _scored(window, hypothesis)
            if label == "entailment" and (best is None or score > best[1]):
                best = (label, score)

        if best is not None:
            stats["won_by_window"] += 1
            return best
        return full_label, full_score

    return windowed_predict_nli


def baseline_predict_nli(premise: str, hypothesis: str):
    stats["claims"] += 1
    return _scored(premise, hypothesis)


ARMS: dict[str, dict[str, Any]] = {
    "baseline": {"fn": baseline_predict_nli, "desc": "production verifier, no windowing"},
    "c400": {"fn": make_windowed(400), "desc": "original prototype — 400-char windows"},
    "c700": {"fn": make_windowed(700), "desc": "window floor 700"},
    "c900": {"fn": make_windowed(900), "desc": "window floor 900"},
    "cond700": {"fn": make_windowed(700, 900), "desc": "floor 700 + only window chunks >900 chars"},
    "cond900": {"fn": make_windowed(900, 900), "desc": "floor 900 + only window chunks >900 chars"},
}


def false_rejection(records: list[dict]) -> tuple[float, int, int]:
    answerable = [
        r for r in records if r["error"] is None and r.get("expected_state") == "ok"
    ]
    refused = [r for r in answerable if r.get("actual_state") == "insufficient_evidence"]
    n = len(answerable)
    return (len(refused) / n if n else 0.0), len(refused), n


def run_arm(name: str, items_adv: list[dict], items_ret: list[dict]) -> dict[str, Any]:
    arm = ARMS[name]
    _reset_stats()

    # The arm's scorer is injected into a verifier built for this arm, rather
    # than assigned over a module global. Two things improve: the swap cannot
    # leak out of this call, and each arm is a value that can be constructed,
    # inspected and thrown away.
    service = QueryService(
        retriever=build_default_retriever(),
        generator=LLMClaimGenerator(),
        verifier=ClaimVerifier(scorer=arm["fn"]),
    )

    started = time.perf_counter()
    records = evaluate(items_adv, f"{name}/adv", service=service) + evaluate(
        items_ret, f"{name}/ret", service=service
    )
    elapsed = time.perf_counter() - started

    d1 = trap_scoring.score(records, items_adv)
    fr_rate, refused, answerable = false_rejection(records)

    return {
        "arm": name,
        "desc": arm["desc"],
        "records": records,
        "d1_rate": d1["d1_rate"],
        "d1_items": d1["d1_items"],
        "old_rate": d1["old_rate"],
        "old_items": d1["old_items"],
        "d4_rate": d1["d4_rate"],
        "fr_rate": fr_rate,
        "fr_refused": refused,
        "fr_answerable": answerable,
        "logical_calls": stats["logical_calls"],
        "claims_scored": stats["claims"],
        "windowed": stats["windowed"],
        "won_by_window": stats["won_by_window"],
        "wall_seconds": round(elapsed, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", nargs="+", default=["baseline", "c400", "c700", "c900", "cond700"])
    parser.add_argument("--out-prefix", default="direction-c")
    args = parser.parse_args(argv)

    unknown = [a for a in args.arms if a not in ARMS]
    if unknown:
        print(f"unknown arms: {unknown}; known: {list(ARMS)}")
        return 2

    items_adv = load(ADVERSARIAL_SET)
    items_ret = load(RETRIEVAL_SET)
    print(
        f"provider={settings.llm_provider} model="
        f"{settings.openai_model if settings.llm_provider == 'openai' else settings.gemini_model}\n"
        f"{len(items_adv)} adversarial + {len(items_ret)} answerable items per arm\n"
        f"generation served from the dev cache — verification is the only thing varying\n",
        flush=True,
    )

    results = []
    for name in args.arms:
        print(f"\n=== arm: {name} — {ARMS[name]['desc']} ===", flush=True)
        result = run_arm(name, items_adv, items_ret)
        out = RESULTS / f"records-{args.out_prefix}-{name}.json"
        out.write_text(json.dumps(result.pop("records"), indent=2))
        result["records_path"] = str(out)
        results.append(result)
        print(
            f"  D1 {result['d1_rate']:.3f}  old {result['old_rate']:.3f}  "
            f"D4 {result['d4_rate']:.3f}  FR {result['fr_rate']:.3f}  "
            f"NLI calls {result['logical_calls']}  ({result['wall_seconds']}s)",
            flush=True,
        )

    summary = RESULTS / f"{args.out_prefix}-summary.json"
    summary.write_text(json.dumps(results, indent=2))

    base = next((r for r in results if r["arm"] == "baseline"), None)
    print("\n" + "=" * 96)
    print("DIRECTION C REPAIRS — all arms, same generator, generation held constant")
    print("=" * 96)
    header = f"{'arm':10} {'D1':>7} {'D4':>7} {'old':>7} {'false-rej':>11} {'NLI calls':>10} {'vs base':>8}"
    print(header)
    print("-" * len(header))
    for r in results:
        mult = f"{r['logical_calls'] / base['logical_calls']:.1f}x" if base and base["logical_calls"] else "—"
        print(
            f"{r['arm']:10} {r['d1_rate']:>7.3f} {r['d4_rate']:>7.3f} {r['old_rate']:>7.3f} "
            f"{r['fr_rate']:>6.3f} ({r['fr_refused']:>2}/{r['fr_answerable']}) "
            f"{r['logical_calls']:>10} {mult:>8}"
        )
    for r in results:
        if r["d1_items"]:
            print(f"\n  {r['arm']}: D1 flagged {', '.join(r['d1_items'])}")
    print(f"\nwrote {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
