# KnowRAG Golden Evaluation Set

Labeled data for retrieval and adversarial (faithfulness) evals — see `app/KNowRAG_SPEC.md` §8.2, §8.4.
Owned by agent A7a (content only; harness code is A7b's).

**Corpus:** `data.pdf` (28 pages, Embedded Systems notes, sections 1.1–1.9.1). Originally spec'd as `example_file.pdf` (never existed); `data.pdf` substituted and pre-approved.

---

## Two-Phase Labeling

Chunk identity = `(document_id, chunk_index)`, formatted via `chunk_key()` in `app/schemas.py`.

A2 was concurrently changing the chunker (`60/15` → `512/64` size/overlap) while this set was authored, so no real chunk IDs existed to label against yet. Hence two phases:

- **Phase 1 (done):** Each item has `source_quote` (verbatim span, normalized text), `source_section`, and `expected_chunk_ids: []`.
- **Phase 2 (done, via `eval/map_chunk_ids.py`):** After ingestion, map each quote to its actual chunk(s).
  - Run: `python -m eval.map_chunk_ids [--write]`
  - **Any chunker setting change invalidates all `expected_chunk_ids`** — re-run Phase 2. `source_quote`/`source_section` stay valid since they're derived from the PDF, not the chunker.

`adversarial_set.jsonl` has no chunk IDs — graded on final `state` only.

---

## Corpus Extraction Quirk

`data.pdf`'s LaTeX font maps ligatures (fi/ff/fl/ffi/ffl) and apostrophes to Private Use Area codepoints instead of standard characters (affects both `pdftotext` and `pypdf` — a PDF property, not a tool bug). Raw extraction breaks words like "specific" → `speci<PUA>c`.

**Fixed at ingestion** via `normalize_ligatures()` in `app/pg.py`, run before chunking. Before the fix, words like "specific," "different," and "efficiency" had zero plain-text matches anywhere in the corpus — breaking BM25, the embedding tokenizer, and the NLI verifier.

**Implication for matching:** `source_quote` values are ligature-normalized and whitespace-collapsed. Apply the same transforms to chunk text before substring matching. Never match against raw `pdftotext`/`pypdf` output.

---

## `eval/retrieval_set.jsonl`

39 labeled items, one JSON object per line:

```json
{
  "id": "ret-010",
  "question": "Per Table 1.2, does RISC use an orthogonal or non-orthogonal instruction set?",
  "expected_answer": "RISC uses an orthogonal instruction set ...",
  "source_section": "1.8.2 The RISC Philosophy",
  "source_quote": "Orthogonal instruction set (Allows",
  "expected_chunk_ids": [],
  "expected_state": "ok"
}
```

| Field | Meaning |
|---|---|
| `id` | `ret-NNN` |
| `question` | Answerable from `data.pdf` alone |
| `expected_answer` | Reference answer for grading (paraphrase OK) |
| `source_section` | TOC section |
| `source_quote` | Normalized verbatim span containing the answer |
| `expected_chunk_ids` | Populated in Phase 2 |
| `expected_state` | Always `"ok"` (shared verdict field with adversarial set) |

**Coverage:** Concentrated on the doc's three fragmentation-prone structures — Table 1.1 (Embedded vs. General Computing, `ret-001`–`007`), Table 1.2 (RISC vs. CISC, `ret-008`–`013`), and the Quality Attributes lists (`ret-016`–`025`). These are two-column tables/dense lists where linear PDF extraction interleaves rows — the exact case where a naive chunker splits a row across chunks. Quotes here are deliberately short/single-line to avoid splicing in the adjacent column's text.

---

## `eval/adversarial_set.jsonl`

16 items, one JSON object per line:

```json
{
  "id": "adv-007",
  "category": "unsupported_combination",
  "question": "How much faster and more power-efficient is an ARM-based DSP than a standard microprocessor?",
  "rationale": "ARM's low-power goals (§1.8.3) and DSP speed (§1.7.1) are discussed separately, never combined into a joint figure.",
  "near_miss_section": "1.7.1 / 1.8.3",
  "expected_state": "insufficient_evidence"
}
```

| Category | Count | Tests |
|---|---|---|
| `adjacent_absent` | 6 | Plausible-sounding but unstated numbers (e.g. FPGA gate counts) |
| `unsupported_combination` | 5 | Stitching two true but unrelated statements into an unsupported conclusion |
| `out_of_scope` | 5 | Entities absent from the doc entirely (RISC-V, Kubernetes, etc.) |

All 16 expect `"insufficient_evidence"`. A system returning `"ok"` on any of these is a false-support — §8.4's primary tracked metric.

---

## Current Status

Generated against `chunk_size=512, chunk_overlap=64`, `data.pdf` as `document_id=1` (29 chunks).

- **36 / 39** retrieval items labeled (1 chunk each, 15 distinct chunks referenced)
- **3 unlabeled** (excluded from recall@k, not counted as misses):

| Item | Cause |
|---|---|
| `ret-025` | pypdf drops space before ligature-initial words (`"newfirmware"`) |
| `ret-034` | Same defect (`"world'sfirst"`) |
| `ret-006` | Quote splices two Table 1.1 columns — contiguous in poppler's `-layout` output only, not in pypdf. Needs re-authoring. |

**Matching is token-based, not character-based:** `map_chunk_ids.py` compares alphanumeric token sequences (punctuation/whitespace collapsed) to tolerate poppler/pypdf spacing disagreements. Source files are never rewritten.

### Normalization fixes applied since authoring

- `U+E005`/`U+E035`/`U+E036` were being deleted as bullets/dashes; they're actually curly quote marks with no surrounding space, so deletion fused words (`"ofbenchmark"`). Now mapped to `"`.
- **Hyphenation** (78 occurrences, e.g. `"microproces-\nsors"`) now rejoined via `dehyphenate()`.
- **Missing wordlist** in the Docker image caused ambiguous `U+E000` to always resolve to `ff` (e.g. `Classiffcation`) in containerized ingests only. Fixed by installing `wamerican`.

Post-fix, on `data.pdf`: 0 PUA codepoints, 0 unresolved hyphens, "Classification" 1→10 occurrences, "microprocessors" 0→7.