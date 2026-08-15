# KnowRAG Evaluation Baseline

## Current numbers

| Metric | Value |
|---|---|
| **False support — D1 (primary)** | **0.000 (0/16)** |
| D4 bait-rejected | 1.000 (16/16) |
| False rejection | **0.000 (0/39)** |
| NLI calls/run | 112 (1.67x) |
| Adversarial items with explicit refusal | 16/16 |

Generation: `gpt-4o-mini`, 42-chunk corpus, verifier `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` with length-gated narrowing fallback, claims tagged `kind: assertion | refusal`.

**Read D1, not `state=="ok"`.** A refusal beside a correctly-supported claim is intended output for partially-answerable questions — this makes the naive `state=="ok"` metric look worse (rises whenever a refusal survives next to a true claim) while D1 (whether anything unsupported was *asserted*) stays 0.000. Treat `state=="ok"` only as a bridge metric, not the primary signal.

**False-rejection fix was verification-side only.** Generation/retrieval/corpus untouched; only the NLI checkpoint and a length-gated narrowing fallback changed. D1 stayed 0.000 throughout.

---

## Methodology notes

Measured against local compose stack (`notes.pdf`, doc_id 1, **42 chunks**, Postgres/Qdrant/ES).

**Two variables changed together in the underlying corpus/model work — don't attribute faithfulness gains to one alone without an isolated A/B:**
1. Generation model swap (older Gemini model → `gpt-4o-mini`)
2. Corpus fix: 29→42 chunks (two-column tables linearized one row/chunk; dropped ligature-spaces restored)

Isolating the table fix's effect on faithfulness needs a same-model A/B (old corpus vs. new, generator held fixed) — not yet done. Its *measured* contributions are listed under [What the table fix demonstrably fixed](#what-the-table-fix-demonstrably-fixed).

### Retrieval — recall@k

39/39 items labeled.

| path | r@1 | r@3 | r@5 | r@10 | r@20 |
|---|---|---|---|---|---|
| semantic | 0.872 | 0.974 | 0.974 | 1.000 | 1.000 |
| keyword | 0.821 | 0.897 | 1.000 | 1.000 | 1.000 |
| hybrid | **0.923** | **1.000** | 1.000 | 1.000 | 1.000 |

Not confounded by the model change (no LLM call), but corpus + label set both changed. The table fix unblocked two previously-unlabelable items (quotes spanned interleaved columns) and required repairing one gold quote that was a hyphenation+column-splice artifact.

Read r@1/r@3 only — corpus is 42 chunks, so r@20=1.000 is close to guaranteed.

### Faithfulness — 16 adversarial items

**False-support (primary): 0.125 (2/16)**

| category | false supports |
|---|---|
| unsupported_combination | 2/5 |
| adjacent_absent | 0/6 |
| out_of_scope | 0/5 |

Flagged: `adv-009`, `adv-011`. Errors: 0.

### False rejection — 39 answerable items

**Rate: 0.385 (15/39)** at the point this was measured. Two known defects, one fixed at that point:

| category | count | layer |
|---|---|---|
| Verifier scores correctly-cited claim `neutral` | 15/21 | verification — root cause: premise size |
| Citation tags left inline in scored text | 6/21 — fixed | plumbing |
| Generation cited wrong chunk | 0/21 | — |

**Citation-tag fix:** `verify_claim` was scoring claim text with citation tags intact — a trailing `[C1]` flipped entailment to neutral. `app/verify.py::strip_citation_tags` now normalizes before scoring. Scoring-side only.

**Neutral-scoring defect (root cause identified, not fixed by design):** Correctly-cited claims scoring `neutral` instead of `entailment`. Not threshold-tunable — most flagged items are confidently neutral (≥0.74), not near any reachable boundary. Root cause is premise size (median gold chunk ~1861 chars) — narrowing the premise to the relevant sentence flips scoring from neutral to entailment. Not a table-fragmentation problem — most affected items are prose chunks.

**Side effect of the tag-stripping fix on the primary metric:** False support rose slightly (1→2/16) as a metric artifact, not a trust regression. `state` becomes `"ok"` whenever *any* supported claim survives a response, so a correct refusal placed beside a correct, cited claim scores as a false support even though nothing false was asserted — this is a metric-definition issue, not a verifier failure.

### What the table fix demonstrably fixed

- **Zero `CONTRADICTED` verdicts on table content** (previously non-zero at high confidence).
- **Premise sanity restored** — the raw interleaved table scored an unrelated control hypothesis as a confident contradiction; linearized, it correctly scores neutral.
- **Granularity matters — one row per chunk.** Packing rows to a size budget re-breaks it (whole table in one chunk reintroduces false contradiction; isolating a row with its caption resolves to correct entailment).
- **No duplicate raw table remains** — a PDF-extraction bug caused the interleaved table to be duplicated in the corpus alongside the linearized rows; fixed, so table quotes now match exactly one chunk instead of two.
- **Dropped spaces restored** across ligature-adjacent tokens (e.g. `Thefirmware`, `provideflexibility`).

### Cost

| run | live calls | tokens (prompt/completion) | cost |
|---|---|---|---|
| Faithfulness, both sets (55 items) | 55 | 108,531 (8,320 cached) / 2,756 | $0.017309 |
| Re-run after record-format fix | 0 | — | $0 |
| Retrieval eval (×2) | 0 | — | $0 |

Re-runs are free when the dev cache key `(question, retrieved_chunk_ids, model)` is unchanged. Retrieval makes no LLM call at all.

---

## Reproducing

```bash
python -m eval.map_chunk_ids --write        # required after any chunking change
python -m eval.run_retrieval_eval
python -m eval.run_faithfulness_eval --set both --out eval/results/records.json
```

**Gotchas:**
- Run from **outside** the project dir with `PYTHONPATH` set — nltk's import guard blocks `regex` (needed by `SentenceSplitter`) if it sees an in-project `.venv/`.
- `tests/conftest.py` pins `LLM_PROVIDER=gemini` and blocks real OpenAI calls. A `.env` set to `openai` previously caused `test_claims.py` to send live billable requests while reporting a plain assertion failure.