"""How a document becomes chunks — the policy, not the file handling.

Extracted from the old `app/pg.py`'s `load_and_chunk_pdf`, which read the
PDF and decided how to cut it in the same function. Opening the file is
`app.infrastructure.pdf`'s job and lands here as a list of `PageContent`;
choosing the chunk boundaries is a domain decision, because chunk size is
not a storage detail — it determines whether the §6 verifier has a premise
it can reason about at all.

Splitting the two also makes the policy testable without a PDF: hand
`chunk_document` a couple of synthetic pages and the whole
table-vs-prose decision is exercised with no file on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from llama_index.core.node_parser import SentenceSplitter

from app.domain.tables import TextOp, detect_table, linearize_table, table_row_chunks
from app.domain.text_normalization import normalize_ligatures

# chunk_size=60/chunk_overlap=15 (the original values) barely holds a single
# sentence, which starves both retrieval and the §6 NLI verifier — a chunk
# must contain enough surrounding context to actually entail or contradict
# a claim, not just be "topically related" to it. 512/64 suits
# bge-base-en-v1.5's window and gives the verifier real premises to work
# with. (Decision recorded in PLAN.md Blocker #1.)
splitter = SentenceSplitter(
    chunk_size=512,
    chunk_overlap=64,
)


@dataclass(frozen=True)
class PageContent:
    """One page, in both of the forms chunking needs.

    `text` is the extractor's plain reading of the page and `ops` are its
    positioned text-showing operations. Both are carried because they answer
    different questions: `text` is what gets chunked as prose, while `ops`
    retain the x/y geometry without which a table is indistinguishable from
    a run of short paragraphs (see `app.domain.tables`).

    `ops` is allowed to be empty — a reader that cannot supply geometry
    simply gets no table detection, and every page is treated as prose.
    """

    text: str | None
    ops: list[TextOp] = field(default_factory=list)


def split_prose(text: str) -> list[str]:
    """Cut ordinary running text at sentence boundaries, under the size cap."""
    return splitter.split_text(text)


def chunk_document(pages: list[PageContent]) -> list[str]:
    """Chunk a whole document, linearizing any comparison tables it contains.

    Pages with no detected table go through the plain
    normalize-then-sentence-split path, so this treatment is confined to the
    pages that actually need it (2 of 28 in data.pdf) rather than quietly
    re-cutting the whole corpus. On a table page the page is rebuilt from its
    text ops instead: the table becomes row-per-line chunks, and everything
    else on the page stays prose.
    """
    chunks: list[str] = []

    for page in pages:
        detected = detect_table(page.ops) if page.ops else None

        if detected is not None:
            caption, headers, rows, consumed = detected
            chunks.extend(table_row_chunks(linearize_table(caption, headers, rows)))
            # The raw interleaved cells are dropped rather than kept alongside
            # the linearized form: leaving them in would keep feeding the
            # verifier the exact premise that breaks it, and retrieval would
            # still be free to pick the broken copy.
            prose = "".join(
                text for i, (_x, _y, text) in enumerate(page.ops) if i not in consumed
            )
            chunks.extend(split_prose(normalize_ligatures(prose)))
            continue

        if not page.text:
            continue
        chunks.extend(split_prose(normalize_ligatures(page.text)))

    return chunks
