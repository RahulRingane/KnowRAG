"""Corpus normalization — regression cover for three fixed extraction defects.

Not one of §8.1's three named areas, but it belongs in the offline suite for
the same reason they do: each of these bugs was silent. The corpus looked
fine, ingestion reported success, `/health` was green, and the damage only
showed up as retrieval that could never match certain words.

All three were live in the ingested corpus at the same time:

1. `U+E005/E035/E036` are the document's curly quotes and were deleted as
   "ornaments", fusing the words on either side (`"of" + "Benchmark"` ->
   the single token `ofBenchmark`).
2. Line-break hyphenation was never rejoined, so `"microproces-\\nsors"`
   indexed as two tokens and no query for "microprocessors" could match.
3. The wordlist was absent from the Docker image, so the ambiguous
   `U+E000` fell back to `ff` everywhere and the container's corpus
   contained `Classiffcation` where a host ingest produced
   `Classification`.

Pure functions over strings — no PDF, no database.
"""

from __future__ import annotations

import pytest

from app.domain.text_normalization import dehyphenate, normalize_ligatures

# Referenced by codepoint, not pasted literally: these are Private Use Area
# characters that most editors, terminals and diff tools render as nothing at
# all, so a literal here is invisible and trivially lost to a copy-paste.
FI = "\ue001"
FL = "\ue002"
FFI = "\ue003"
FFL = "\ue004"
APOS = "\ue008"
AMBIGUOUS = "\ue000"   # "ff" in body text, "fi" in headings
QUOTE_L = "\ue035"
QUOTE_L2 = "\ue036"
QUOTE_R = "\ue005"


# --- Ligatures ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (f"speci{FI}c", "specific"),
        (f"{FL}exibility", "flexibility"),
        (f"e{FFI}ciency", "efficiency"),
        (f"o{FFL}oading", "offloading"),
        (f"Let{APOS}s", "Let's"),
    ],
)
def test_ligatures_expand_to_real_words(raw, expected):
    assert normalize_ligatures(raw) == expected


def test_no_private_use_codepoints_survive():
    raw = f"speci{FI}c {QUOTE_L}Benchmark{QUOTE_R} di{AMBIGUOUS}erent Let{APOS}s"
    out = normalize_ligatures(raw)

    assert not any(0xE000 <= ord(ch) <= 0xF8FF for ch in out), out


# --- Quotation marks (defect 1) ---------------------------------------------


def test_quote_glyph_does_not_fuse_the_words_around_it():
    """The exact regression: 'in terms of<QUOTE>Benchmark' must not become one token."""
    out = normalize_ligatures(f"measured in terms of{QUOTE_L}Benchmark{QUOTE_R}. A Benchmark")

    assert "ofBenchmark" not in out
    assert "Benchmark" in out
    assert out == 'measured in terms of"Benchmark". A Benchmark'


@pytest.mark.parametrize("glyph", [QUOTE_L, QUOTE_L2, QUOTE_R])
def test_every_quote_glyph_becomes_a_token_boundary(glyph):
    out = normalize_ligatures(f"word{glyph}other")

    assert out == 'word"other'
    assert "wordother" not in out


def test_apostrophe_is_not_treated_as_a_separator():
    """U+E008 is a possessive apostrophe; separating it would break the word."""
    assert normalize_ligatures(f"the patient{APOS}s body") == "the patient's body"


# --- Line-break hyphenation (defect 2) --------------------------------------


def test_hyphenated_line_break_is_rejoined():
    assert dehyphenate("8-bit microproces-\nsors like 8085") == "8-bit microprocessors like 8085"


def test_rejoining_survives_ligature_expansion_first():
    """normalize_ligatures must dehyphenate *after* expanding ligatures."""
    out = normalize_ligatures(f"classi{FI}-\ncation of systems")

    assert out == "classification of systems"


def test_real_compound_hyphen_is_preserved_when_a_wordlist_is_available():
    from app.domain.text_normalization import _english_words

    if not _english_words():
        pytest.skip("no system wordlist installed; dehyphenate falls back to always joining")

    assert dehyphenate("it is non-\nalterable by") == "it is non-alterable by"


def test_hyphen_not_at_a_line_break_is_untouched():
    assert dehyphenate("a well-known result") == "a well-known result"
    assert dehyphenate("8-bit and 16-bit") == "8-bit and 16-bit"


# --- Ambiguous U+E000 (defect 3) --------------------------------------------


def test_ambiguous_ligature_resolves_per_occurrence_with_a_wordlist():
    from app.domain.text_normalization import _english_words

    if not _english_words():
        pytest.skip("no system wordlist installed; U+E000 falls back to 'ff'")

    # The same codepoint means "ff" in body text and "fi" in headings.
    assert normalize_ligatures(f"di{AMBIGUOUS}erent") == "different"
    assert normalize_ligatures(f"Classi{AMBIGUOUS}cation") == "Classification"
    assert normalize_ligatures(f"De{AMBIGUOUS}nition") == "Definition"


def test_normalization_is_idempotent():
    raw = f"Classi{AMBIGUOUS}cation of {QUOTE_L}Hard Real Time{QUOTE_R}systems, microproces-\nsors"
    once = normalize_ligatures(raw)

    assert normalize_ligatures(once) == once
