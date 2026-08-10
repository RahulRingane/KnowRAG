# KnowRAG golden evaluation set

Labeled data for the retrieval and adversarial (faithfulness) evals described in
`app/KNowRAG_SPEC.md` §8.2 and §8.4. This directory is owned by agent A7a
(content authoring only — no harness/runner code lives here; see A7b for that).

Corpus: `notes.pdf` (repo root) — 28 pages of Embedded Systems study notes, TOC
sections 1.1–1.9.1. (§8.2 originally named `example_file.pdf` as the source
corpus; that file never existed in the repo — it was a placeholder — so
`notes.pdf` was substituted as the real corpus. This substitution was
pre-approved for this agent's work.)

---

## Two-phase labeling — read this before trusting `expected_chunk_ids`

Chunk identity in KnowRAG is `(document_id, chunk_index)`, canonicalized by
`chunk_key()` in `app/schemas.py`:

```python
def chunk_key(document_id: int, chunk_index: int) -> str:
    return f"{document_id}:{chunk_index}"
```

At the time this set was authored, **agent A2 was concurrently changing the
chunker** (`chunk_size=60, chunk_overlap=15` → `chunk_size=512,
chunk_overlap=64`), and `notes.pdf` had not yet been ingested under the new
settings. That means no real `(document_id, chunk_index)` pairs existed yet to
label against. Inventing plausible-looking IDs would have silently corrupted
every metric computed from this set later — so this file was built in two
phases instead:

**Phase 1 (this pass, done now).** Every retrieval item carries:
- `source_quote` — a verbatim span from the ligature-normalized PDF text (see below), copied from the
  extracted text, containing the answer.
- `source_section` — the human-readable TOC section it came from (e.g.
  `"1.8.2 The RISC Philosophy"`).
- `expected_chunk_ids: []` — an empty placeholder.

**Phase 2 (a follow-up pass, required after A2's chunking lands and `notes.pdf`
has actually been ingested).** For each item:
1. Ingest `notes.pdf` with the final chunker settings (`python app/pg.py` or
   the `/ingest` route once it exists).
2. For each `source_quote`, find which persisted chunk(s) in the `chunks`
   table contain that substring (`SELECT document_id, chunk_index FROM chunks
   WHERE chunk_text LIKE '%...%'` — match on a short, distinctive slice of the
   quote since some quotes contain non-ASCII ligature artifacts, see below).
3. Fill in `expected_chunk_ids` with the canonical `chunk_key(document_id,
   chunk_index)` string(s) for every chunk that contains the quoted span. A
   quote landing on a chunk boundary legitimately maps to two chunk IDs — that
   split is itself useful signal (see "Why the comparison tables" below).
4. Re-run this mapping any time chunking changes again — **a changed
   `chunk_size`/`chunk_overlap` invalidates every `expected_chunk_ids` value
   in this file**, because chunk boundaries move and old indices no longer
   point at the same text. Do not reuse stale chunk IDs after a re-chunk;
   regenerate them from `source_quote` + `source_section`, which are stable
   across chunking changes because they're derived from the PDF text itself,
   not from any particular chunker's output.

`eval/adversarial_set.jsonl` does not carry chunk IDs at all — adversarial
items are graded on the system's final `state` (`"ok"` vs
`"insufficient_evidence"`), not on which chunks were retrieved.

---

## Corpus-extraction quirk — normalized at ingestion, quotes match plain text

`notes.pdf` is a LaTeX-produced PDF whose embedded font maps ligatures (`fi`,
`ff`, `fl`, `ffi`, `ffl`) and apostrophes to Private Use Area Unicode
codepoints (`U+E000`-`U+E036`) instead of standard characters. Both
`pdftotext` (poppler) and `pypdf` hit this on the same font, so it is a
property of the source PDF, not of the extraction tool. Raw extraction yields
`speci<PUA>c` for "specific" and `<PUA>rmware` for "firmware" - 166 such
codepoints across the document.

**This is corrected at ingestion.** `normalize_ligatures()` in `app/pg.py`
runs before chunking, so the text stored in Postgres, Qdrant, and
Elasticsearch contains real characters. It was not a cosmetic fix: before
normalization the words "specific", "different", and "efficiency" had *zero*
plain-text occurrences in the corpus, so BM25 could never match a query
containing them, the embedding tokenizer saw unknown codepoints, and the
section 6 NLI verifier read corrupted premises. After normalization:
"specific" 36 occurrences, "firmware" 16, "classification" 13.

The mapping was derived empirically - expanding each candidate ligature and
keeping the one that produced a real dictionary word - not by assuming the
standard Unicode ligature order. `U+E000` is genuinely ambiguous (the document
uses more than one subsetted font, so it means `ff` in body text but `fi` in
headings) and is resolved per-occurrence against a wordlist, falling back to
`ff` where none is installed.

### What this means for the Phase 2 mapping pass

`source_quote` values are stored **ligature-normalized and
whitespace-collapsed** - matching the text that ends up in `chunks.chunk_text`,
not the raw PDF bytes. So:

- Apply `normalize_ligatures()` **and** `' '.join(text.split())` to chunk text
  before substring-matching a quote against it. Chunk text carries the PDF's
  original line wrapping; quotes do not.
- Do **not** match against raw `pdftotext`/`pypdf` output - that still contains
  the PUA codepoints and will fail.

All 39 retrieval quotes are verified as exact substrings of the normalized,
whitespace-collapsed corpus. Re-run that check after any change to
`normalize_ligatures()` or to the extraction path.
## `eval/retrieval_set.jsonl` schema

One JSON object per line:

```json
{
  "id": "ret-010",
  "question": "Per Table 1.2, does RISC use an orthogonal or non-orthogonal instruction set?",
  "expected_answer": "RISC uses an orthogonal instruction set ...; CISC's instruction set is non-orthogonal ...",
  "source_section": "1.8.2 The RISC Philosophy",
  "source_quote": "Orthogonal instruction set (Allows",
  "expected_chunk_ids": [],
  "expected_state": "ok"
}
```

| Field | Meaning |
|---|---|
| `id` | Stable identifier, `ret-NNN`. |
| `question` | The eval question, answerable from `notes.pdf` alone — no outside embedded-systems knowledge required or assumed. |
| `expected_answer` | A short reference answer for a human/LLM grader to compare against. Not a substring guarantee — a paraphrase is fine as long as it's faithful to `source_quote`. |
| `source_section` | TOC section the answer comes from, e.g. `"1.6.1 Operational Quality Attributes"`. |
| `source_quote` | Verbatim span containing the answer, taken from the **ligature-normalized** corpus (`normalize_ligatures()` in `app/pg.py`) with whitespace collapsed to single spaces. Match it against chunk text that has had the same two transforms applied — see the extraction-quirk section. |
| `expected_chunk_ids` | **Placeholder — always `[]` in this phase.** Populated in Phase 2 with `chunk_key()`-formatted strings. |
| `expected_state` | `"ok"` for every item in this file (all 39 questions have real answers in the corpus) — included so the harness can use one shared verdict field across both eval files. |

39 items, deliberately concentrated on the document's three
fragmentation-sensitive comparison structures (see below), with the remaining
items spread across every other TOC section for general coverage.

### Why the comparison tables get the concentrated coverage

`ret-001`–`ret-007` (Table 1.1, Embedded vs. General Computing Systems) and
`ret-008`–`ret-013` (Table 1.2, RISC vs. CISC) target individual rows of
two-column tables. In the PDF's linear-extracted text, these tables render as
two columns interleaved line-by-line (poppler's `-layout` mode approximates
the visual columns positionally) — a table row's left-column cell and
right-column cell sit on the *same* extracted line, and a cell that wraps to a
second line pulls the *other* column's second line along with it. This means:

- The quotes here are deliberately short (often a single physical line,
  sometimes truncated mid-word, e.g. `"A large number of registers are
  avail"`) rather than the full row — extending them further risks the quote
  silently splicing in the *other* column's text, which would misrepresent
  what the row actually says.
- These are exactly the passages where a naive chunker is most likely to
  split a row (or an entire column) across two chunks, so hybrid retrieval
  returns half a comparison — this is the fragmentation failure mode §8.2
  wants surfaced, and it is why every row of both tables has at least one
  question against it.
- `ret-016`–`ret-025` do the same for the Operational (1.6.1) and
  Non-Operational (1.6.2) Quality Attributes lists — not a rendered table, but
  a dense enumerated structure with the same splitting risk (e.g. Security's
  "Confidentiality, Integrity, Availability" triad, or the MTBF/MTTR pair
  under Reliability).

---

## `eval/adversarial_set.jsonl` schema

One JSON object per line:

```json
{
  "id": "adv-007",
  "category": "unsupported_combination",
  "question": "Since ARM processors are specifically designed for low power consumption and DSPs are 2 to 3 times faster than general purpose microprocessors in signal processing, how much faster and more power-efficient is an ARM-based DSP than a standard microprocessor?",
  "rationale": "The document discusses ARM's low-power design goals (§1.8.3) and DSP speed separately (§1.7.1) — it never combines these into a joint figure ...",
  "near_miss_section": "1.7.1 Core of the Embedded Systems / 1.8.3 ARM Design",
  "expected_state": "insufficient_evidence"
}
```

| Field | Meaning |
|---|---|
| `id` | Stable identifier, `adv-NNN`. |
| `category` | One of the three §8.4 categories (see below). |
| `question` | The adversarial prompt — phrased to tempt fabrication. |
| `rationale` | Why the correct answer is "insufficient evidence": what's actually in the document vs. what the question is fishing for. |
| `near_miss_section` | For `adjacent_absent` and `unsupported_combination`: the section(s) containing the real (but insufficient) information the question is baiting against. `null` for `out_of_scope` items, which have no relevant section at all. |
| `expected_state` | `"insufficient_evidence"` for every item — the system under test must reject all 16 of these, not answer them. A system that returns `"ok"` (i.e. produces a `SUPPORTED` claim) on any of these has a false-support — the metric §8.4 says to track as primary. |

16 items across the three named categories from §8.4, roughly balanced:

| Category | Count | What it tests |
|---|---|---|
| `adjacent_absent` | 6 | Numbers/dates that sound plausible and near real figures in the text (e.g. FPGA gate counts, Thumb's stated 30% code-density figure) but are never actually stated for the specific thing asked about. Tests whether generation interpolates a number instead of saying "not found." |
| `unsupported_combination` | 5 | Questions that invite stitching two real, independently-true statements from unrelated sections into a conclusion neither supports — the exact failure §5.2 rule 3 names ("Do not combine two chunks to produce a conclusion neither one supports on its own"). |
| `out_of_scope` | 5 | Entities/topics (RISC-V, Kubernetes, Raspberry Pi, the capital of France, LLM architectures) entirely absent from the document — the clean rejection path, no plausible-sounding bait involved. |

---

## Regenerating chunk IDs (Phase 2 — run after any chunking change)

Whenever `app/pg.py`'s `SentenceSplitter` settings change (chunk size,
overlap, or splitter type), `expected_chunk_ids` in `retrieval_set.jsonl` must
be regenerated. There is no code in this directory to do it — that mapping
pass belongs to whoever runs the eval next (A7b's harness, or a future pass on
this same file), and should:

1. Ingest `notes.pdf` fresh under the current chunker settings.
2. For each of the 39 `retrieval_set.jsonl` entries, locate every chunk in
   Postgres whose `chunk_text` contains the entry's `source_quote` (or a
   slice of it — see "Corpus-extraction quirk" above for the required normalization).
3. Write `expected_chunk_ids` as a list of `chunk_key(document_id,
   chunk_index)` strings — use the shared `chunk_key()` helper in
   `app/schemas.py`, don't hand-build the `"{document_id}:{chunk_index}"`
   string anywhere else, per that module's own docstring.
4. Commit the regenerated file with a note of which chunker settings it was
   generated against (chunk_size/chunk_overlap), so a future reader can tell
   at a glance whether the labels are still current.

**Do not skip step 4.** A `retrieval_set.jsonl` with `expected_chunk_ids`
filled in from a stale chunking run is worse than one left empty — it will
compute a confident-looking recall@k number that is silently measuring the
wrong thing.

`eval/map_chunk_ids.py` now performs this pass:

```
python -m eval.map_chunk_ids            # dry run, report only
python -m eval.map_chunk_ids --write    # write expected_chunk_ids back
```

---

## Current labels

**Generated against `chunk_size=512`, `chunk_overlap=64`** (`app/pg.py`),
over `notes.pdf` ingested as `document_id=1` — 29 chunks. **36 of 39 items
are labeled**; each resolved to exactly one chunk, and 15 distinct chunks
are referenced. Regenerate after any chunking or normalization change.

### The 3 unlabeled items, and why

These carry `expected_chunk_ids: []` and are **excluded from recall@k by
the harness rather than counted as misses** — scoring an item whose label
could not be produced would understate retrieval quality for a reason that
has nothing to do with retrieval.

| Item | Cause |
|---|---|
| `ret-025` | Corpus reads `"advantage of newfirmware"`. pypdf drops the space before a word that begins with a ligature glyph, fusing it to the previous word. |
| `ret-034` | Same defect: `"the world'sfirst"`. |
| `ret-006` | Label problem, not a corpus problem. The quote `"sys- Response requirements are not time"` splices two *columns* of Table 1.1 together — it is contiguous in poppler's `-layout` output but is not a contiguous span in any linearization pypdf produces. The quote needs re-authoring against the real ingestion path. |

The first two are the residue of a wider extraction issue: the quotes were
authored from poppler output, ingestion runs on pypdf, and the two disagree
about spacing around ligatures and hyphenation. Ingestion now repairs the
tractable half of that disagreement (see below); the remaining space-drops
would need a different PDF extractor to fix at the source.

## Matching is token-based, not character-based

Because of that same poppler/pypdf disagreement, `map_chunk_ids.py`
compares the **alphanumeric token sequence** of a quote against that of a
chunk — punctuation and whitespace all collapse to single spaces. The full
word sequence must still appear, in order. Neither the corpus nor the
authored `source_quote` is rewritten; the tolerance exists only at match
time.

## Normalization fixes applied since this file was written

`normalize_ligatures()` in `app/pg.py` originally deleted `U+E005`,
`U+E035`, and `U+E036` as "dashes/bullets that carry no letters". They are
the document's curly **quotation marks**, and the extractor emits no space
around them, so deleting one fused the neighbouring words —
`"in terms ofBenchmark"` was indexed as the single token
`ofbenchmark`. They now map to `"`.

Two further corpus defects were fixed at the same time:

- **Line-break hyphenation** was never rejoined: 78 occurrences, so
  `"microproces-\nsors"` indexed as `microproces` + `sors` and no query for
  "microprocessors" could match. `dehyphenate()` now rejoins them, keeping
  the hyphen only where the joined form is not a word but the prefix is
  (`"non-alterable"`).
- **The wordlist was missing from the Docker image**, so the ambiguous
  `U+E000` fell back to `ff` for every occurrence and the *container's*
  corpus contained `Classiffcation` and `Deffnition` while a host ingest
  produced the correct words. The Dockerfile now installs `wamerican`.

Measured on `notes.pdf` after these fixes: 0 PUA codepoints remaining, 0
unresolved line-break hyphens, `"Classification"` 1 → 10 occurrences,
`"microprocessors"` 0 → 7.
