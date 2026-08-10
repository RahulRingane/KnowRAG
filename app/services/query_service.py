"""The query use case — classify, then retrieve, generate, verify, assemble (§5).

    input
      |
      v
    [classifier()] -> ClassifiedInput
      |
      +-- "question" ------------------+     +-- "fact" -----------------------+
      |                                |     |                                 |
      v                                |     v                                 |
    [retriever.search()]   -> chunks   |   [retriever.search()]   -> chunks     |
      |                                |     |                                 |
      v                                |     v                                 |
    [format_context()]     -> block +  |   [build_chunk_tag_map()] -> tag map   |
      |                       tag map  |     |                                 |
      v                                |     |   (no generation call)          |
    [generator.generate()] -> Claims   |     v                                 |
      |                                |   [verifier.verify_claim()] -> one    |
      v                                |     |                     verdict on  |
    [verifier.verify()]    -> verdicts |     |                     the input   |
      |                                |     v                                 |
      v                                |   [assemble_fact_check_response()]    |
    [assemble_response()]              |     |                                 |
      +--------------------------------+-----+---------------------------------+
                                       |
                                       v
                             FactCheckedResponse

Routing (added 2026-08-09) is a fork taken once, up front. The two routes
never call each other and neither can re-enter the other, so the structural
guarantee below is unaffected: a single request still retrieves exactly once.

From the old `app/chain.py`. The structure is unchanged; what changed is
that the three collaborators arrive through the constructor instead of
through lazy module imports. That was the honest signal in the original —
its own docstring explained that `retriver` and `verify` were imported
inside functions so the module would keep loading while other agents edited
them. A port makes that structural rather than a workaround: this module
cannot break when an implementation changes, because it never names one.

Deliberately **not** an agentic loop (§5's explicit design constraint):
retrieval happens exactly once, before the LLM ever runs, and there is no
path back from generation/verification into retrieval. The LLM never decides
mid-chain that it wants more context — that keeps the system auditable
(every answer traces to one fixed retrieval set) and bounds latency to one
retrieval round + one generation call + one verification pass, full stop.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Iterator

from app.core import timing
from app.core.observability import (
    generation_latency_seconds,
    get_logger,
    observe,
    verification_latency_seconds,
)
from app.domain.assembly import assemble_fact_check_response, assemble_response
from app.domain.classification import classify_input
from app.domain.context import format_context
from app.domain.models import Chunk, Claim, ClaimVerdict, FactCheckedResponse, chunk_key
from app.domain.ports import ClaimGenerator, InputClassifier, Retriever, ScoringObserver
from app.domain.verification import ClaimVerifier, build_chunk_tag_map, log_verdicts

logger = get_logger(__name__)


@contextmanager
def _stage(latency_ms: dict[str, float], name: str, *, nested: tuple[str, ...] = ()):
    """Time a stage into `latency_ms[name]`, exclusive of its `nested` costs.

    **Every key in `latency_ms` names a disjoint span**, which is the property
    the whole breakdown rests on: `sum(latency_ms.values())` is the pipeline's
    wall time, and the CLI has printed it as `took` since before this existed.
    An overlapping key would silently inflate that number, so a sub-cost the
    layers below reported through `app.core.timing` — the rerank inside
    retrieval, a model load inside whichever stage first touched it — is
    subtracted back out of the stage that contained it and reported under its
    own name instead.

    Sub-costs are diffed across the stage rather than read absolutely, so a
    model loaded during retrieval is not counted again against verification.
    Both sides come from `timing.snapshot()`, so a stage that incurs nothing
    behaves exactly as the bare timer it replaces, and outside a
    `timing.collect()` scope every diff is zero and this degrades to that timer.
    """
    before = timing.snapshot()
    started = time.perf_counter()
    try:
        yield
    finally:
        total = (time.perf_counter() - started) * 1000
        after = timing.snapshot()
        for key in nested:
            incurred = after.get(key, 0.0) - before.get(key, 0.0)
            if incurred > 0:
                latency_ms[key] = latency_ms.get(key, 0.0) + incurred
                total -= incurred
        # Clamped at zero rather than allowed negative: the sub-cost timers
        # start fractionally before the stage timer in some interleavings, and
        # a negative duration in a latency report is worse than a lost
        # microsecond.
        latency_ms[name] = max(total, 0.0)


class QueryService:
    """The single non-agentic entry point for the whole pipeline."""

    def __init__(
        self,
        retriever: Retriever,
        generator: ClaimGenerator,
        verifier: ClaimVerifier,
        classifier: InputClassifier | None = None,
        top_k: int | None = None,
    ):
        self._retriever = retriever
        self._generator = generator
        self._verifier = verifier
        # Injected like every other collaborator, but defaulted — the fallback
        # is `app.domain.classification.classify_input`, which is pure domain
        # logic with no I/O, so taking it by default costs nothing and keeps
        # every existing construction site working. Compare `top_k` below,
        # which defaults out of settings for the same reason. A classifier
        # that *does* reach infrastructure (a model, an LLM call) must be
        # passed in explicitly; there is deliberately no way for this
        # constructor to reach one on its own.
        self._classifier: InputClassifier = classifier or classify_input
        if top_k is None:
            from app.core.config import settings

            top_k = settings.top_k_rerank
        self._top_k = top_k

    # --- stages -------------------------------------------------------------

    def _retrieve(self, question: str, k: int | None) -> list[Chunk]:
        return self._retriever.search(question, k if k is not None else self._top_k)

    def _generate(
        self, question: str, context_block: str, retrieved_chunk_ids: list[str]
    ) -> list[Claim]:
        with observe(generation_latency_seconds):
            claims = self._generator.generate(
                question, context_block, chunk_ids=retrieved_chunk_ids
            )

        logger.info(
            "generation_complete",
            claim_count=len(claims),
            uncited_claims=sum(1 for c in claims if not c.citations),
            context_chunk_ids=retrieved_chunk_ids,
        )
        return claims

    def _verify(self, claims: list[Claim], tag_map: dict[str, Chunk]) -> list[ClaimVerdict]:
        """§6: each claim is checked against its cited chunk(s).

        Passes the *exact* `tag_map` that `format_context` handed to the
        generator, rather than passing the chunk list and letting the verifier
        rebuild `C1..Cn` itself. Both derivations are positional and agree
        today, but deriving the same mapping twice in two places means any
        future divergence — a reordering, a changed tag scheme — would resolve
        citations against the wrong chunks and emit confidently SUPPORTED
        verdicts backed by evidence the generator never saw. That failure is
        silent and is precisely what this system exists to prevent, so the
        mapping is threaded through as a single value instead.
        """
        with observe(verification_latency_seconds):
            verdicts = self._verifier.verify_tagged(claims, tag_map)

        # `verify_claims()` does this for its own callers; this path calls
        # `verify_tagged` (to thread the exact tag map through), so the §9
        # verdict distribution has to be recorded explicitly here or it is
        # silently missing for every request that goes through the service.
        log_verdicts(verdicts)
        return verdicts

    # --- entry points -------------------------------------------------------

    def run(self, question: str, k: int | None = None) -> FactCheckedResponse:
        """Classify the input, then run the route it belongs to.

        Two routes, and the choice between them is made exactly once, before
        anything else happens:

            question -> `_answer()`      retrieve, generate, verify, assemble
            fact     -> `_fact_check()`  retrieve, verify the caller's own
                                         sentence, assemble

        **Routing is a fork, not a loop.** Each route runs to completion and
        neither can hand control to the other, so §5's no-agentic-loop
        guarantee is untouched — adding a second path did not add a second
        retrieval round to any single request. Both routes still retrieve
        exactly once, before any scoring, with no path back.

        Both return a `FactCheckedResponse` carrying `input_type`, so a caller
        that does not care about the distinction reads it exactly as before.
        """
        classified = self._classifier(question)

        logger.info(
            "input_classified",
            input_type=classified.input_type,
            text=classified.text,
        )

        if classified.input_type == "fact":
            return self._fact_check(classified.text, k)
        return self._answer(classified.text, k)

    def _answer(self, question: str, k: int | None = None) -> FactCheckedResponse:
        """The question route: exactly one retrieval round, one generation
        call, one verification pass, then assembly — no loop back into
        retrieval at any point, which is what makes "no agentic loop"
        structural rather than a convention someone can accidentally break
        later.

        Unchanged from the single path that preceded routing.
        """
        latency_ms: dict[str, float] = {}

        # One collection scope per route, opened here rather than in `run()`,
        # so any caller reaching a route directly — an eval script, a test —
        # gets the same breakdown the API does.
        with timing.collect():
            with _stage(latency_ms, "retrieval_ms", nested=("rerank_ms", "model_load_ms")):
                chunks = self._retrieve(question, k)

            with _stage(latency_ms, "format_context_ms"):
                context_block, tag_map = format_context(chunks)

            retrieved_chunk_ids = [chunk_key(c.document_id, c.chunk_index) for c in chunks]

            with _stage(latency_ms, "generation_ms"):
                claims = self._generate(question, context_block, retrieved_chunk_ids)

            # `model_load_ms` again: on a cold process the NLI model has not
            # been touched before this point, so its load lands here and would
            # otherwise read as slow scoring.
            with _stage(latency_ms, "verification_ms", nested=("model_load_ms",)):
                verdicts = self._verify(claims, tag_map)

            t0 = time.perf_counter()
            response = assemble_response(question, verdicts, retrieved_chunk_ids, latency_ms)
            # Set on the response, not on the local dict: pydantic copies
            # `latency_ms` during validation, so mutating the local dict after
            # assembly would silently drop this stage from the serialized response.
            response.latency_ms["assembly_ms"] = (time.perf_counter() - t0) * 1000

        logger.info(
            "query_complete",
            question=question,
            state=response.state,
            supported=len(response.claims) - len(response.rejected_claims),
            rejected=len(response.rejected_claims),
            retrieved_chunk_ids=retrieved_chunk_ids,
            latency_ms={key: round(value, 2) for key, value in response.latency_ms.items()},
        )
        return response

    def _fact_check(self, statement: str, k: int | None = None) -> FactCheckedResponse:
        """The fact route: check the caller's own sentence against the corpus.

        Retrieve evidence for the statement, then score *the statement* — not
        something an LLM wrote about it — against every retrieved chunk with
        the same `ClaimVerifier` the question route uses. §6.2's decision
        procedure is reused verbatim rather than reimplemented, so a statement
        and a generated claim are held to identical standards; only the source
        of the sentence differs.

        **There is no generation call on this path**, which is the point.
        Asking an LLM to answer "RISC has instruction pipelining" produced
        claims that were then fact-checked — so the thing verified was the
        model's output, never the caller's assertion. Removing the generator
        removes both the cost and that substitution. `latency_ms` therefore
        carries no `generation_ms` key, rather than a zero: nothing generated,
        so there is no duration to report.

        The claim cites every retrieved chunk, so `verify_claim` scores the
        statement against all of them and keeps the best entailment — the
        caller supplied no citations and cannot be expected to.

        Tags come from `build_chunk_tag_map` rather than being threaded out of
        `format_context`. That is safe *here* specifically because no
        generator runs: the divergence that rule guards against is between the
        tags an LLM was shown and the tags its citations are resolved with,
        and on this path nothing is ever shown a context block. Reusing
        `format_context` would mean building a context string for no reader.
        """
        latency_ms: dict[str, float] = {}

        with timing.collect():
            with _stage(latency_ms, "retrieval_ms", nested=("rerank_ms", "model_load_ms")):
                chunks = self._retrieve(statement, k)

            tag_map = build_chunk_tag_map(chunks)
            retrieved_chunk_ids = [chunk_key(c.document_id, c.chunk_index) for c in chunks]

            claim = Claim(kind="assertion", text=statement, citations=list(tag_map))

            with _stage(latency_ms, "verification_ms", nested=("model_load_ms",)):
                with observe(verification_latency_seconds):
                    verdict = self._verifier.verify_claim(claim, tag_map)
                # The §9 verdict distribution is recorded for this route too, so
                # the metrics answer "what did the system decide" across both
                # paths rather than silently covering only one of them.
                log_verdicts([verdict])

            t0 = time.perf_counter()
            response = assemble_fact_check_response(
                statement, verdict, retrieved_chunk_ids, latency_ms
            )
            response.latency_ms["assembly_ms"] = (time.perf_counter() - t0) * 1000

        logger.info(
            "fact_check_complete",
            statement=statement,
            state=response.state,
            verdict=verdict.status,
            evidence_score=verdict.evidence_score,
            retrieved_chunk_ids=retrieved_chunk_ids,
            latency_ms={key: round(value, 2) for key, value in response.latency_ms.items()},
        )
        return response

    def stream(self, question: str, k: int | None = None) -> Iterator[tuple[str, dict]]:
        """Streaming variant of `run()`, for §7's `POST /query/stream`.

        Yields `(event_name, payload)` pairs:

            ("retrieval",    {"retrieved_chunk_ids": [...]})
            ("token",        {"text": "<delta>"})            # zero or more
            ("verification", <FactCheckedResponse as dict>)  # exactly one, last

        Verification is a single terminal event, not interleaved, because it
        cannot start until the answer is complete — §7 calls this out directly.
        Streaming a claim the verifier later rejects would show the user text
        the system is about to disown, which is the opposite of the point.

        A generator rather than an async generator on purpose: both the NLI
        verification pass and the underlying SDK stream are blocking, so
        Starlette iterating this in a threadpool is correct. Making it `async`
        would park that work on the event loop and stall every other request.

        The same no-agentic-loop guarantee as `run()` holds: retrieval happens
        once, before generation, with no path back.

        Classifies and routes exactly as `run()` does, so `/query/stream` and
        `/query` cannot disagree about what an input is. A fact emits no
        `token` events at all — there is no generation on that route to stream
        — but still emits `retrieval` then `verification`, so the event
        contract holds and a client needs no special case beyond tolerating
        zero tokens, which it must already do for a generation that yields
        nothing.
        """
        classified = self._classifier(question)

        logger.info(
            "input_classified",
            input_type=classified.input_type,
            text=classified.text,
        )

        if classified.input_type == "fact":
            response = self._fact_check(classified.text, k)
            yield "retrieval", {"retrieved_chunk_ids": response.retrieved_chunk_ids}
            yield "verification", response.model_dump()
            return

        yield from self._stream_answer(classified.text, k)

    def _stream_answer(self, question: str, k: int | None = None) -> Iterator[tuple[str, dict]]:
        """The question route's streaming body — unchanged by routing."""
        latency_ms: dict[str, float] = {}

        # `timing.collect()` is not opened around this route. A generator's
        # frame is suspended at every `yield`, so the scope would be entered and
        # exited on whatever context the consumer happens to iterate from, and
        # `contextvars` does not follow a generator across those resumptions.
        # The sub-stage keys are therefore absent here rather than wrong, and
        # `_stage` degrades to the plain timer this always used — `/query`
        # remains the path with the full breakdown.
        with _stage(latency_ms, "retrieval_ms"):
            chunks = self._retrieve(question, k)

        context_block, tag_map = format_context(chunks)
        retrieved_chunk_ids = [chunk_key(c.document_id, c.chunk_index) for c in chunks]

        yield "retrieval", {"retrieved_chunk_ids": retrieved_chunk_ids}

        t0 = time.perf_counter()
        parts: list[str] = []
        with observe(generation_latency_seconds):
            for delta in self._generator.stream(question, context_block):
                parts.append(delta)
                yield "token", {"text": delta}
        latency_ms["generation_ms"] = (time.perf_counter() - t0) * 1000

        raw = "".join(parts)
        try:
            payload = json.loads(raw) if raw.strip() else {"claims": []}
        except json.JSONDecodeError:
            # A truncated stream is not a reason to emit an unverified answer.
            # Falling through with zero claims yields "insufficient_evidence",
            # which is the honest outcome: nothing was successfully generated,
            # so nothing can be supported.
            logger.warning("Streamed generation did not parse as JSON; treating as no claims")
            payload = {"claims": []}

        claims = [Claim(**c) for c in payload.get("claims", [])]

        with _stage(latency_ms, "verification_ms"):
            verdicts = self._verify(claims, tag_map)

        t0 = time.perf_counter()
        response = assemble_response(question, verdicts, retrieved_chunk_ids, latency_ms)
        response.latency_ms["assembly_ms"] = (time.perf_counter() - t0) * 1000

        yield "verification", response.model_dump()


def build_default_query_service(
    retriever: Retriever | None = None,
    scoring_observer: ScoringObserver | None = None,
) -> QueryService:
    """The production object graph, in one place.

    Used by the API's dependency provider, the CLI, and the eval scripts, so
    all three exercise identical wiring — §7.2's "exactly one code path for
    retrieval, exercised by both the CLI and the API".

    `retriever` overrides only the retrieval leg, leaving every other edge of
    the graph identical. It exists so a caller that wants to *observe* the
    retrieval step — `app.cli.query` printing the top candidates it got — can
    decorate the production retriever instead of rebuilding the graph beside
    this function. A second hand-wired construction site is exactly how the
    CLI and the API drift apart, and §7.2 is the rule that forbids it. Anything
    passed here must still satisfy `Retriever`, so the service cannot tell.

    `scoring_observer` is the same idea one stage later: it reaches the
    `ClaimVerifier` so the CLI can print the `(premise, hypothesis)` pairs NLI
    actually scored, between the candidates block and the verdict. Observation
    is the only thing either parameter buys — neither changes what the pipeline
    decides, and both default to the production graph when omitted.
    """
    from app.infrastructure.llm.provider import LLMClaimGenerator
    from app.infrastructure.ml.nli import predict_nli
    from app.infrastructure.search.hybrid_retriever import build_default_retriever

    return QueryService(
        retriever=retriever if retriever is not None else build_default_retriever(),
        generator=LLMClaimGenerator(),
        verifier=ClaimVerifier(scorer=predict_nli, observer=scoring_observer),
        # Named explicitly even though it is the constructor default, because
        # this is the composition root and the choice of classifier is exactly
        # the kind of decision that belongs visible here — it is the line an
        # LLM-backed implementation would replace.
        classifier=classify_input,
    )
