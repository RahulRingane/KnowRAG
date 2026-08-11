"""Repairing text a PDF extractor mangled — pure string rules, no I/O.

Extracted from the old `app/pg.py`, where this sat in the same module as the
SQLAlchemy engine. It belongs in the domain layer because none of it is
about storage: it encodes what this corpus's text *means* when the extractor
hands back a Private Use Area codepoint, and every rule below was derived by
measurement against `data.pdf`, not from a specification.

The one filesystem read here (`_english_words`) is a static language
resource, not an application dependency — no service, no network, no
configuration. Nothing in the module *requires* it: every caller degrades to
its measured majority-case behaviour when no wordlist is installed.

Left uncorrected these defects silently degrade the entire pipeline: BM25
never matches a query for "specific" against an indexed "specic",
the embedding tokenizer sees an unknown codepoint, and the NLI verifier in
§6 reads corrupted premises.
"""

from __future__ import annotations

import re
from functools import lru_cache

# --- Ligature normalization -------------------------------------------------
# LaTeX-produced PDFs (data.pdf is one) frequently embed subsetted fonts that
# map ligatures and smart punctuation into the Unicode Private Use Area rather
# than to real characters. Extracted text then contains "specic" instead
# of "specific" — reproducible under both pdftotext and pypdf, so it is a
# property of the file, not the extractor.
#
# Measured on data.pdf: 166 PUA codepoints, and the words "specific",
# "different", and "efficiency" have *zero* plain-text occurrences before
# normalization.
#
# The mapping below was derived empirically, by expanding each candidate
# ligature and keeping the one that produced a real dictionary word, not by
# assuming the standard Unicode ligature order.
#
# Written as `\ueNNN` escapes rather than as the literal codepoints. They are
# unrenderable by definition — they display as nothing, or as a replacement
# box, in most editors and in every diff view — so a literal here is a
# constant nobody can proofread and that any copy/paste silently destroys.
# The escape form is the only one that survives being moved between files.
_PUA_LIGATURES = {
    "\ue001": "fi",   # 100/100 unambiguous
    "\ue002": "fl",   # 6/6
    "\ue003": "ffi",  # 2/2
    "\ue004": "ffl",  # 1/1
    "\ue008": "'",    # apostrophe: today's, Let's, SoC's
}

# U+E000 is genuinely ambiguous — the document uses more than one subsetted
# font, and the same codepoint means "ff" in body text ("dierent") but
# "fi" in headings ("Classication"). Frequency on this corpus is ff:19 /
# fi:7, so "ff" is the fallback, but each occurrence is resolved individually
# against a wordlist when one is available.
_PUA_AMBIGUOUS = {"\ue000": ("ff", "fi")}

# Quotation marks — NOT droppable ornaments.
#
# These three were originally classified as "dashes/bullets that carry no
# letters" and replaced with the empty string. They are in fact the
# document's curly quotes, and the extractor emits no space around them,
# so deleting one fuses the words on either side:
#
#   raw    'in terms of\ue035Benchmark\ue005. A Benchmark'
#   before 'in terms ofBenchmark. A Benchmark'    <- one token, "ofbenchmark"
#   after  'in terms of"Benchmark". A Benchmark'
#
# That is the same failure the ligature mapping exists to prevent: BM25
# cannot match a query for "Benchmark" against an indexed "ofBenchmark",
# and the §6 NLI verifier reads a premise containing a word that does not
# exist. ASCII quotes rather than curly ones, because every downstream
# consumer (ES's standard analyzer, the embedding tokenizer, substring
# matching in eval/) treats them as a clean token boundary.
#
# U+E008 stays mapped to "'" in _PUA_LIGATURES above: it is the right
# single quote, and is overwhelmingly used as a possessive apostrophe
# ("Let's", "patient's", "SoC's"), where inserting a separator would be
# wrong.
_PUA_QUOTES = {
    "\ue035": '"',  # left double quote
    "\ue036": '"',  # left double quote, second subsetted font
    "\ue005": '"',  # right double quote
}

# Words broken across a line by LaTeX hyphenation ("microproces-\nsors").
# Left alone, the indexed tokens are "microproces" and "sors", so a query
# for "microprocessors" matches neither. 78 occurrences in data.pdf.
_HYPHEN_LINEBREAK = re.compile(r"(\w+)-\n(\w+)")


# Words that legitimately look like two words fused at a ligature.
#
# `repair_ligature_spaces` below decides a space was dropped when the fused
# token isn't a word but both halves are. That test is right 24 times out of
# 25 on this corpus and wrong exactly once: "config" is a real token that
# splits into "con" + "fig". Note the test never even sees "pipelining",
# "Testability" or "Keypads" — those contain no PUA codepoint, so keying the
# repair on the ligature position (rather than on a bare wordlist split)
# excludes that entire class of false positive by construction.
_LIGATURE_FUSION_EXCEPTIONS = frozenset({"config"})

# A ligature can continue a word ("speci" + fi + "c") but never continues one
# across punctuation, so a PUA codepoint sitting directly against one of these
# is always a dropped space ("Single," + fi + "xed" -> "Single,fixed").
_FUSION_PUNCTUATION = ",.;:)"


@lru_cache(maxsize=1)
def _english_words() -> frozenset[str]:
    """Lowercase system wordlist, or empty if none is installed.

    Used to disambiguate `_PUA_AMBIGUOUS` and to decide whether a
    line-break hyphen is real (see `dehyphenate`). Ingestion never *depends*
    on this file existing — both callers fall back to their majority-case
    expansion — but the results genuinely differ, so the Dockerfile installs
    `wamerican` to keep container ingestion identical to host ingestion.

    Without it, `U+E000` resolves to "ff" everywhere and the corpus gains
    "Classiffcation" and "Deffnition" in place of "Classification" and
    "Definition" — words no query will ever match. That divergence went
    unnoticed precisely because it only appears in the container.
    """
    for path in ("/usr/share/dict/american-english", "/usr/share/dict/words"):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                return frozenset(w.strip().lower() for w in fh)
        except OSError:
            continue
    return frozenset()


def dehyphenate(text: str) -> str:
    """Rejoin words LaTeX split across a line break ("microproces-\\nsors").

    Must run *after* ligature expansion, so the dictionary test below sees
    real words rather than strings with Private Use Area codepoints in them.

    Some of these hyphens are real ("non-\\nalterable" is the compound
    "non-alterable", not "nonalterable"), so the joined form is checked
    against the wordlist first and the hyphen is only kept when joining
    would produce a non-word *and* the prefix stands alone as a word. With
    no wordlist installed the hyphen is always dropped, which is the
    dominant case by a wide margin on this corpus.
    """
    words = _english_words()

    def _join(match: re.Match[str]) -> str:
        head, tail = match.group(1), match.group(2)
        if not words:
            return f"{head}{tail}"
        if f"{head}{tail}".lower() in words:
            return f"{head}{tail}"
        if head.lower() in words:
            return f"{head}-{tail}"
        return f"{head}{tail}"

    return _HYPHEN_LINEBREAK.sub(_join, text)


# Every PUA codepoint that expands to letters, and what it expands to. The
# ambiguous one contributes its majority expansion: this table is only used to
# decide *whether* a space is missing, never to write the final text, so the
# fallback is good enough here and the real disambiguation still happens in
# `normalize_ligatures`.
_PUA_LETTERS = {
    pua: replacement
    for pua, replacement in {
        **_PUA_LIGATURES,
        **{k: v[0] for k, v in _PUA_AMBIGUOUS.items()},
    }.items()
    # Selected by what it expands *to*, not by key: the apostrophe entry in
    # `_PUA_LIGATURES` is punctuation, and a missing space is not a question
    # that arises for it. (The keys are unrenderable PUA codepoints, so
    # excluding it by literal would be unreadable and easy to get wrong.)
    if replacement.isalpha()
}
_PUA_LETTER_CLASS = "".join(_PUA_LETTERS)
# Apostrophes count as part of a token so "world's" + fi + "rst" resolves
# against the wordlist as "world's" rather than the non-word "s".
_TOKEN_CHARS = "'"


def _expand_pua(text: str) -> str:
    for pua, replacement in _PUA_LETTERS.items():
        text = text.replace(pua, replacement)
    return text


def repair_ligature_spaces(text: str) -> str:
    """Reinsert spaces dropped where a ligature starts a word.

    Same corruption class as `normalize_ligatures` itself, one step earlier:
    when a subsetted-font ligature begins a word, pypdf emits it as its own
    text op and the preceding space is lost. "The firmware" extracts as
    "The" + U+E001 + "rmware" and normalizes to the single token
    "Thefirmware" — a word no query matches and, more importantly here, a
    corrupted premise for the §6 NLI verifier. Measured on data.pdf: 33
    occurrences across 24 distinct tokens ("thefirmware", "provideflexibility",
    "infigure", ...).

    Must run *before* ligature expansion: the PUA codepoint's position is the
    entire signal. Once it has been replaced by "fi" the boundary is gone and
    all that's left is a wordlist guess that cannot tell "thefirmware" from
    "pipelining".

    With no wordlist installed only the punctuation rule applies, which needs
    no dictionary. That is the same graceful degradation `dehyphenate` uses.
    """
    if not _PUA_LETTER_CLASS:
        return text

    words = _english_words()
    cuts: list[int] = []

    for match in re.finditer(f"[{_PUA_LETTER_CLASS}]", text):
        start = match.start()
        if start == 0 or text[start - 1].isspace():
            continue

        previous = text[start - 1]
        if previous in _FUSION_PUNCTUATION:
            cuts.append(start)
            continue

        if not previous.isalpha() or not words:
            continue

        # Widen to the whole token the ligature sits inside.
        left = start
        while left > 0 and (
            text[left - 1].isalpha()
            or text[left - 1] in _PUA_LETTER_CLASS
            or text[left - 1] in _TOKEN_CHARS
        ):
            left -= 1
        right = match.end()
        while right < len(text) and (
            text[right].isalpha()
            or text[right] in _PUA_LETTER_CLASS
            or text[right] in _TOKEN_CHARS
        ):
            right += 1

        head = _expand_pua(text[left:start])
        tail = _expand_pua(text[start:right])
        fused = f"{head}{tail}".lower()

        if fused in words or fused in _LIGATURE_FUSION_EXCEPTIONS:
            continue
        if head.lower() in words and tail.lower() in words:
            cuts.append(start)

    if not cuts:
        return text

    out: list[str] = []
    previous_cut = 0
    for cut in cuts:
        out.append(text[previous_cut:cut])
        out.append(" ")
        previous_cut = cut
    out.append(text[previous_cut:])
    return "".join(out)


def normalize_ligatures(text: str) -> str:
    """Restore PUA-mapped ligatures and punctuation to real characters."""
    # First, while the PUA codepoints still mark where words actually began.
    text = repair_ligature_spaces(text)

    for pua, replacement in _PUA_LIGATURES.items():
        text = text.replace(pua, replacement)
    for pua, replacement in _PUA_QUOTES.items():
        text = text.replace(pua, replacement)

    words = _english_words()
    for pua, (fallback, alternate) in _PUA_AMBIGUOUS.items():
        if pua not in text:
            continue
        if not words:
            text = text.replace(pua, fallback)
            continue

        def _resolve(match: re.Match[str], _p=pua, _f=fallback, _a=alternate) -> str:
            token = match.group()
            for candidate in (_f, _a):
                if token.replace(_p, candidate).lower() in words:
                    return token.replace(_p, candidate)
            return token.replace(_p, _f)

        text = re.sub(rf"[\w{pua}]*{pua}[\w{pua}]*", _resolve, text)

    # Last: the hyphen rule tests candidate joins against the wordlist, so
    # every ligature must already be a real character by this point.
    return dehyphenate(text)
