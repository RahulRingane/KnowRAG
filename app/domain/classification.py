"""Deciding what kind of input arrived — a question, or an assertion.

Until this module existed there was exactly one path: every input ran
retrieve -> generate -> verify -> assemble, regardless of whether the caller
asked something or asserted something. That is the right pipeline for a
question and the wrong one for a statement. Given "RISC has instruction
pipelining", the old path spent a generation call asking an LLM to *answer*
a sentence that was not a question, then fact-checked whatever it produced —
never the sentence the caller actually wrote.

The routing this feeds is in `app.services.query_service`:

    question -> retrieve, generate, verify, assemble   (unchanged)
    fact     -> retrieve, verify the caller's own sentence, assemble

**Why this lives in the domain layer.** Classification is a decision about
vocabulary — what counts as a question — and it is pure: a string in, a
`ClassifiedInput` out, no model, no I/O, no config. That is the same reason
`ClaimVerifier` is here while the NLI model is in `infrastructure`. The
`InputClassifier` port in `app.domain.ports` is what keeps that swappable:
an LLM- or model-backed classifier belongs in `app.infrastructure`, satisfies
the same protocol, and drops into `QueryService.__init__` with no caller
changing. `classify_input` below satisfies that protocol as a plain function.

**Why a heuristic and not a model, for now.** The distinction this draws is
carried almost entirely by two surface features — a trailing `?` and a
leading interrogative — and paying a network round trip plus a generation
call to recover them would put an LLM call *in front of* the LLM call it is
supposed to save. When the heuristic's limits start to bite (see
`INTERROGATIVE_OPENERS`), replace the implementation, not the interface.
"""

from __future__ import annotations

import re

from app.domain.models import ClassifiedInput, InputType

# The openers that make a sentence a question even without a `?`.
#
# Deliberately a small, closed set rather than a general grammar. Each entry
# is a word that, in first position, is overwhelmingly interrogative in this
# corpus's register ("what is RISC", "does the MMU translate addresses"). The
# set is *not* exhaustive English — "are", "do", "which", "should" and friends
# are absent — and that is a bounded, known gap rather than an oversight:
# every one of those forms almost always carries a `?` in practice, which the
# other rule catches. Widening the set is the cheap fix if that stops holding;
# the expensive failure would be the reverse, a word that also opens ordinary
# declaratives ("has" is already the riskiest member here) silently routing
# assertions into the answer path where they are never checked.
INTERROGATIVE_OPENERS = frozenset(
    {
        "what",
        "why",
        "how",
        "when",
        "where",
        "who",
        "is",
        "does",
        "can",
        "has",
    }
)

# Leading/trailing punctuation and quoting that must not hide the first word.
# `"What is RISC"` and `- what is RISC` both open with an interrogative.
_LEADING_NOISE = re.compile(r'^[\s\'"“”‘’(\[\-–—*>•.]+')


def first_word(text: str) -> str:
    """The first word of `text`, lowercased, stripped of framing punctuation.

    Returns `""` for input with no word characters at all, which the caller
    treats as "no interrogative opener" rather than as an error — an empty or
    punctuation-only string is a degenerate input, not a crash.
    """
    cleaned = _LEADING_NOISE.sub("", text.strip())
    match = re.match(r"[\w']+", cleaned)
    return match.group(0).lower() if match else ""


def classify_input(text: str) -> ClassifiedInput:
    """Classify `text` as a question or an assertion.

    Two rules, in this order:

    1. It ends with `?` -> question. Explicit punctuation beats everything;
       a caller who typed a question mark meant to ask something.
    2. Its first word is in `INTERROGATIVE_OPENERS` -> question.

    Anything else is a `fact` — an assertion to be checked against the index
    rather than answered. The default falls that way on purpose: routing a
    question into the fact path checks a sentence that was never asserted and
    the caller sees an odd but *visible* verdict, while routing an assertion
    into the answer path fact-checks the model's output instead of the
    caller's claim, which looks entirely normal and answers the wrong
    question. Between two errors, prefer the one that shows.

    Trailing whitespace is ignored, so a piped `"what is RISC?\\n"` classifies
    the same as the typed form.
    """
    stripped = text.strip()

    if stripped.endswith("?"):
        input_type: InputType = "question"
    elif first_word(stripped) in INTERROGATIVE_OPENERS:
        input_type = "question"
    else:
        input_type = "fact"

    return ClassifiedInput(input_type=input_type, text=text)


__all__ = ["INTERROGATIVE_OPENERS", "classify_input", "first_word"]
