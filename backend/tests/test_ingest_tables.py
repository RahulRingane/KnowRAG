"""Table detection/linearization and the ligature-space repair (`app.pg`).

Both exist for one reason: the §6 NLI verifier reads a chunk as its premise,
and a premise that is structurally scrambled does not degrade its scores
gracefully — it breaks them. Measured on the real Table 1.1 chunk, the
unrelated hypothesis "The Eiffel Tower is located in Paris." scored
*contradiction 0.78*, and all six claims generated from that table were
rejected, two as CONTRADICTED. So these tests care much less about pretty
output than about two properties: a table must come out row-per-line, and
ordinary prose must never be mistaken for a table.

The detector is exercised with synthetic op lists rather than a PDF fixture.
`detect_table` consumes `(x, y, text)` tuples, so the geometry that matters
— column anchors, shared y-bands, stream order — can be stated directly and
a nested-list page (the realistic false positive on this corpus) can be
written down exactly.
"""

from __future__ import annotations

import pytest

from app.domain import tables, text_normalization
from app.infrastructure.pdf import reader


def op(x: float, y: float, text: str) -> tuple[float, float, str]:
    return (x, y, text)


def two_column_ops(rows: list[tuple[str, str]], caption: str = "Table 1.1: A vs. B"):
    """Ops for a two-column table, in the order LaTeX emits them.

    One cell at a time — every line of the left cell, then every line of the
    right cell, then the next row. Row reconstruction depends on that
    ordering, because line spacing within a cell (~34.9pt) and between rows
    (~35.7pt) are too close to separate on y alone.
    """
    ops = [op(275.1, 162.8, caption)]
    y = 195.8
    for left, right in rows:
        ops.append(op(184.5, y, left + "\n"))
        ops.append(op(681.8, y, right + "\n"))
        y += 35.0
    return ops


HEADER = ("Embedded Systems", "General Computing Systems")


# --- detection ---------------------------------------------------------------


def test_detects_a_two_column_table():
    ops = two_column_ops([HEADER, ("May or may not contain an OS", "Contains a GPOS")])
    detected = tables.detect_table(ops)

    assert detected is not None
    caption, headers, rows, _consumed = detected
    assert caption == "Table 1.1: A vs. B"
    assert headers == list(HEADER)
    assert rows == [["May or may not contain an OS", "Contains a GPOS"]]


def test_multi_line_cells_are_joined_into_one_row():
    # Cells wrap across several lines in the real document; a row split
    # mid-sentence is the malformed premise this all exists to prevent.
    ops = [
        op(275.1, 162.8, "Table 1.1: A vs. B"),
        op(184.5, 195.8, "Embedded Systems\n"),
        op(681.8, 195.8, "General Computing Systems\n"),
        op(184.5, 270.5, "A system which is a combination of\n"),
        op(184.5, 305.4, "special purpose hardware\n"),
        op(681.8, 270.5, "A system which is a combination of\n"),
        op(681.8, 305.4, "generic hardware\n"),
    ]
    _caption, _headers, rows, _consumed = tables.detect_table(ops)

    assert rows == [
        [
            "A system which is a combination of special purpose hardware",
            "A system which is a combination of generic hardware",
        ]
    ]


def test_a_nested_list_is_not_a_table():
    # The realistic false positive: this document indents list items at
    # x=168.8/188.1/220.6, so "two x anchors" alone would call ~24 of 28
    # pages a table. Items sit at *disjoint* y — they never share a row.
    ops = [op(275.1, 162.8, "Table 1.1: A vs. B")]
    y = 195.8
    for text in ("1. Based on generation", "2. Based on complexity", "3. Based on triggering"):
        ops.append(op(168.8, y, text + "\n"))
        ops.append(op(188.1, y + 17.0, "   continued\n"))
        y += 35.0

    assert tables.detect_table(ops) is None


def test_columns_must_be_far_apart():
    ops = two_column_ops([HEADER, ("left", "right")])
    # Same structure, columns 40pt apart instead of ~500 — an indented
    # block, not a table.
    narrow = [(224.5 if x == 681.8 else x, y, t) for x, y, t in ops]

    assert tables.detect_table(narrow) is None


def test_a_page_with_no_caption_is_not_a_table():
    ops = two_column_ops([HEADER, ("left", "right")])
    assert tables.detect_table(ops[1:]) is None


def test_a_header_alone_is_not_a_table():
    # One row is a heading pair, not a comparison.
    assert tables.detect_table(two_column_ops([HEADER])) is None


def test_prose_below_the_table_is_left_for_the_caller():
    # Page 22 carries paragraphs below Table 1.2. Consuming them with the
    # table would silently drop them from the corpus.
    ops = two_column_ops([HEADER, ("left", "right")])
    prose = op(220.6, 1274.8, "Instructions are pipelined for throughput.\n")
    ops.append(prose)

    _caption, _headers, _rows, consumed = tables.detect_table(ops)

    assert len(ops) - 1 not in consumed
    kept = "".join(t for i, (_x, _y, t) in enumerate(ops) if i not in consumed)
    assert "pipelined for throughput" in kept


class FakePage:
    """Minimal stand-in for a pypdf page that replays ops to the visitor."""

    def __init__(self, ops: list[tuple[float, float, str]]):
        self._ops = ops

    def extract_text(self, visitor_text=None, **kwargs):
        text = "".join(t for _x, _y, t in self._ops)
        if visitor_text is not None:
            for x, y, t in self._ops:
                visitor_text(t, None, [1, 0, 0, 1, x, y], None, 1.0)
        return text


def test_the_end_of_page_flush_op_is_dropped():
    # pypdf fires the visitor once more at end-of-page with the whole page's
    # text under an identity matrix. It is never a column anchor, so
    # detection looks fine — but it is never consumed by a table either, so
    # it carried a verbatim copy of the raw interleaved table back into the
    # corpus alongside the linearized rows.
    real = [(184.5, 195.8, "Embedded Systems\n"), (681.8, 195.8, "General Computing\n")]
    flush = (0.0, 0.0, "Embedded Systems General Computing")

    ops = reader._page_text_ops(FakePage(real + [flush]))

    assert ops == real


def test_a_table_page_leaves_no_copy_of_the_raw_table_behind():
    # The end-to-end version of the bug: whatever the detector does not
    # consume becomes prose, so a duplicate here is a duplicate in the index.
    table = two_column_ops([HEADER, ("May or may not contain an OS", "Contains a GPOS")])
    prose_op = (168.8, 1246.0, "1.2 Classification of Embedded Systems\n")
    flush = (0.0, 0.0, "".join(t for _x, _y, t in table) + "1.2 Classification")

    ops = reader._page_text_ops(FakePage(table + [prose_op, flush]))
    _caption, _headers, _rows, consumed = tables.detect_table(ops)
    leftover = "".join(t for i, (_x, _y, t) in enumerate(ops) if i not in consumed)

    assert "Contains a GPOS" not in leftover
    assert "1.2 Classification" in leftover


# --- linearization -----------------------------------------------------------


def test_each_row_becomes_one_self_contained_line():
    block = tables.linearize_table(
        "Table 1.1: A vs. B",
        list(HEADER),
        [["pre-programmed firmware", "user-alterable applications"]],
    )
    lines = block.split("\n")

    assert lines[0] == "Table 1.1: A vs. B"
    assert lines[1] == (
        "Row 1 | Embedded Systems: pre-programmed firmware "
        "| General Computing Systems: user-alterable applications"
    )


def test_every_row_repeats_the_column_headers():
    # A row may be chunked away from the caption, and a cell whose column is
    # unknown cannot support a claim about which side of the comparison it
    # describes.
    block = tables.linearize_table(
        "Table 1.1: A vs. B", list(HEADER), [["a", "b"], ["c", "d"], ["e", "f"]]
    )
    for line in block.split("\n")[1:]:
        assert "Embedded Systems:" in line
        assert "General Computing Systems:" in line


# --- chunking ----------------------------------------------------------------


def test_one_chunk_per_row():
    # Not a size budget: packing rows together was measured to put the NLI
    # model back into confident nonsense (whole 7-row table -> contradiction
    # 0.96 on a claim its own row entails at 0.997).
    block = tables.linearize_table("Table 1.1: A vs. B", list(HEADER), [["a", "b"], ["c", "d"]])
    assert len(tables.table_row_chunks(block)) == 2


def test_every_chunk_carries_the_caption_and_exactly_one_row():
    rows = [[f"left cell {i}", f"right cell {i}"] for i in range(6)]
    block = tables.linearize_table("Table 1.1: A vs. B", list(HEADER), rows)

    chunks = tables.table_row_chunks(block)

    emitted = []
    for chunk in chunks:
        lines = chunk.split("\n")
        assert lines[0] == "Table 1.1: A vs. B"  # caption repeats
        assert len(lines) == 2
        emitted.extend(lines[1:])
    # No row is torn in half, and none is lost.
    assert emitted == block.split("\n")[1:]


# --- ligature-space repair ---------------------------------------------------
#
# The PUA codepoints are unrenderable, so they're written as escapes here.
# U+E001 expands to "fi" per `_PUA_LIGATURES`.

FI = ""


@pytest.mark.skipif(not text_normalization._english_words(), reason="needs a system wordlist")
def test_space_is_restored_when_a_ligature_starts_a_word():
    # "The firmware" extracts as "The" + U+E001 + "rmware" and normalizes to
    # the single token "Thefirmware" — 33 occurrences in data.pdf.
    assert text_normalization.normalize_ligatures(f"The{FI}rmware is pre-programmed") == (
        "The firmware is pre-programmed"
    )


@pytest.mark.skipif(not text_normalization._english_words(), reason="needs a system wordlist")
def test_a_ligature_inside_a_word_is_left_alone():
    # "specific" is one word; inserting a space would be the mirror-image bug.
    assert text_normalization.normalize_ligatures(f"a speci{FI}c set") == "a specific set"


@pytest.mark.skipif(not text_normalization._english_words(), reason="needs a system wordlist")
def test_a_real_word_that_looks_fused_is_not_split():
    # "config" splits into "con" + "fig", both real words. Keying the repair
    # on the ligature position is also what keeps "pipelining", "Testability"
    # and "Keypads" out of scope entirely — they contain no PUA codepoint.
    assert text_normalization.normalize_ligatures(f"con{FI}g") == "config"


def test_a_ligature_after_punctuation_always_gets_a_space():
    # "Single," + U+E001 + "xed" -> "Single,fixed". A ligature never
    # continues a word across punctuation, so this needs no wordlist.
    assert text_normalization.normalize_ligatures(f"Single,{FI}xed length") == "Single, fixed length"


def test_repair_runs_before_expansion_and_is_idempotent_on_clean_text():
    clean = "The firmware is already correct"
    assert text_normalization.normalize_ligatures(clean) == clean
