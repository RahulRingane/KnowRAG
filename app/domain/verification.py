"""Claim verification — the core differentiator of KnowRAG (§6.1, §6.2).

Extracted from the old `app/verify.py`, which held both these rules and the
~400MB transformer that scores them. The rules are the valuable part and
they are pure decision logic; the model is infrastructure. Separating them
means:

- this module imports no torch, no `sentence_transformers`, no config
  beyond a threshold, and
- the entire §6.2 decision table is exercised offline against a stub
  scorer, which is what it always was in the tests — that was previously
  achieved by monkeypatching a module global, and is now a constructor
  argument.

Per KNowRAG_SPEC.md §6:

- §6.1: verification uses a real NLI/entailment model (Option B), not the
  retrieval cross-encoder. A relevance score answers "is this chunk related
  to the claim," which is the wrong question — we need "does this chunk
  support this claim," which is what NLI models are trained to answer. The
  `EntailmentScorer` port is how that requirement is stated in code: a
  reranker does not satisfy it, because a reranker has no notion of a label.
- §6.2: `ClaimVerifier.verify_claim()` implements the five-path decision
  exactly as specified.
"""

from __future__ import annotations

import logging
import re
from typing import NamedTuple

from app.domain.conflation import detect_composition_mismatch
from app.domain.models import Chunk, Claim, ClaimVerdict, ScoringEvent, chunk_key
from app.domain.ports import EntailmentScorer, NLILabel, ScoringObserver

logger = logging.getLogger(__name__)


# §5.2 asks the generator to end each factual sentence with the tags of the
# chunks it draws from, so a claim arrives looking like:
#
#     "Embedded Systems may or may not contain an operating system [C1]."
#
# That trailing tag is metadata, not a proposition, and feeding it to the NLI
# model as part of the hypothesis is a measurable bug (PLAN.md OQ-2): the
# tagged string is not a well-formed sentence, and the model answers "neutral"
# to it. Measured across the 21 false-rejection items, stripping the tags
# flips 6 of them outright — ret-002 neutral 0.995 -> entailment 0.998,
# ret-003 0.539 -> 0.966, ret-017 0.658 -> 0.980.
#
# Scoring-side only. `ClaimVerdict.text` keeps the tags, so §6.4's serialized
# contract and the human-readable inline citations callers already get are
# unchanged — this touches what the model reads, nothing else.
_CITATION_TAG = re.compile(r"\s*\[C\d+\]")


def strip_citation_tags(text: str) -> str:
    """Remove `[Cn]` tags from a claim so only the proposition is scored.

    Measured on this corpus, every tag in 84 generated claims was
    sentence-final, which this handles exactly. §5.2's prompt does also
    invite a tag as a grammatical element ("Combining [C1] and [C3], ..."),
    which no observed claim used; that form degrades to "Combining and, ..."
    rather than breaking, and scores neutral either way, so it is no worse
    than the behavior this replaces.
    """
    stripped = _CITATION_TAG.sub("", text)
    stripped = re.sub(r"\s+([.,;:!?])", r"\1", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def build_chunk_tag_map(chunks: list[Chunk]) -> dict[str, Chunk]:
    """Assign citation tags `C1..Cn` to `chunks` in list order.

    Matches §5.1's `format_context()` contract ("[C1] ... [C2] ..." assigned
    in the order chunks are passed in), so a `Claim.citations` list produced
    against a given `format_context(chunks)` call lines up with the same
    `chunks` list passed here.
    """
    return {f"C{i + 1}": chunk for i, chunk in enumerate(chunks)}


# OQ-3R. Direction D took false rejection 0.385 -> 0.051, but the two survivors
# both cite the correct chunk with the fact present verbatim and are the two
# longest cited chunks in the corpus (2251 and 2328 chars). The new checkpoint
# pushed the length at which confident-neutral sets in far enough to recover 13
# of 15 items, not past the tail.
#
# So: keep Direction D as the verifier, and fall back to Direction C's premise
# narrowing ONLY on a long premise the model already declined. Gated at 2200
# chars, which is 4 of 42 chunks — the corpus max is 2343, so this is
# deliberately the tail and not a general strategy.
#
# Why this is not the conditional windowing that failed as a general repair
# (0.6% compute saving, cost a recovery): there the gate sat in front of a
# verifier that rejected 15 items, so windowing carried the whole fix and
# gating it could only lose recoveries. Here it sits behind a verifier that
# already recovered 13, and has to earn only the last two.
LONG_PREMISE_CHARS = 2200

# Sentence-ish units: real sentence enders, newlines, and the bullet character
# this corpus uses for definition lists. Note the known dot-leader defect (a
# table of contents splits into ~5-char fragments) is unreachable from here:
# those chunks are ~1000 chars and never pass the gate.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|(?=•)")
_WINDOW_CHARS = 700

# Start a window every Nth unit rather than at every unit. Window *count*
# tracks unit count and is independent of window size (measured under Direction
# C: 1258 windows across the corpus at caps of 400, 700 and 900 alike), so
# stride is the only lever on cost here. Measured on the two OQ-3R items:
#
#   stride 1 -> 44/45 windows, entailment 0.997 / 0.979
#   stride 3 -> 15/15 windows, entailment 0.997 / 0.979   <- chosen
#   stride 4 -> 11/12 windows, entailment 0.992 / 0.978
#   stride 6 ->   8/8 windows, entailment 0.709 / 0.979   <- degrading
#
# 3 keeps a margin before the cliff between 4 and 6 while cutting the work by
# two thirds. Windows still overlap heavily (700 chars stepping 3 sentences),
# which is what keeps the relevant sentence inside some window.
_WINDOW_STRIDE = 3


def _windows(text: str) -> list[str]:
    """Sliding windows of consecutive sentence units, each under the char cap."""
    units = [u.strip() for u in _SENTENCE_SPLIT.split(text) if u and u.strip()]
    windows: list[str] = []
    for start in range(0, len(units), _WINDOW_STRIDE):
        current: list[str] = []
        for unit in units[start:]:
            candidate = current + [unit]
            if current and len(" ".join(candidate)) > _WINDOW_CHARS:
                break
            current = candidate
        if current:
            window = " ".join(current)
            if window not in windows:
                windows.append(window)
    return windows


class _Scored(NamedTuple):
    """One scored citation: what was reported, and what it resolved to.

    `event` is the record handed to the log and the observer; `chunk` is kept
    beside it because §6.4's `chunk_ids` needs the resolved chunk and `None`
    is how a citation that resolved to nothing is represented.
    """

    event: ScoringEvent
    chunk: Chunk | None


def _entailment_rank(scored: _Scored) -> float:
    """The key the best-evidence race sorts on.

    §6.2 already ranks anything not labeled "entailment" at -1, so a confident
    *neutral* loses to a marginal entailment. A flagged entailment now joins
    them, which is the whole of the composition guard's effect on the decision:
    it removes a pair from contention rather than inventing a verdict of its
    own. Everything downstream — the contradiction path, the threshold, the
    reasons — is untouched, so a corpus that genuinely refutes the claim is
    reached by §6.2's existing branch 4 rather than by anything here.
    """
    if scored.event.label != "entailment" or scored.event.flagged:
        return -1.0
    return scored.event.score


class ClaimVerifier:
    """§6.2's decision procedure, over an injected `EntailmentScorer`.

    A class rather than a module of functions for one reason: the scorer and
    the threshold are what the whole procedure is parameterised by, and every
    function below needed both. Passing them as arguments through four call
    layers, or reading them from module globals, were the two alternatives —
    the first is noise at every call site, and the second is what made the
    old tests monkeypatch a global to substitute a fake model.

    `threshold` defaults to `settings.claim_verification_threshold`, read at
    construction time rather than at call time so a verifier's behaviour
    cannot change underneath a request that is already running.
    """

    def __init__(
        self,
        scorer: EntailmentScorer,
        threshold: float | None = None,
        observer: ScoringObserver | None = None,
    ):
        if threshold is None:
            # Imported here, not at module scope: the domain layer states its
            # own default without making `import app.domain.verification`
            # depend on configuration being loadable. A caller that passes a
            # threshold explicitly — every unit test does — never touches
            # settings at all.
            from app.core.config import settings

            threshold = settings.claim_verification_threshold

        self._scorer = scorer
        self.threshold = threshold
        self._observer = observer

    # --- scoring -----------------------------------------------------------

    def score(self, premise: str, hypothesis: str) -> tuple[NLILabel, float]:
        """`scorer`, plus a narrowed retry on long premises it declined.

        The full premise is always scored first and its verdict is returned
        unchanged unless a narrower window *entails*. So this can only ever add
        entailments, never remove one, and the CONTRADICTED path keeps seeing the
        whole premise — a contradiction found in one sentence of a chunk that the
        chunk as a whole resolves would be a worse error than the rejection it
        replaces.
        """
        label, score = self._scorer(premise=premise, hypothesis=hypothesis)
        if label == "entailment" or len(premise) <= LONG_PREMISE_CHARS:
            return label, score

        best: tuple[NLILabel, float] | None = None
        for window in _windows(premise):
            if window == premise:
                continue
            w_label, w_score = self._scorer(premise=window, hypothesis=hypothesis)
            if w_label == "entailment" and (best is None or w_score > best[1]):
                best = (w_label, w_score)

        if best is not None:
            logger.info(
                "Long-premise fallback recovered an entailment: %d chars, score %.3f (OQ-3R)",
                len(premise),
                best[1],
            )
            return best
        return label, score

    def _notify(self, event: ScoringEvent) -> None:
        """Report one scored pair to the log and to any injected observer.

        The observer is a diagnostic side channel — a terminal watching a CLI
        run — so its failure must never become the request's failure. Anything
        it raises is logged and dropped; a broken renderer costs a trace line,
        not a verdict.
        """
        from app.core.observability import get_logger

        get_logger(__name__).info(
            "nli_scoring",
            tag=event.tag,
            label=event.label,
            score=round(event.score, 4),
            chunk_id=(
                chunk_key(event.document_id, event.chunk_index)
                if event.document_id is not None and event.chunk_index is not None
                else None
            ),
            premise_chars=len(event.premise),
            hypothesis=event.hypothesis,
            mismatch=event.mismatch_reason,
        )

        if event.flagged:
            logger.warning(
                "identity/composition conflation detected: [%s] scored entailment %.3f for "
                "hypothesis %r, but %s — this entailment is excluded from the "
                "best-evidence race (see app.domain.conflation)",
                event.tag,
                event.score,
                event.hypothesis,
                event.mismatch_reason,
            )

        if self._observer is None:
            return
        try:
            self._observer(event)
        except Exception:  # noqa: BLE001 — see docstring
            logger.exception("Scoring observer raised; continuing verification")

    def _score_citations(self, claim: Claim, chunks_by_tag: dict[str, Chunk]) -> list[_Scored]:
        """Score every cited tag against its chunk, and screen each entailment.

        Returns one `_Scored` per citation, in citation order. A tag with no
        matching chunk gets label `"MISSING_CHUNK"` and score 0.0 — recorded,
        not raised — so a bad/stale citation tag from the LLM's output can
        never crash verification.

        The conflation screen (`app.domain.conflation`) runs **only on pairs
        the model labeled "entailment"**. That is not an optimization: the
        guard exists to stop a spurious entailment from winning the
        best-evidence race, and a finding against a neutral or contradiction
        pair could not change any verdict while still appearing in the trace as
        if it had. Reporting a rejection that rejected nothing is how a
        diagnostic starts lying.
        """
        hypothesis = strip_citation_tags(claim.text)

        scored: list[_Scored] = []
        for tag in claim.citations:
            chunk = chunks_by_tag.get(tag)
            if chunk is None:
                event = ScoringEvent(
                    tag=tag, premise="", hypothesis=hypothesis, label="MISSING_CHUNK", score=0.0
                )
                scored.append(_Scored(event, None))
                self._notify(event)
                continue

            label, score = self.score(chunk.text, hypothesis)
            mismatch = (
                detect_composition_mismatch(chunk.text, hypothesis)
                if label == "entailment"
                else None
            )
            event = ScoringEvent(
                tag=tag,
                premise=chunk.text,
                hypothesis=hypothesis,
                label=label,
                score=score,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                mismatch_reason=mismatch.describe() if mismatch is not None else None,
            )
            scored.append(_Scored(event, chunk))
            self._notify(event)
        return scored

    # --- §6.2 decision -----------------------------------------------------

    def verify_claim(self, claim: Claim, chunks_by_tag: dict[str, Chunk]) -> ClaimVerdict:
        """Decide whether `claim` is supported by its cited chunks.

        Five paths, per §6.2, in priority order:

        1. No citations at all -> UNSUPPORTED, reason="no citation provided".
        2. A cited tag with no matching chunk in `chunks_by_tag` -> scored 0.0
           and recorded (see `_score_citations`); never raises.
        3. The best-scoring *unflagged* entailment among cited chunks is >=
           `self.threshold` -> SUPPORTED.
        4. Otherwise, if any cited chunk was scored "contradiction" -> CONTRADICTED
           (logged at warning level — usually means the generator misread a
           chunk, e.g. a sign flip or wrong date).
        5. Otherwise, if every entailment there was got flagged by the
           composition guard -> UNSUPPORTED, naming the rejected score.
        6. Otherwise -> UNSUPPORTED.

        Path 5 is new (2026-08-09) and path 3 gained one word, which together
        are the whole of `app.domain.conflation`'s effect on this procedure.
        Note the order: **4 still precedes 5**. When the guard removes a
        conflated entailment and the corpus also contains a chunk that
        genuinely contradicts the claim, the verdict is CONTRADICTED, carrying
        that chunk's real score — not an UNSUPPORTED derived from a regex.
        The measured case is `"embedded system is an operating system"`, where
        suppressing a 0.987 conflation lets a 0.802 contradiction that §6.2
        had always computed and never reached decide the verdict.
        """
        # OQ-5, and this branch must come first. A refusal is a statement about the
        # *context* ("the context does not specify the release year"), not a
        # proposition about the world, so there is nothing for evidence to entail
        # or contradict and NLI is the wrong question to ask of it.
        #
        # Scoring them was the root bug. A refusal only landed in UNSUPPORTED by
        # accident — the model usually left it uncited, so it fell through the "no
        # citation provided" path below. When the model *did* cite one, the
        # verifier entailed it (0.904 observed) and "I cannot answer this" became a
        # supported claim, taking `state` to "ok". Every refusal-related signal,
        # D4 included, was resting on that accident.
        if claim.kind == "refusal":
            return ClaimVerdict(
                text=claim.text,
                status="REFUSAL",
                citations=claim.citations,
                evidence_score=None,  # deliberately never scored
                # Resolved for the audit trail so a reader can see what the model
                # looked at before declining, but nothing is scored against them.
                chunk_ids=[
                    chunk_key(chunk.document_id, chunk.chunk_index)
                    for tag in claim.citations
                    if (chunk := chunks_by_tag.get(tag)) is not None
                ],
                reason=None,
            )

        if not claim.citations:
            return ClaimVerdict(
                text=claim.text,
                status="UNSUPPORTED",
                citations=claim.citations,
                evidence_score=None,
                chunk_ids=[],
                reason="no citation provided",
            )

        scored = self._score_citations(claim, chunks_by_tag)

        # chunk_ids: the canonical (document_id, chunk_index) keys of citations
        # that actually resolved to a chunk — i.e. the chunks actually used in
        # scoring, per §6.4's comment on `ClaimVerdict.chunk_ids`. Missing tags
        # contribute no chunk_id (there is no chunk to key).
        chunk_ids = [chunk_key(s.chunk.document_id, s.chunk.chunk_index) for s in scored if s.chunk]

        # Mirrors §6.2's pseudocode: among all scored citations, pick the one
        # with the highest entailment score; anything not labeled "entailment"
        # — and, since 2026-08-09, anything the composition guard flagged —
        # loses the race unconditionally. See `_entailment_rank`.
        best = max(scored, key=_entailment_rank)
        best_tag, best_label, best_score = best.event.tag, best.event.label, best.event.score

        # `_entailment_rank(best)` is `best_score` for a surviving entailment
        # and -1.0 otherwise, so this one comparison says "an unflagged
        # entailment cleared the threshold" and nothing else. Spelling it as
        # `best_label == "entailment" and best_score >= threshold` would read
        # the same and be wrong: `max` returns the *first* element when every
        # key ties at -1, which after flagging can be an entailment whose score
        # is exactly the one that must not count.
        if _entailment_rank(best) >= self.threshold:
            return ClaimVerdict(
                text=claim.text,
                status="SUPPORTED",
                citations=claim.citations,
                evidence_score=best_score,
                chunk_ids=chunk_ids,
                reason=None,
            )

        contradictions = [
            (s.event.tag, s.event.score) for s in scored if s.event.label == "contradiction"
        ]
        if contradictions:
            worst_tag, worst_score = max(contradictions, key=lambda t: t[1])
            logger.warning(
                "Claim contradicted by cited evidence: claim=%r tag=%s score=%.3f "
                "(likely the generator misread the chunk — check for sign flips "
                "or date/number transcription errors)",
                claim.text,
                worst_tag,
                worst_score,
            )
            return ClaimVerdict(
                text=claim.text,
                status="CONTRADICTED",
                citations=claim.citations,
                evidence_score=worst_score,
                chunk_ids=chunk_ids,
                reason=f"cited chunk [{worst_tag}] contradicts the claim",
            )

        # Every entailment there was got flagged, and no contradiction outranked
        # it. Returned here rather than folded into the reason ladder below
        # because the score to report is the *flagged* one — the number a reader
        # would otherwise go looking for when they see a confident claim come
        # back unsupported — and `best` is not it: with every key tied at -1,
        # `max` returned whichever citation came first.
        flagged = [s for s in scored if s.event.flagged]
        if flagged and _entailment_rank(best) < 0:
            rejected = max(flagged, key=lambda s: s.event.score)
            return ClaimVerdict(
                text=claim.text,
                status="UNSUPPORTED",
                citations=claim.citations,
                evidence_score=rejected.event.score,
                chunk_ids=chunk_ids,
                reason=(
                    f"entailment {rejected.event.score:.3f} on [{rejected.event.tag}] rejected: "
                    f"{rejected.event.mismatch_reason}"
                ),
            )

        missing_tags = [s.event.tag for s in scored if s.chunk is None]
        if missing_tags and len(missing_tags) == len(scored):
            reason = f"cited chunk(s) not found in retrieved context: {', '.join(missing_tags)}"
        elif best_label == "entailment":
            reason = (
                f"best entailment score {best_score:.3f} on [{best_tag}] is below "
                f"threshold {self.threshold:.3f}"
            )
        else:
            reason = "no cited chunk entails this claim"

        return ClaimVerdict(
            text=claim.text,
            status="UNSUPPORTED",
            citations=claim.citations,
            evidence_score=best_score,
            chunk_ids=chunk_ids,
            reason=reason,
        )

    def verify_claims(self, claims: list[Claim], chunks: list[Chunk]) -> list[ClaimVerdict]:
        """Batch entry point: verify every claim against the retrieved chunks.

        `chunks` is the full retrieved/reranked list (the same one passed to
        `format_context()` when generating the answer); tags are (re)computed
        here via `build_chunk_tag_map`.

        Prefer `verify_tagged()` from the query pipeline — see its docstring
        for why threading the *exact* map through matters there. This overload
        stays for callers (eval scripts, tests) that only have a chunk list.
        """
        verdicts = self.verify_tagged(claims, build_chunk_tag_map(chunks))
        log_verdicts(verdicts)
        return verdicts

    def verify_tagged(
        self, claims: list[Claim], chunks_by_tag: dict[str, Chunk]
    ) -> list[ClaimVerdict]:
        """Verify against a tag map the caller already built.

        The query pipeline uses this so the citations the LLM emitted resolve
        against the identical mapping it was shown. Both derivations are
        positional and agree today, but deriving the same mapping twice in two
        places means any future divergence — a reordering, a changed tag scheme
        — would resolve citations against the wrong chunks and emit confidently
        SUPPORTED verdicts backed by evidence the LLM never saw. That failure is
        silent and is precisely what this system exists to prevent.
        """
        return [self.verify_claim(claim, chunks_by_tag) for claim in claims]


def log_verdicts(verdicts: list[ClaimVerdict]) -> dict[str, int]:
    """Emit §9's verdict distribution and bump the claim counters.

    Purely additive — it reads verdicts and returns a summary, and never
    alters one. Each claim is logged individually with the evidence it was
    scored against and the reason it was rejected, because §9's stated
    purpose is being able to answer "why was this claim accepted or
    rejected" after the fact, which an aggregate count alone cannot.
    """
    from app.core.observability import get_logger, record_verdicts

    obs_logger = get_logger(__name__)
    distribution = record_verdicts(verdicts)

    for verdict in verdicts:
        obs_logger.info(
            "claim_verdict",
            status=verdict.status,
            claim=verdict.text,
            citations=verdict.citations,
            chunk_ids=verdict.chunk_ids,
            evidence_score=verdict.evidence_score,
            reason=verdict.reason,
        )

    obs_logger.info("verification_complete", total=len(verdicts), distribution=distribution)
    return distribution
