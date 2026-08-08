"""Phase 2 of `eval/README.md`: resolve `source_quote` spans to real chunk IDs.

`eval/retrieval_set.jsonl` ships with `expected_chunk_ids: []` on every item.
It has to: chunk identity is `(document_id, chunk_index)`, which does not
exist until a corpus has actually been ingested under a specific
`chunk_size`/`chunk_overlap`, and inventing plausible IDs would produce a
confident-looking recall@k measuring nothing.

This script closes that gap. For each item it finds every ingested chunk
whose text contains the item's `source_quote` and writes the canonical
`chunk_key()` strings back into `expected_chunk_ids`.

**Re-run this after any chunking change.** Moving `chunk_size` or
`chunk_overlap` moves every boundary, so previously-correct indices silently
start pointing at different text — per `eval/README.md`, stale labels are
worse than empty ones.

Matching follows the normalization contract in `eval/README.md`: quotes are
stored ligature-normalized and whitespace-collapsed, and chunk text carries
the PDF's original line wrapping, so both sides get `' '.join(text.split())`
before the substring test. `normalize_ligatures()` has already been applied
to chunk text at ingestion (`app/domain/text_normalization.py`), but it is
applied again here —
idempotently — so this script still works against a corpus ingested before
that fix landed.

Usage (read-only by default, so you can inspect the mapping first):

    python -m eval.map_chunk_ids            # report what would change
    python -m eval.map_chunk_ids --write    # write expected_chunk_ids back
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from app.domain.chunking import splitter
from app.domain.models import chunk_key
from app.domain.text_normalization import normalize_ligatures
from app.infrastructure.db.orm import ChunkRow as Chunk
from app.infrastructure.db.session import SessionLocal

RETRIEVAL_SET = Path(__file__).resolve().parent / "retrieval_set.jsonl"


def collapse(text: str) -> str:
    """The normalization both sides of the substring test must agree on.

    Reduces text to its alphanumeric token sequence: punctuation and
    whitespace all become single spaces. That is deliberately looser than
    `eval/README.md`'s original "whitespace-collapsed" rule, because the
    labels and the corpus disagree on punctuation for reasons that have
    nothing to do with which chunk a passage lives in — the quotes were
    authored from poppler's extraction, while ingestion goes through pypdf,
    and the two render the document's curly quotes and line-break
    hyphenation differently.

    Matching on tokens rather than characters means a punctuation
    difference can no longer cost an item its label, while still requiring
    the full word sequence to appear in order. It is a matching-time
    tolerance only: neither the corpus nor the authored `source_quote` is
    rewritten.
    """
    return " ".join(re.sub(r"[^0-9A-Za-z]+", " ", normalize_ligatures(text)).split())


def load_chunks() -> list[tuple[str, str]]:
    """Return `(chunk_key, normalized_text)` for every ingested chunk."""
    db = SessionLocal()
    try:
        return [
            (chunk_key(c.document_id, c.chunk_index), collapse(c.chunk_text))
            for c in db.query(Chunk).order_by(Chunk.document_id, Chunk.chunk_index).all()
        ]
    finally:
        db.close()


def map_items(items: list[dict], chunks: list[tuple[str, str]]) -> tuple[list[dict], list[dict]]:
    """Attach `expected_chunk_ids` to each item; return `(items, unmatched)`.

    A quote spanning a chunk boundary legitimately matches two chunks —
    that overlap is itself the fragmentation signal §8.2 wants visible, so
    every match is kept rather than just the first.
    """
    unmatched = []

    for item in items:
        quote = collapse(item["source_quote"])
        matches = [key for key, text in chunks if quote in text]
        item["expected_chunk_ids"] = matches
        if not matches:
            unmatched.append(item)

    return items, unmatched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write expected_chunk_ids back to retrieval_set.jsonl (default: report only).",
    )
    args = parser.parse_args(argv)

    items = [json.loads(line) for line in RETRIEVAL_SET.read_text().splitlines() if line.strip()]
    chunks = load_chunks()

    if not chunks:
        print("No chunks in Postgres — ingest the corpus first (python -m app.ingest data.pdf).")
        return 1

    items, unmatched = map_items(items, chunks)

    multi = [i for i in items if len(i["expected_chunk_ids"]) > 1]

    print(f"corpus:      {len(chunks)} chunks")
    print(f"chunker:     chunk_size={splitter.chunk_size}, chunk_overlap={splitter.chunk_overlap}")
    print(f"items:       {len(items)}")
    print(f"mapped:      {len(items) - len(unmatched)}")
    print(f"spanning >1: {len(multi)}  {[i['id'] for i in multi]}")

    if unmatched:
        # Not a warning to skim past: an unmatched quote means the label is
        # unusable and that item silently contributes a guaranteed miss to
        # every recall@k number computed from this file.
        print(f"\nUNMATCHED ({len(unmatched)}) — these quotes are not substrings of any chunk:")
        for item in unmatched:
            print(f"  {item['id']}: {item['source_quote']!r}")

    if args.write:
        RETRIEVAL_SET.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n"
        )
        print(f"\nWrote {RETRIEVAL_SET}")
    else:
        print("\n(dry run — pass --write to update retrieval_set.jsonl)")

    return 1 if unmatched else 0


if __name__ == "__main__":
    sys.exit(main())
