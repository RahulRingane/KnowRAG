"""§8.3/§8.4 — faithfulness, with false-support rate as the primary metric.

§8.4 is explicit about the asymmetry: a false rejection is a UX cost, a
false support is a trust failure. So the headline number here is the
fraction of adversarial questions that came back `state="ok"` — the system
asserting something the corpus does not support. False rejections are
reported too, but they are secondary and are never allowed to gate CI.

Built on the hand-authored golden set rather than `ragas`. §8.3 permits
either, and the hand-built path is the honest one here: every adversarial
item already carries an expected verdict and a written rationale, so a
failure names the exact question and category rather than moving an
aggregate score. It also makes no LLM-judge calls, which would otherwise
double free-tier spend to grade the run.

Cost control (§12.1): `app.infrastructure.llm.cache` caches on
`(question, retrieved_chunk_ids, model)`, so re-running against unchanged
retrieval spends no quota. `--no-cache` forces live calls — use it when the
prompt, the corpus or the model has changed and cached verdicts would be
stale.

Usage:

    python -m eval.run_faithfulness_eval                     # both sets, cached
    python -m eval.run_faithfulness_eval --set adversarial --no-cache
    python -m eval.run_faithfulness_eval --max-false-support 0.0   # CI gate
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

from app.infrastructure.llm import cache
from app.services.query_service import build_default_query_service
from eval import trap_scoring


@lru_cache(maxsize=1)
def default_service():
    """The production query pipeline, built once and only when first needed.

    Deferred rather than built at import so that importing this module (as
    `experiment_direction_c` does, for `evaluate`) costs nothing.
    """
    return build_default_query_service()

EVAL_DIR = Path(__file__).resolve().parent
ADVERSARIAL_SET = EVAL_DIR / "adversarial_set.jsonl"
RETRIEVAL_SET = EVAL_DIR / "retrieval_set.jsonl"


def load(path: Path, ids: list[str] | None = None) -> list[dict]:
    items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if ids is not None:
        wanted = set(ids)
        items = [i for i in items if i["id"] in wanted]
    return items


def _claim_record(claim) -> dict:
    """Serialize one verdict with the evidence trail intact.

    `citations`, `chunk_ids` and `evidence_score` used to be dropped here,
    which made the false-rejection number uninvestigable: "no cited chunk
    entails this claim" does not say *which* chunk was cited, so telling a
    mis-citation (the claim pointed at the wrong tag; the right chunk would
    have entailed it) apart from a genuine verification miss (the right chunk
    was cited and still didn't entail) meant re-deriving the whole thing by
    hand. Those two have opposite fixes — generation-side tag discipline
    versus verification-side premise handling — so a record that cannot
    distinguish them cannot support a decision.
    """
    return {
        "text": claim.text,
        "status": claim.status,
        "reason": claim.reason,
        "citations": claim.citations,
        "chunk_ids": claim.chunk_ids,
        "evidence_score": claim.evidence_score,
    }


def _split_verdicts(response) -> dict[str, list[dict]]:
    """Partition verdicts into supported / rejected / refusals (OQ-5).

    `refusals` is a third list, not a subset of `rejected`. Before the `kind`
    field they were indistinguishable, and D4 had to infer "the system
    refused" from a rejected claim existing — which held only because the
    model usually left refusals uncited. Recording them separately is what
    lets D4 read an explicit signal instead of that accident.
    """
    return {
        "supported": [_claim_record(c) for c in response.claims if c.status == "SUPPORTED"],
        "rejected": [
            _claim_record(c) for c in response.claims if c.status in ("UNSUPPORTED", "CONTRADICTED")
        ],
        "refusals": [_claim_record(c) for c in response.claims if c.status == "REFUSAL"],
    }


def evaluate(items: list[dict], label: str, service=None) -> list[dict]:
    """Run the full pipeline over `items`, returning one record each.

    `service` lets an experiment harness supply a `QueryService` wired
    differently — a different verifier, a different scorer — and reuse this
    loop unchanged. `eval/experiment_direction_c.py` does exactly that; it
    used to reassign `verify.predict_nli` globally instead, which meant the
    experiment's arm leaked into anything else in the process.
    """
    service = service or default_service()
    records = []

    for n, item in enumerate(items, start=1):
        print(f"  [{label} {n}/{len(items)}] {item['id']}", flush=True)
        try:
            response = service.run(item["question"])
            record = {
                "id": item["id"],
                "category": item.get("category"),
                "question": item["question"],
                "expected_state": item["expected_state"],
                "actual_state": response.state,
                "answer": response.answer,
                # What retrieval actually put in front of the model. Without
                # it there is no way to tell "the evidence was never
                # retrieved" from "it was retrieved and mis-cited".
                "retrieved_chunk_ids": response.retrieved_chunk_ids,
                **_split_verdicts(response),
                "error": None,
            }
        except Exception as exc:
            # Recorded, not raised: one question failing (a 429 that
            # outlasted its retries, say) should not discard the results of
            # every question already run.
            print(f"      ERROR: {exc}", flush=True)
            record = {
                "id": item["id"],
                "category": item.get("category"),
                "question": item["question"],
                "expected_state": item["expected_state"],
                "actual_state": None,
                "answer": None,
                "retrieved_chunk_ids": [],
                "supported": [],
                "rejected": [],
                "refusals": [],
                "error": str(exc),
            }
        records.append(record)

    return records


def report(records: list[dict], items: list[dict] | None = None) -> tuple[float, float]:
    """Print the breakdown; return `(false_support_rate, false_rejection_rate)`.

    When `items` carry traps, the primary number is **D1** and the old
    `state == "ok"` number is printed beside it as a bridge. Both are computed
    on the same records in one pass: a redefined primary metric reported
    without the number it replaced, on identical data, is not checkable — and
    this redefinition takes the figure to zero, which is exactly the shape a
    reader is entitled to be suspicious of.
    """
    scored = [r for r in records if r["error"] is None]
    errored = [r for r in records if r["error"] is not None]

    should_reject = [r for r in scored if r["expected_state"] == "insufficient_evidence"]
    should_answer = [r for r in scored if r["expected_state"] == "ok"]

    false_supports = [r for r in should_reject if r["actual_state"] == "ok"]
    false_rejections = [r for r in should_answer if r["actual_state"] == "insufficient_evidence"]

    trapped = [i for i in (items or []) if "trap" in i]
    d1 = trap_scoring.score(records, trapped) if trapped else None

    # D1 is the primary metric once traps are present; the old rate stays the
    # return value only where no trap is defined to replace it.
    fs_rate = d1["d1_rate"] if d1 and d1["n"] else (
        len(false_supports) / len(should_reject) if should_reject else 0.0
    )
    fr_rate = len(false_rejections) / len(should_answer) if should_answer else 0.0

    print("\n" + "=" * 72)
    print("FAITHFULNESS RESULTS")
    print("=" * 72)
    print(f"scored: {len(scored)}   errored: {len(errored)}")

    if d1 and d1["n"]:
        trap_scoring.report_d1(d1)
        untrapped = len(should_reject) - d1["n"]
        if untrapped:
            print(f"\n  ({untrapped} adversarial items carry no trap and are unscored by D1)")
    elif should_reject:
        print(
            f"\nFALSE-SUPPORT RATE (primary): {fs_rate:.3f}  "
            f"({len(false_supports)}/{len(should_reject)} adversarial items answered)"
        )
        by_category = Counter(r["category"] for r in false_supports)
        for category, n in by_category.most_common():
            total = sum(1 for r in should_reject if r["category"] == category)
            print(f"    {category}: {n}/{total}")

    if should_reject:
        for r in false_supports:
            flagged = d1 and r["id"] in d1["d1_items"]
            mark = "!" if flagged else "~"
            print(f"\n  {mark} {r['id']} [{r['category']}]"
                  f"{'' if flagged else '  — old metric only, trap not asserted'}")
            print(f"    Q: {r['question'][:110]}")
            for claim in r["supported"]:
                # Pre-2026-08-03 records stored bare strings with no evidence
                # trail; print what is there rather than failing to rescore.
                if isinstance(claim, str):
                    print(f"    claimed: {claim}")
                else:
                    print(f"    claimed: {claim['text']}  {claim['citations']}")

    if should_answer:
        print(
            f"\nfalse-rejection rate (secondary): {fr_rate:.3f}  "
            f"({len(false_rejections)}/{len(should_answer)} answerable items refused)"
        )
        for r in false_rejections[:10]:
            print(f"    - {r['id']}: {r['question'][:90]}")
        if len(false_rejections) > 10:
            print(f"    ... and {len(false_rejections) - 10} more")

    if errored:
        print(f"\nerrored items ({len(errored)}):")
        for r in errored:
            print(f"    - {r['id']}: {r['error'][:120]}")

    return fs_rate, fr_rate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--set",
        choices=["adversarial", "retrieval", "both"],
        default="both",
        help="Which golden set to run. 'adversarial' alone measures the primary metric.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force live LLM calls instead of reusing cached generations (spends quota).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N items.")
    parser.add_argument(
        "--ids",
        nargs="+",
        default=None,
        help="Only run these item ids (e.g. --ids adv-007 adv-014). Re-runs items that "
        "errored without re-spending quota on the ones that already scored.",
    )
    parser.add_argument(
        "--max-false-support",
        type=float,
        default=None,
        help="Fail (exit 1) if the false-support rate exceeds this. For CI gating.",
    )
    parser.add_argument("--out", type=Path, default=None, help="Write per-item records as JSON.")
    parser.add_argument(
        "--rescore",
        type=Path,
        default=None,
        help="Score an existing records JSON instead of running the pipeline. Makes no "
        "LLM calls at all, which is what lets the D1/old-metric bridge be re-derived "
        "on past runs without re-spending quota on them.",
    )
    args = parser.parse_args(argv)

    if args.no_cache:
        cache.CACHE_ENABLED = False
        print("Cache disabled — every question will spend free-tier quota.\n")

    records: list[dict] = []
    adversarial_items: list[dict] = []

    if args.rescore:
        records = json.loads(args.rescore.read_text())
        adversarial_items = load(ADVERSARIAL_SET)
        print(f"Rescoring {len(records)} records from {args.rescore} — no LLM calls.")
    else:
        if args.set in ("adversarial", "both"):
            adversarial_items = load(ADVERSARIAL_SET, args.ids)[: args.limit]
            print(f"Adversarial set: {len(adversarial_items)} items (all must be rejected)")
            records += evaluate(adversarial_items, "adv")

        if args.set in ("retrieval", "both"):
            items = load(RETRIEVAL_SET, args.ids)[: args.limit]
            print(f"\nRetrieval set: {len(items)} items (all should be answerable)")
            records += evaluate(items, "ret")

    fs_rate, _fr_rate = report(records, adversarial_items)

    if args.out:
        args.out.write_text(json.dumps(records, indent=2))
        print(f"\nWrote {args.out}")

    if args.max_false_support is not None:
        if fs_rate > args.max_false_support:
            print(f"\nFAIL: false-support rate {fs_rate:.3f} > {args.max_false_support:.3f}")
            return 1
        print(f"\nPASS: false-support rate {fs_rate:.3f} <= {args.max_false_support:.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
