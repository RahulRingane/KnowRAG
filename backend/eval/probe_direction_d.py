"""Direction D probe: does another NLI checkpoint avoid the long-premise failure?

OQ-3's root cause is a property of `cross-encoder/nli-deberta-v3-base` — it
returns *confident* neutral on long multi-topic premises even when the claim is
a verbatim sentence inside them. Direction C works around that by shrinking the
premise, at 17.6x compute. Direction D asks whether the problem is the model
rather than the premise, in which case there is nothing to work around.

**Method — re-verification from records, nothing re-generated.** Every claim,
citation and chunk assignment is replayed from
`records-direction-c-baseline.json`, so generation and retrieval are held
exactly constant and the NLI checkpoint is the only variable. `verify_claim`'s
rule is mirrored here (best entailment among cited chunks >= threshold ->
SUPPORTED) rather than re-implemented loosely.

**Label order is read from each model's own config, never assumed.**
`app/domain/verification.py` used to hardcode (contradiction, entailment, neutral), which is the
sentence-transformers convention and is *wrong* for most other checkpoints —
MoritzLaurer's are (entailment, neutral, contradiction), BART-MNLI is
(contradiction, neutral, entailment). Assuming the current order would silently
invert every comparison and make a good model look useless or a bad one look
perfect.

    python -m eval.probe_direction_d
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.domain.verification import strip_citation_tags
from eval import trap_scoring
from eval.run_faithfulness_eval import ADVERSARIAL_SET, load

RESULTS = Path(__file__).resolve().parent / "results"
BASELINE = RESULTS / "records-direction-c-baseline.json"

CANDIDATES = [
    # The incumbent, re-measured through this same harness so the comparison
    # is apples-to-apples rather than against the recorded numbers.
    "cross-encoder/nli-deberta-v3-base",
    # Same size class, but trained on MNLI+FEVER+ANLI. FEVER is fact
    # verification against retrieved evidence passages, which is exactly this
    # workload and exactly what the incumbent's MNLI-only training lacks.
    "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli",
    # A larger, different-architecture control: if only this one works, the
    # answer is "capacity"; if the base model above works too, it is training
    # data, which is the far cheaper conclusion.
    "facebook/bart-large-mnli",
]


def load_chunks() -> dict[str, str]:
    eng = create_engine(settings.database_url)
    with eng.connect() as c:
        rows = c.execute(text("SELECT document_id, chunk_index, chunk_text FROM chunks")).fetchall()
    return {f"{d}:{i}": t for d, i, t in rows}


def label_order(model) -> tuple[str, ...]:
    """Read (index -> label) from the checkpoint's own config."""
    id2label = model.model.config.id2label
    out = []
    for i in range(len(id2label)):
        raw = str(id2label[i]).lower()
        if "entail" in raw:
            out.append("entailment")
        elif "contra" in raw:
            out.append("contradiction")
        elif "neutral" in raw:
            out.append("neutral")
        else:
            raise SystemExit(f"unrecognised label {raw!r} in {id2label}")
    return tuple(out)


def main(argv: list[str] | None = None) -> int:
    from sentence_transformers import CrossEncoder

    chunks = load_chunks()
    records = json.loads(BASELINE.read_text())
    items = load(ADVERSARIAL_SET)
    threshold = settings.claim_verification_threshold

    # OQ-3 exemplars. The first two are hand-written on purpose — they are the
    # live-query claims that started Direction D, and chunk 1:21 is where the
    # text demonstrably lives.
    #
    # The rest are pulled from the *records*, claim string and cited chunk id
    # together, never hand-typed. An earlier version of this list guessed both
    # and reported "ret-020 fails on every checkpoint" — false: the real claim
    # cites 1:20, not the 1:41 that was guessed, and it passes at 0.963. That is
    # PLAN.md's standing warning ("score the recorded string, never a retyped
    # one") reproduced exactly, in the probe written to avoid it.
    EXEMPLARS = [
        ("1:21", "Confidentiality deals with protection of data and application from unauthorized disclosure."),
        ("1:21", "Integrity deals with the protection of data and application from unauthorized modification."),
    ]
    for rid in ("ret-014", "ret-020", "ret-004"):
        rec = next((r for r in records if r["id"] == rid), None)
        for claim in (rec or {}).get("rejected", []):
            for cid in claim.get("chunk_ids") or []:
                EXEMPLARS.append((cid, strip_citation_tags(claim["text"])))
                break

    summary = []
    for name in CANDIDATES:
        print(f"\n{'=' * 84}\n{name}", flush=True)
        t0 = time.perf_counter()
        model = CrossEncoder(name)
        # Normalise storage dtype to fp32. MoritzLaurer's checkpoint ships in
        # float16, and x86 has no native fp16 compute — torch emulates it, which
        # cost 3112ms/call versus 525ms for the identical weights in fp32, with
        # byte-identical scores (0.943/0.946/0.570 either way). Comparing
        # checkpoints without this normalisation measures the upload dtype, not
        # the model, and would have rejected the best candidate on a 9x latency
        # figure that does not exist.
        import torch

        if next(model.model.parameters()).dtype != torch.float32:
            model.model.to(torch.float32)
        load_s = time.perf_counter() - t0
        labels = label_order(model)
        params = sum(p.numel() for p in model.model.parameters())
        print(f"  label order from config: {labels}")
        print(f"  {params/1e6:.0f}M params, loaded in {load_s:.1f}s")

        def score(premise: str, hypothesis: str) -> tuple[str, float]:
            (probs,) = model.predict([(premise, hypothesis)], apply_softmax=True)
            i = int(probs.argmax())
            return labels[i], float(probs[i])

        print(f"\n  --- OQ-3 exemplars, FULL chunk as premise (no windowing) ---")
        for cid, claim in EXEMPLARS:
            premise = chunks[cid]
            lab, sc = score(premise, claim)
            ok = lab == "entailment" and sc >= threshold
            print(f"   [{'PASS' if ok else 'FAIL'}] {cid} ({len(premise)} ch)  {lab:>13} {sc:.3f}  {claim[:56]}...")

        # --- full replay: only the checkpoint varies -------------------------
        t0 = time.perf_counter()
        calls = 0
        new_records = []
        for r in records:
            supported, rejected = [], []
            for claim in r["supported"] + r["rejected"]:
                claim = claim if isinstance(claim, dict) else {"text": claim, "citations": [], "chunk_ids": []}
                cites = claim.get("citations") or []
                # Production's stripper, never a local reimplementation. A
                # hand-rolled version here left "... minutes ." — a dangling
                # space before the period — on 39 claims, which is OQ-2's exact
                # failure mode (non-sentence hypothesis scores neutral) and
                # inflated false rejection 0.385 -> 0.462. Caught only because
                # the incumbent is re-measured through this harness and failed
                # to reproduce its own known number.
                hypothesis = strip_citation_tags(claim["text"])

                best = ("neutral", 0.0)
                for cid in claim.get("chunk_ids") or []:
                    if cid not in chunks:
                        continue
                    lab, sc = score(chunks[cid], hypothesis)
                    calls += 1
                    if lab == "entailment" and sc > best[1]:
                        best = (lab, sc)
                entry = dict(claim)
                if cites and best[0] == "entailment" and best[1] >= threshold:
                    entry["evidence_score"] = best[1]
                    supported.append(entry)
                else:
                    rejected.append(entry)
            new_records.append({**r, "supported": supported, "rejected": rejected,
                                "actual_state": "ok" if supported else "insufficient_evidence"})
        replay_s = time.perf_counter() - t0

        d1 = trap_scoring.score(new_records, items)
        answerable = [r for r in new_records if r["expected_state"] == "ok"]
        refused = [r for r in answerable if r["actual_state"] == "insufficient_evidence"]
        fr = len(refused) / len(answerable) if answerable else 0.0

        row = {
            "model": name, "params_m": round(params / 1e6), "labels": labels,
            "d1": d1["d1_rate"], "d4": d1["d4_rate"], "old": d1["old_rate"],
            "d1_items": d1["d1_items"], "old_items": d1["old_items"],
            "fr": fr, "refused": len(refused), "answerable": len(answerable),
            "ms_per_call": round(replay_s / calls * 1000, 1) if calls else None,
            "load_s": round(load_s, 1),
        }
        summary.append(row)
        print(f"\n  D1 {row['d1']:.3f}  D4 {row['d4']:.3f}  old {row['old']:.3f}  "
              f"false-rejection {fr:.3f} ({len(refused)}/{len(answerable)})  "
              f"{row['ms_per_call']}ms/call", flush=True)

        # Fidelity gate. The incumbent replayed through this harness must
        # reproduce the pipeline numbers it actually produced. If it does not,
        # the replay differs from production somewhere and every other row is
        # measuring that difference rather than the checkpoint.
        if name == CANDIDATES[0]:
            want_fr, want_old = 15 / 39, 2 / 16
            drift = abs(fr - want_fr) > 1e-9 or abs(d1["old_rate"] - want_old) > 1e-9
            print(
                f"  fidelity vs recorded baseline: false-rejection {fr:.3f} vs {want_fr:.3f}, "
                f"old {d1['old_rate']:.3f} vs {want_old:.3f} -> "
                f"{'DRIFT — other rows are not attributable' if drift else 'exact'}",
                flush=True,
            )
            if drift:
                return 1

    print("\n" + "=" * 96)
    print("DIRECTION D — NLI checkpoint swap, generation + retrieval held constant")
    print("=" * 96)
    h = f"{'model':46} {'params':>7} {'D1':>6} {'D4':>6} {'old':>6} {'false-rej':>12} {'ms/call':>8}"
    print(h); print("-" * len(h))
    for r in summary:
        print(f"{r['model']:46} {r['params_m']:>6}M {r['d1']:>6.3f} {r['d4']:>6.3f} {r['old']:>6.3f} "
              f"{r['fr']:>5.3f} ({r['refused']:>2}/{r['answerable']}) {r['ms_per_call']:>8}")
    for r in summary:
        if r["d1_items"]:
            print(f"\n  ! {r['model']}: D1 flagged {r['d1_items']}")
    (RESULTS / "direction-d-summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {RESULTS / 'direction-d-summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
