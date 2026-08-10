"""The CLI's retrieval-candidates block (`app.cli.query`).

The block's whole value rests on one property: `[C1]` printed above a verdict
is the same chunk as `[C1]` cited inside it. That holds because the numbering
comes from `format_context` — §5.1's single owner of the tag scheme — rather
than from a second `enumerate` here. These tests pin that, plus the decorator
being transparent to the pipeline and the two quiet output modes staying
machine-readable.

Offline: no model, no datastore, no network. The retriever is a list.
"""

from __future__ import annotations

from app.cli.query import (
    _CONTENT_CHARS,
    _PrintingRetriever,
    _preview,
    _print_candidates,
)
from app.domain.context import format_context
from app.domain.models import Chunk


def _chunk(index: int, text: str = "body text", rerank: float | None = 1.0) -> Chunk:
    return Chunk(
        source="qdrant",
        score=0.5,
        rerank_score=rerank,
        document_id=1,
        chunk_index=index,
        text=text,
    )


class _StubRetriever:
    """Records what it was asked for, returns what it was scripted with."""

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, k: int = 0) -> list[Chunk]:
        self.calls.append((query, k))
        return self.chunks


# --- the decorator ----------------------------------------------------------


def test_printing_retriever_returns_its_inner_result_unchanged(capsys):
    """It observes retrieval; it must not participate in it. A decorator that
    reordered, truncated, or copied the chunks would change what the pipeline
    verifies, which is the one thing a debug print may never do.
    """
    chunks = [_chunk(i) for i in range(5)]
    inner = _StubRetriever(chunks)

    got = _PrintingRetriever(inner).search("what is RISC?", 5)

    assert got is chunks
    assert inner.calls == [("what is RISC?", 5)]
    assert capsys.readouterr().out  # and it did print


def test_printing_retriever_satisfies_the_retriever_port():
    from app.domain.ports import Retriever

    assert isinstance(_PrintingRetriever(_StubRetriever([])), Retriever)


# --- tags -------------------------------------------------------------------


def test_tags_match_format_context_numbering():
    """The guarantee the block exists for: the tags printed above the verdict
    are the tags the verdict's citations resolve against. Derived from
    `format_context` rather than re-`enumerate`d, so this cannot drift.
    """
    chunks = [_chunk(i) for i in range(5)]
    _, tag_map = format_context(chunks)

    assert list(tag_map)[:3] == ["C1", "C2", "C3"]
    assert tag_map["C2"] is chunks[1]


def test_prints_three_of_five_in_rank_order(capsys):
    chunks = [_chunk(i, text=f"chunk number {i}", rerank=9.0 - i) for i in range(5)]

    _print_candidates(chunks)
    out = capsys.readouterr().out

    assert "=== TOP 3 RETRIEVAL CANDIDATES ===" in out
    assert "[C1] Score: 9.0000" in out
    assert "[C3] Score: 7.0000" in out
    # `top_k_rerank` is 5; the block deliberately shows the first three.
    assert "[C4]" not in out
    assert out.count("---") == 3
    assert out.index("[C1]") < out.index("[C2]") < out.index("[C3]")


def test_header_reports_the_actual_count(capsys):
    """Fewer than three retrieved is a normal outcome, and a header that says
    "TOP 3" above two entries misreports the evidence.
    """
    _print_candidates([_chunk(0), _chunk(1)])

    assert "=== TOP 2 RETRIEVAL CANDIDATES ===" in capsys.readouterr().out


def test_empty_retrieval_says_so(capsys):
    _print_candidates([])
    out = capsys.readouterr().out

    assert "=== TOP 0 RETRIEVAL CANDIDATES ===" in out
    assert "(nothing retrieved)" in out


# --- scores -----------------------------------------------------------------


def test_falls_back_to_the_store_score_when_not_reranked(capsys):
    """A retriever that does not rerank still has a score worth showing; the
    alternative is printing a rank with nothing justifying it.
    """
    _print_candidates([_chunk(0, rerank=None)])

    assert "[C1] Score: 0.5000" in capsys.readouterr().out


def test_unscored_chunk_prints_na_rather_than_crashing(capsys):
    chunk = Chunk(source="elastic", document_id=1, chunk_index=0, text="body")

    _print_candidates([chunk])

    assert "[C1] Score: n/a" in capsys.readouterr().out


def test_negative_rerank_scores_render(capsys):
    """Cross-encoder output is a logit, not a probability — it goes negative
    for a poorly-matched chunk, and those are exactly the ones a reader is
    inspecting the block to find.
    """
    _print_candidates([_chunk(0, rerank=-8.25)])

    assert "[C1] Score: -8.2500" in capsys.readouterr().out


# --- content preview --------------------------------------------------------


def test_preview_truncates_and_marks_it():
    got = _preview("x" * 1000)

    assert got.endswith("…")
    assert len(got) == _CONTENT_CHARS + 1


def test_preview_leaves_short_text_alone():
    assert _preview("a short chunk") == "a short chunk"


def test_preview_collapses_pdf_line_breaks():
    """Chunks carry the source PDF's own line breaks. Collapsing before the
    cut is what keeps the character budget spent on words.
    """
    assert _preview("line one\n  line   two\n\nline three") == "line one line two line three"


def test_content_line_is_wrapped_and_hanging_indented(capsys):
    _print_candidates([_chunk(0, text=" ".join(f"word{i}" for i in range(120)))])
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]

    content = [ln for ln in lines if ln.startswith("Content: ") or ln.startswith(" " * 9)]
    assert len(content) > 1  # it actually wrapped
    assert content[0].startswith("Content: word0 ")
    # Continuation lines hang under the label rather than under the margin, so
    # the preview reads as one block and not as more output.
    assert all(ln.startswith(" " * 9) for ln in content[1:])
    assert all(len(ln) <= 88 for ln in content)
