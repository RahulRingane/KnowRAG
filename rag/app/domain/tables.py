"""Recovering comparison tables from positioned text — pure geometry, no PDF.

Extracted from the old `app/pg.py`. Everything here operates on a list of
`TextOp` tuples — `(x, y, text)` — and knows nothing about pypdf, files, or
databases. Producing those tuples from an actual PDF page is
`app.infrastructure.pdf`'s job; deciding whether they form a table, and what
that table means, is this module's.

That split is worth making explicitly, because the rules below are the
expensive part. They were derived by measuring this corpus and are the
reason the verifier works at all on table content:

A two-column comparison table extracted as plain text is not merely ugly,
it is actively poisonous to the §6 NLI verifier. `page.extract_text()`
emits the cells in content-stream order with nothing marking where one ends
and the next begins, so Table 1.1 arrives as a wall of alternating
half-sentences. Measured against that premise, `cross-encoder/nli-deberta-
v3-base` does not merely score poorly — it degenerates: the unrelated
hypothesis "The Eiffel Tower is located in Paris." comes back
*contradiction 0.78*. A premise that confidently contradicts an unrelated
sentence is out of distribution, so every score against it is noise, and
every claim drawn from that table gets rejected no matter how faithful it
is (measured: 6 of 6, two of them as CONTRADICTED at 0.60 and 0.88).

Rewriting each row as one self-contained line fixes it outright. Same model,
same claims, against the linearized form:

    compound true claim -> entailment    0.997   (was contradiction 0.68)
    atomic true claim   -> entailment    0.988   (was contradiction 0.85)
    false claim         -> contradiction 0.995
    unrelated           -> neutral       0.993   (was contradiction 0.78)

Row-level, specifically. Emitting one premise per *cell* was also measured
and it does not work: a single cell scores the compound comparative claim
neutral 0.999, because half a comparison genuinely cannot entail one. The
LLM generates comparative claims from a comparison table, so the row is the
smallest unit that can support them.
"""

from __future__ import annotations

import re

from app.domain.text_normalization import normalize_ligatures

# One text-showing operation: where it was drawn, and what it said.
TextOp = tuple[float, float, str]

_TABLE_CAPTION_RE = re.compile(r"Table\s+\d+\.\d+:")

# Column anchors must be far apart. Nested lists in this document sit at
# x=168.8/188.1/220.6 — tens of points apart — while the real table columns
# are ~500pt apart. Requiring a wide gap is what keeps an indented list from
# being read as a table.
_MIN_COLUMN_GAP = 200.0

# A cell run has to recur to count as a column, so a one-off stray op at an
# unusual x cannot invent a column.
_MIN_RUNS_PER_COLUMN = 2


def _group_runs(ops: list[TextOp]) -> list[tuple[float, list[str]]]:
    """Collapse consecutive same-x ops into `(x, lines)` cell runs.

    LaTeX emits a table one cell at a time — every line of the left cell,
    then every line of the right cell, then the next row — so a run of
    consecutive ops sharing an x *is* a cell. This ordering is what makes row
    reconstruction possible: line spacing within a cell (~34.9pt) and between
    rows (~35.7pt) are too close to separate on y alone.
    """
    runs: list[tuple[float, list[str]]] = []
    for x, _y, text in ops:
        if runs and runs[-1][0] == x:
            runs[-1][1].append(text)
        else:
            runs.append((x, [text]))
    return runs


def _clean_cell(lines: list[str]) -> str:
    """Join one cell's lines into a single normalized sentence-like string."""
    # Joined raw so the trailing newlines pypdf leaves on each line survive
    # into `normalize_ligatures`, whose `dehyphenate` step needs them to
    # rejoin "embed-\nded".
    text = normalize_ligatures("".join(lines))
    return re.sub(r"\s+", " ", text).strip()


def detect_table(
    ops: list[TextOp],
) -> tuple[str, list[str], list[list[str]], set[int]] | None:
    """Recover `(caption, headers, rows, consumed_op_indices)`, or `None`.

    Three signals must agree, because any one alone misfires on this corpus:
    a `Table N.N:` caption, two column anchors separated by more than
    `_MIN_COLUMN_GAP`, and at least one body row that has text in both
    columns. The third is the one that distinguishes a table from an indented
    list — list items at different x sit at *disjoint* y, while a table's
    cells share a row.

    `consumed_op_indices` is what lets the caller keep the rest of the page.
    Page 22 carries several paragraphs of prose *below* Table 1.2; returning
    only the table would silently drop them from the corpus, trading one
    retrieval defect for another.
    """
    caption_index, caption_op = next(
        ((i, op) for i, op in enumerate(ops) if _TABLE_CAPTION_RE.search(op[2])),
        (None, None),
    )
    if caption_op is None:
        return None

    # Everything below the caption; y grows downward in this file.
    body = [(i, op) for i, op in enumerate(ops) if op[1] > caption_op[1]]
    runs = _group_runs([op for _i, op in body])

    counts: dict[float, int] = {}
    for x, _lines in runs:
        counts[x] = counts.get(x, 0) + 1
    candidates = sorted(x for x, n in counts.items() if n >= _MIN_RUNS_PER_COLUMN)
    if len(candidates) < 2:
        return None

    left, right = candidates[0], candidates[-1]
    if right - left < _MIN_COLUMN_GAP:
        return None

    # Walk the runs, starting a new row each time the left column comes back
    # around. Ops outside the two anchors (running headers, figure captions,
    # body prose further down the page) are simply not table content, and
    # stay behind for the caller to chunk as prose.
    rows: list[list[str]] = []
    consumed: set[int] = {caption_index}
    row_op_indices: list[list[int]] = []
    position = 0
    for x, lines in runs:
        indices = [body[position + offset][0] for offset in range(len(lines))]
        position += len(lines)
        if x not in (left, right):
            continue
        if x == left:
            rows.append([_clean_cell(lines), ""])
            row_op_indices.append(list(indices))
        elif rows:
            rows[-1][1] = _clean_cell(lines)
            row_op_indices[-1].extend(indices)

    keep = [
        (row, indices)
        for row, indices in zip(rows, row_op_indices)
        if row[0] and row[1]
    ]
    if len(keep) < 2:  # a header plus at least one body row
        return None

    for _row, indices in keep:
        consumed.update(indices)

    caption = _clean_cell([caption_op[2]])
    return caption, keep[0][0], [row for row, _ in keep[1:]], consumed


def linearize_table(caption: str, headers: list[str], rows: list[list[str]]) -> str:
    """Render a detected table as one premise-shaped block per row.

    Column headers repeat on every row rather than being stated once at the
    top: a row has to stay verifiable on its own, because the chunker may put
    it in a different chunk from the caption, and a cell whose column is
    unknown cannot support a claim about which side of the comparison it
    describes.
    """
    lines = [caption]
    for index, row in enumerate(rows, start=1):
        cells = " | ".join(f"{h}: {c}" for h, c in zip(headers, row))
        lines.append(f"Row {index} | {cells}")
    return "\n".join(lines)


def table_row_chunks(block: str) -> list[str]:
    """One chunk per row: caption, then that row and nothing else.

    `SentenceSplitter` is deliberately not used — it splits on sentence
    boundaries, and half a row is exactly the malformed premise this whole
    function exists to prevent.

    Packing several rows per chunk was tried first and measured worse, which
    is not obvious and is the reason this function exists rather than a size
    budget. A chunk holding the whole 7-row table is *still* a broken premise:
    the six rows irrelevant to a given claim are enough to push the NLI model
    back into confident nonsense, just less severely than the raw extraction
    did. Same three claims, same model, only the premise size differing:

        premise = whole table (7 rows)   -> contradiction 0.96 / 0.91 / 0.96
        premise = caption + its own row  -> entailment    0.997 / 0.997 / 0.989

    So the row is not merely the smallest unit that *can* support a
    comparative claim (see `linearize_table`) — it is also the largest one
    that reliably does. A row that is itself enormous still ships whole:
    splitting it would break the comparison it exists to state.
    """
    lines = block.split("\n")
    caption, rows = lines[0], lines[1:]
    return [f"{caption}\n{row}" for row in rows]
