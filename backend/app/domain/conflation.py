"""Identity/composition conflation — a pre-verification guard on entailment.

NLI treats "X *contains* Y" as evidence for "X *is* Y". Measured on this
corpus, `"embedded system is an operating system"` scored **entailment
0.987** against

    Embedded Systems: A system which is a combination of special purpose
    hardware and embedded OS for executing a specific set of applications

and **entailment 0.685** against "May or may not contain an operating
system for functioning". Both premises attach an OS to an embedded system by
*composition*; neither asserts *identity*. The same chunk set also contains
a chunk the model labels **contradiction 0.802** ("An embedded system is a
combination of hardware and software…"), which §6.2's decision order never
reaches, because a passing entailment is checked before any contradiction is.
So the corpus's own refutation lost to a conflation.

This module answers one narrow question — *does the premise attach the
hypothesis's predicate to its subject by composition rather than identity?* —
and nothing else. It is pure string inspection over `(premise, hypothesis)`,
imports nothing outside the standard library, and makes no decision: it
returns a finding, and `app.domain.verification` decides what a finding is
worth.

**Why the matching is bound rather than independent.** The obvious
implementation — "hypothesis looks like an identity claim AND premise
mentions a composition verb" — does not survive this corpus. A retrieved
chunk is 220–2343 chars of PDF prose and very nearly always contains *some*
composition verb somewhere, so two independent tests fire on almost every
pair. The measured false positive is real, not hypothetical:
`"an embedded system is a combination of hardware and software"` is correctly
SUPPORTED at 0.995, and its best premise is full of composition language.
So a finding requires the *same* subject and the *same* predicate on both
sides, inside one segment, with the two guards below.

**The two guards, and what each one is for:**

- `_is_composition_phrase` — if the hypothesis's own predicate *is* a
  composition ("…is a combination of hardware and software"), there is no
  identity/composition distinction left to confuse and detection is skipped
  outright. This is what protects the false positive above.
- `_asserts_identity` — if any segment of the premise directly asserts
  "subject is a/an predicate", the entailment is legitimate however much
  composition language surrounds it.

**Known gap, deliberately left open.** Predicate matching is surface-level
plus a small alias table (`_ALIASES`). "embedded OS" is only caught because
`os` is aliased to `operating system` by hand. A conflation phrased with a
synonym the table does not know is missed, silently. That is the honest
limit of a lexical rule, and it is the argument for an NLI-side fix (scoring
the identity direction explicitly) rather than a longer alias table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CompositionMismatch:
    """A premise that attaches `predicate` to `subject` by composition.

    Carries what it matched on rather than just a boolean, because the log
    line and the rejection reason both have to be able to say *why* — "flagged"
    with nothing behind it is not auditable, and this guard overrides a score
    the NLI model was confident about.
    """

    subject: str
    predicate: str
    verb: str
    segment: str

    def describe(self) -> str:
        return (
            f"premise attaches {self.predicate!r} to {self.subject!r} by "
            f"composition ({self.verb!r}), not identity"
        )


# Composition relations, as they survive `_normalize` (which strips a trailing
# plural "s", so "contains" arrives as "contain"). Both forms are listed anyway
# — the normalizer deliberately leaves short words like "has" alone, and a
# pattern that silently depended on that would be a trap.
#
# "with" is deliberately absent. It is the most common composition preposition
# in English and by far the loosest: "a combination of hardware and software
# *with* some attached peripherals" would make every prose chunk a match.
_COMPOSITION_VERBS = (
    r"contain(?:s)?",
    r"include(?:s)?",
    r"incorporate(?:s)?",
    r"use(?:s)?",
    r"ha(?:s|ve)",
    r"consist(?:s)?\s+of",
    r"comprise(?:s)?",
    r"combination\s+of",
    r"composed\s+of",
    r"made\s+up\s+of",
    r"built\s+around",
    r"equipped\s+with",
)
_COMPOSITION_ALT = "(?:" + "|".join(_COMPOSITION_VERBS) + ")"

# Head nouns that make a predicate a composition rather than a kind. See
# `_is_composition_phrase` — this is the guard that keeps a true composition
# *claim* out of the detector entirely.
_COMPOSITION_HEADS = (
    "combination",
    "collection",
    "composite",
    "assembly",
    "mixture",
    "blend",
    "set",
    "group",
    "aggregate",
)
_COMPOSITION_HEAD_RE = re.compile(
    r"^(?:" + "|".join(_COMPOSITION_HEADS) + r")\b|^made\s+up\b|^composed\b"
)

# "X is a/an Y". The article before Y is required, and that is a real
# restriction, not an oversight: it is what separates a kind claim ("is an
# operating system") from a property predication ("is task-specific", "are
# pre-programmed"), and only the first can be confused with composition.
#
# The leading article is matched as `(?:a|an|the)\s+` rather than `(?:a|an|the)?`
# so that a subject merely *starting* with "a" ("apples are a fruit") is not
# decapitated to "pples".
_IDENTITY_RE = re.compile(
    r"^(?:(?:a|an|the)\s+)?(?P<subject>.+?)\s+(?:is|are)\s+(?:a|an|the)\s+(?P<predicate>.+?)\.?$"
)

# Segment boundaries. `|` is here because this corpus linearizes PDF tables one
# row per chunk with pipe-separated columns, and the two offending premises are
# both table rows whose columns describe *different* subjects ("Embedded
# Systems: …| General Computing Systems: …"). Without the pipe split, a subject
# in one column binds to a predicate in the other and the finding names the
# wrong row.
_SEGMENT_SPLIT = re.compile(r"(?<=[.!?])\s+|[|\n]+|(?=•)")

# Surface variants that mean the same predicate. Corpus-specific and known
# incomplete — see the module docstring. Keyed and valued in normalized form.
_ALIASES: dict[str, tuple[str, ...]] = {
    "operating system": ("operating system", "os"),
    "os": ("operating system", "os"),
    "general purpose operating system": ("general purpose operating system", "gpos"),
    "microcontroller": ("microcontroller", "mcu"),
    "microprocessor": ("microprocessor", "mpu"),
}


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, and singularize obvious plurals.

    Plural stripping is what lets a hypothesis about "an embedded system" bind
    to a premise heading that says "Embedded Systems". It is deliberately timid
    — only words over three characters that end in a single "s" — so that "is",
    "has", "os", "gpos" and "vs" are left alone. A general stemmer would also
    turn "is" into "i", which would break every pattern in this module.
    """
    lowered = " ".join(text.lower().split())
    words = []
    for word in lowered.split(" "):
        core = word.rstrip(".,;:()")
        suffix = word[len(core) :]
        if len(core) > 3 and core.endswith("s") and not core.endswith("ss"):
            core = core[:-1]
        words.append(core + suffix)
    return " ".join(words)


def _predicate_alternation(predicate: str) -> str:
    """Regex alternation over `predicate` and any known alias, longest first."""
    variants = _ALIASES.get(predicate, (predicate,))
    ordered = sorted(set(variants), key=len, reverse=True)
    return "(?:" + "|".join(re.escape(v) for v in ordered) + ")"


def _is_composition_phrase(predicate: str) -> bool:
    """Is the hypothesis's own predicate a composition rather than a kind?

    "…is a combination of hardware and software" asserts composition directly,
    so there is nothing for a composition premise to be conflated *with*. This
    is the guard that keeps the measured false positive out.
    """
    return bool(_COMPOSITION_HEAD_RE.search(predicate))


def _asserts_identity(segment: str, subject: str, predicate_alt: str) -> bool:
    """Does this segment say "subject is a/an predicate" outright?

    The window after the copula is short on purpose. In
    "…a system which is a combination of special purpose hardware and embedded
    os", the copula and the predicate are both present but 50 characters apart,
    with the composition sitting between them — which is the conflation, not an
    identity assertion. Requiring the predicate to be the *head* of the
    complement is what tells those two apart.
    """
    pattern = (
        re.escape(subject)
        + r"\b.{0,40}?\b(?:is|are)\s+(?:a|an|the)\s+.{0,25}?"
        + predicate_alt
        + r"\b"
    )
    return re.search(pattern, segment) is not None


def detect_composition_mismatch(premise: str, hypothesis: str) -> CompositionMismatch | None:
    """Return a finding if `premise` supports `hypothesis` only by composition.

    Returns `None` — meaning "no opinion" — for anything that is not a bound
    identity/composition pair, which is the overwhelming majority of calls.
    Pure and side-effect free; the caller decides what a finding costs.
    """
    identity = _IDENTITY_RE.match(_normalize(hypothesis))
    if identity is None:
        return None

    subject = identity.group("subject").strip()
    predicate = identity.group("predicate").strip()
    if not subject or not predicate or _is_composition_phrase(predicate):
        return None

    predicate_alt = _predicate_alternation(predicate)
    segments = [s.strip() for s in _SEGMENT_SPLIT.split(_normalize(premise)) if s and s.strip()]

    # Checked across every segment before any composition match is considered:
    # one segment stating the identity outright legitimizes the entailment no
    # matter how much composition language the rest of the chunk carries.
    if any(_asserts_identity(seg, subject, predicate_alt) for seg in segments):
        return None

    composition = re.compile(
        re.escape(subject)
        + r"\b.{0,160}?\b(?P<verb>"
        + _COMPOSITION_ALT
        + r")\b.{0,120}?"
        + predicate_alt
        + r"\b"
    )
    for segment in segments:
        match = composition.search(segment)
        if match is not None:
            return CompositionMismatch(
                subject=subject,
                predicate=predicate,
                verb=match.group("verb"),
                segment=segment,
            )
    return None
