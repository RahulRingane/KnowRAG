"""Turning a PDF on disk into `PageContent` — extraction only, no policy.

Split out of the old `app/pg.py`'s `load_and_chunk_pdf`, which opened the
file, pulled its text ops, detected tables, split prose and decided chunk
boundaries in one function. Everything that is a *decision* moved to
`app.domain.chunking` and `app.domain.tables`; what is left here is the part
that genuinely needs pypdf and llama-index.

Two readers are used against the same file on purpose, because they give
different things and both are needed:

- `pypdf.PdfReader` exposes the text-showing operations *with their x/y
  coordinates*, which is the only signal that distinguishes a two-column
  table from a run of short paragraphs.
- llama-index's `PDFReader` gives the page text in the form the chunker has
  always consumed.

They are zipped by page index, and the llama-index page list is the one that
governs — matching the original loop, which iterated its documents and
looked up geometry by page number.
"""

from __future__ import annotations

import hashlib

from llama_index.readers.file import PDFReader
from pypdf import PdfReader

from app.domain.chunking import PageContent, chunk_document
from app.domain.tables import TextOp


def _page_text_ops(page) -> list[TextOp]:
    """Every text-showing op on `page` as `(x, y, text)`, in stream order.

    `extract_text()` throws the coordinates away, and
    `extraction_mode="layout"` returns an empty string for this file, so the
    visitor callback is the only route to the geometry that makes a table
    recognizable as one.
    """
    ops: list[TextOp] = []

    def visitor(text, cm, tm, font_dict, font_size):  # noqa: ANN001 - pypdf's signature
        if not text.strip():
            return
        x, y = round(tm[4], 1), round(tm[5], 1)
        # pypdf fires the visitor once more at end-of-page, under an identity
        # text matrix, with the *entire* page's text as one op. Verified on
        # every page of data.pdf: exactly one op at (0, 0), always last,
        # always equal to `extract_text()`.
        #
        # Dropping it is not cosmetic. It is never a column anchor (it occurs
        # once, and `_MIN_RUNS_PER_COLUMN` needs two), so table detection is
        # unaffected and looked correct — but it is also never *consumed* by
        # a table, so it flowed straight into the leftover prose and put a
        # verbatim copy of the raw interleaved table back into the corpus.
        # The linearized rows and the broken original then both existed, and
        # retrieval was free to pick the broken one.
        if x == 0.0 and y == 0.0:
            return
        ops.append((x, y, text))

    page.extract_text(visitor_text=visitor)
    return ops


def read_pages(path: str) -> list[PageContent]:
    """Load `path` as one `PageContent` per page, text plus geometry."""
    geometry = [_page_text_ops(page) for page in PdfReader(path).pages]
    docs = PDFReader().load_data(file=path)

    return [
        PageContent(
            text=getattr(doc, "text", None),
            ops=geometry[number] if number < len(geometry) else [],
        )
        for number, doc in enumerate(docs)
    ]


class PdfDocumentLoader:
    """`app.domain.ports.DocumentLoader` for PDF files."""

    def chunk(self, path: str) -> list[str]:
        return chunk_document(read_pages(path))

    def content_hash(self, path: str) -> str:
        """SHA-256 of the raw file bytes — the identity ingestion short-circuits on."""
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()
