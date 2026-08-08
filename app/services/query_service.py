"""The query use case — retrieve, generate, verify, assemble (§5).

    question
       |
       v
    [retriever.search()]  -> retrieved_chunks: list[Chunk]
       |
       v
    [format_context()]    -> numbered context block + tag -> Chunk map
       |
       v
    [generator.generate()]-> list[Claim]
       |
       v
    [verifier.verify()]   -> ClaimVerdict per claim
       |
       v
    [assemble_response()] -> FactCheckedResponse

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
from typing import Iterator

from app.core.observability import (
    generation_latency_seconds,
    get_logger,
    observe,
    verification_latency_seconds,
)
from app.domain.assembly import assemble_response
from app.domain.context import format_context
from app.domain.models import Chunk, Claim, ClaimVerdict, FactCheckedResponse, chunk_key
from app.domain.ports import ClaimGenerator, Retriever
from app.domain.verification import ClaimVerifier, log_verdicts

logger = get_logger(__name__)


class QueryService:
    """The single non-agentic entry point for the whole pipeline."""

    def __init__(
        self,
        retriever: Retriever,
        generator: ClaimGenerator,
        verifier: ClaimVerifier,
        top_k: int | None = None,
    ):
        self._retriever = retriever
        self._generator = generator
        self._verifier = verifier
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
        """Exactly one retrieval round, one generation call, one verification
        pass, then assembly — no loop back into retrieval at any point, which
        is what makes "no agentic loop" structural rather than a convention
        someone can accidentally break later.
        """
        latency_ms: dict[str, float] = {}

        t0 = time.perf_counter()
        chunks = self._retrieve(question, k)
        latency_ms["retrieval_ms"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        context_block, tag_map = format_context(chunks)
        latency_ms["format_context_ms"] = (time.perf_counter() - t0) * 1000

        retrieved_chunk_ids = [chunk_key(c.document_id, c.chunk_index) for c in chunks]

        t0 = time.perf_counter()
        claims = self._generate(question, context_block, retrieved_chunk_ids)
        latency_ms["generation_ms"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        verdicts = self._verify(claims, tag_map)
        latency_ms["verification_ms"] = (time.perf_counter() - t0) * 1000

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
        """
        latency_ms: dict[str, float] = {}

        t0 = time.perf_counter()
        chunks = self._retrieve(question, k)
        latency_ms["retrieval_ms"] = (time.perf_counter() - t0) * 1000

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

        t0 = time.perf_counter()
        verdicts = self._verify(claims, tag_map)
        latency_ms["verification_ms"] = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        response = assemble_response(question, verdicts, retrieved_chunk_ids, latency_ms)
        response.latency_ms["assembly_ms"] = (time.perf_counter() - t0) * 1000

        yield "verification", response.model_dump()


def build_default_query_service() -> QueryService:
    """The production object graph, in one place.

    Used by the API's dependency provider, the CLI, and the eval scripts, so
    all three exercise identical wiring — §7.2's "exactly one code path for
    retrieval, exercised by both the CLI and the API".
    """
    from app.infrastructure.llm.provider import LLMClaimGenerator
    from app.infrastructure.ml.nli import predict_nli
    from app.infrastructure.search.hybrid_retriever import build_default_retriever

    return QueryService(
        retriever=build_default_retriever(),
        generator=LLMClaimGenerator(),
        verifier=ClaimVerifier(scorer=predict_nli),
    )
