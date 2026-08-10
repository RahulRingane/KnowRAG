# KnowRAG evaluation baseline

> ## Current numbers — 2026-08-03, end of session
>
> The sections below this box predate the OQ-4 metric redefinition and the
> OQ-3 verifier fix. They are kept because their confound analysis is still
> the reason those numbers read as they do. **These are the current figures:**
>
> | metric | value | vs. previous |
> |---|---|---|
> | **false support — D1 (primary)** | **0.000** (0/16) | 0.000 — unchanged |
> | D4 bait-rejected | **1.000** (16/16) | 0.938 — `adv-002` resolved by OQ-5 |
> | old metric (`state == "ok"`, bridge only) | 0.500 (8/16) | 0.312 — rises by design, see below |
> | **false rejection** | **0.000** (0/39) | 0.385 — **all 15 recovered** |
> | NLI calls per run | 112 (1.67x) | 66 — long-premise fallback only |
> | adversarial items with an explicit refusal | **16/16** | n/a — the field is new |
>
> Records: `records-2026-08-03-oq5-kind.json`. Generation `gpt-4o-mini`, 42-chunk
> corpus, verifier `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` (OQ-3
> Direction D) plus the OQ-3R length-gated narrowing fallback, and claims
> carrying an explicit `kind: assertion | refusal` (OQ-5).
>
> **The old metric rising to 0.500 is by design, not a regression.** A refusal
> beside a supported assertion is now the intended output for a
> partially-answerable question, so `state == "ok"` fires more often while D1 —
> the metric that asks whether anything unsupported was *asserted* — holds at
> 0.000.
>
> **Read the primary metric as D1, not as `state == "ok"`.** The old number is
> printed alongside as a bridge only; it counts a correct refusal that shipped
> beside a true cited claim as a false support, which is what all four of the
> items it flags here are. See PLAN.md OQ-4.
>
> **The false-rejection drop is entirely verification-side.** Generation,
> retrieval and corpus are identical to the run above; only the NLI checkpoint
> changed (0.385 → 0.051) and then a length-gated narrowing fallback recovered
> the last two items (0.051 → 0.000). No prompt, corpus or model change is
> involved, and D1 stayed 0.000 throughout.


Measured 2026-08-03 against the local compose stack (`notes.pdf`,
document_id 1, **42 chunks** across Postgres / Qdrant / ES), with A8
instrumentation in place.

**Generation model: `gpt-4o-mini` (OpenAI).** Previous baseline:
`gemini-3.6-flash`.

---

## Read this before reading the numbers

**This run changed two things at once. Do not attribute the faithfulness
improvement to the table fix.**

Since the 2026-08-02 baseline, both of these changed:

1. **The generation model**, `gemini-3.6-flash` → `gpt-4o-mini`.
2. **The corpus**, 29 → 42 chunks — `app/pg.py` now detects two-column PDF
   tables and linearizes them one row per chunk, and a ligature-space
   extraction bug was fixed (33 dropped spaces restored).

The primary metric moved from **0.312 → 0.062** (and to 0.125 after the
OQ-2 fix, for reasons unrelated to either change below). That is a real measurement,
but it is *confounded*, and the most likely reading is that it is mostly the
model:

- 0.062 (1/16) is **identical** to the older `gemini-2.5-flash` baseline of
  0.063 (1/16).
- The 2026-08-02 baseline already argued at length that its own 0.312 was
  not a genuine trust regression — in all 5 flagged items the unsupported
  combination the item was authored to bait *was explicitly rejected*, and
  what flipped each response to `ok` was a different, true, correctly cited
  claim surviving alongside the refusal.

Taken together: the earlier 0.312 looks substantially like a
`gemini-3.6-flash` artifact, and this run's 0.062 looks like a return to the
long-run level rather than an improvement caused by the corpus work.

**The table fix's isolated contribution to faithfulness is not measured by
this run and should not be claimed from it.** Isolating it needs the corpus
fix re-run on `gemini-3.6-flash`, or the old corpus re-run on
`gpt-4o-mini` — neither has been done.

What the table fix *is* directly evidenced to have changed is narrower and
sits below, under [Retrieval](#retrieval--recallk) and
[What the table fix demonstrably fixed](#what-the-table-fix-demonstrably-fixed).

---

## Retrieval — recall@k

**39 of 39 items labeled** (previous baseline: 36 of 39).

| path | r@1 | r@3 | r@5 | r@10 | r@20 |
|---|---|---|---|---|---|
| semantic | 0.872 | 0.974 | 0.974 | 1.000 | 1.000 |
| keyword | 0.821 | 0.897 | 1.000 | 1.000 | 1.000 |
| hybrid | **0.923** | **1.000** | 1.000 | 1.000 | 1.000 |

Previous baseline: semantic 0.778 / keyword 0.750 / hybrid 0.917 at r@1.

Retrieval makes no LLM call, so unlike the faithfulness numbers this
comparison is **not** confounded by the model change. It is still not a
clean A/B: the corpus and the label set both changed, and the denominator
went from 36 to 39 items.

The label-set growth is itself a result of the table fix. Two items
(`ret-025`, `ret-034`) had been unlabelable because their `source_quote`
spanned the interleaved column text; they map cleanly now. The third,
`ret-006`, needed its gold quote repaired — its stored quote
(`'sys- Response requirements are not time'`) was an artifact of the broken
extraction, spanning a hyphenated line break *and* a column boundary that no
longer exist. It now reads `'Response requirements are not time critical'`
and resolves to a single chunk.

Read r@1/r@3 only. The corpus is 42 chunks, so k=20 covers 48% of it and
r@20 = 1.000 is close to arithmetically guaranteed.

## Faithfulness — 16 adversarial items

**False-support rate (primary): 0.125 — 2/16** (0.062 — 1/16 before the OQ-2
fix; see [the side-effect note](#side-effect-of-the-oq-2-fix-on-the-primary-metric),
which explains why this is a metric artifact rather than a trust regression).
Previous baseline (`gemini-3.6-flash`): 0.312 — 5/16.
Older baseline (`gemini-2.5-flash`): 0.063 — 1/16.

See [the confound note](#read-this-before-reading-the-numbers) before
comparing these.

| category | false supports |
|---|---|
| unsupported_combination | 2/5 |
| adjacent_absent | 0/6 |
| out_of_scope | 0/5 |

Errored items: 0. Flagged: `adv-009`, `adv-011`.

## False rejection — 39 answerable items

**False-rejection rate (secondary): 0.385 — 15/39** (was 0.538 — 21/39).

**This is not "false rejection mostly resolved."** One of the two underlying
defects was fixed; the larger one is untouched. Original categorization of
all 21 items, and where they stand:

| category | before | after | layer |
|---|---|---|---|
| Verifier scores a correctly-cited claim `neutral` | 15 / 21 (71.4%) | **15 — all still open (OQ-3)** | verification |
| Citation tags left inline in the scored claim text | 6 / 21 (28.6%) | 0 — fixed (OQ-2) | plumbing |
| Generation cited the wrong chunk | 0 / 21 | 0 | — |

**Fixed (OQ-2).** `verify_claim` scored `claim.text` verbatim, tags and all,
and a trailing `[C1]` flipped entailment to neutral (`ret-002` neutral 0.995
→ entailment 0.998 once stripped). `app/verify.py::strip_citation_tags` now
normalizes the hypothesis before scoring. Scoring-side only — the verdict
still carries the tags, so §6.4 is unchanged. All 6 predicted items flipped;
zero tag-contamination cases remain.

**Still open, and now the whole number (OQ-3).** The remaining 15 cite the
correct gold chunk, have no tag problem, and are still not entailed.
**Not threshold-tunable:** only `ret-004` (0.479) and `ret-030` (0.531) sit
near 0.55; the other 13 are *confidently* neutral (≥0.74, most ≥0.98), so no
reachable threshold recovers them and lowering it would worsen the primary
metric.

These are **not** a table problem — 14 of the 15 cite prose chunks, and
`ret-004` is the only table-derived item. The measured cause is premise
size, the same effect as OQ-1's in a different surface: gold chunks here
have a median of 1861 chars, and narrowing the premise to the sentence
region the claim is about flips chunk `1:22` from neutral 0.994 to
entailment 0.993. Deliberately unfixed; see PLAN.md OQ-3.

### Side effect of the OQ-2 fix on the primary metric

False support moved **0.062 → 0.125** (1/16 → 2/16). This is a metric
artifact, not a trust regression. The new item is `adv-009`; the claim that
flipped it is true and correctly cited — chunk `1:20` ends "For embedded
system with critical application need, it should be of the order of
minutes", and the claim says exactly that (entailment 0.978 stripped vs
neutral 0.763 tagged). The bait combination was explicitly rejected twice.

`state` becomes `ok` whenever any supported claim survives, so a correct
refusal *plus* a correct supported claim scores as a false support — the
same composition artifact the 2026-08-02 baseline documented at length for
its own 5 flagged items. The metric definition is the issue, not the
verifier.

## What the table fix demonstrably fixed

Separated out from the confounded metrics above, because these are directly
attributable:

- **Zero `CONTRADICTED` verdicts on table-derived content**, against 2 of 6
  (at 0.60 and 0.88) on the same question before the fix. OQ-1 is closed.
- **Premise sanity restored.** On the raw interleaved Table 1.1 chunk the
  NLI model scored the unrelated hypothesis *"The Eiffel Tower is located in
  Paris."* as `contradiction 0.78` — a premise that confidently contradicts
  an unrelated sentence is out of distribution, so every score against it
  was noise. Linearized, the same hypothesis scores `neutral 0.99`.
- **Granularity matters and is fixed at one row per chunk.** Packing rows to
  a size budget re-breaks it: whole 7-row table → `contradiction 0.96`;
  caption + its own row → `entailment 0.997`.
- **No duplicate raw table remains.** pypdf fires its text visitor once more
  at end-of-page with the entire page as one op; that op was never consumed
  by the table detector, so a verbatim copy of the interleaved table was
  landing in the corpus alongside the linearized rows. Caught because every
  table-derived eval quote matched two chunks; after the fix, zero do.
- **33 dropped spaces restored** (`Thefirmware`, `provideflexibility`,
  `Single,fixed`), across 24 distinct tokens.

## Cost

| run | live calls | tokens (prompt / completion) | cost |
|---|---|---|---|
| Faithfulness, both sets (55 items) | 55 | 108,531 (8,320 cached) / 2,756 | **$0.017309** |
| Re-run after fixing the record format | 0 | — | $0.000000 |
| Retrieval eval (×2) | 0 | — | $0.000000 |

The re-run cost nothing because the dev cache keys on
`(question, retrieved_chunk_ids, model)` and the corpus was unchanged.
Retrieval makes no LLM call at all.

## Reproducing

```bash
python -m eval.map_chunk_ids --write        # required after any chunking change
python -m eval.run_retrieval_eval
python -m eval.run_faithfulness_eval --set both --out eval/results/records-2026-08-03.json
```

Per-item records: `eval/results/records-2026-08-03.json`.

Two environment notes, both of which cost time to rediscover:

- Run from **outside** the project directory (with `PYTHONPATH` set). nltk's
  import guard treats an in-project `.venv/` as "inside the CWD" and blocks
  `regex`, which `SentenceSplitter` needs.
- The test suite pins `LLM_PROVIDER=gemini` in `tests/conftest.py` and blocks
  real OpenAI client construction. A `.env` selecting `openai` previously made
  four `test_claims.py` tests send live billable requests while reporting an
  ordinary assertion failure.
