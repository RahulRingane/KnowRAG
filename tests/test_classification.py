"""Query/fact classification and the routing it drives.

Two layers, tested separately because they fail separately:

- `app.domain.classification` decides *what an input is*. Pure string
  inspection, so these tests are just a table.
- `app.services.query_service` decides *what happens next*. The tests below
  assert the thing that actually matters — that a statement never reaches the
  generator — by giving the service a generator that fails the test if it is
  called at all. A test that only checked `input_type` on the response would
  pass against a service that classified correctly and then ran the old
  single path anyway, which is precisely the bug worth catching.

Fully offline: the retriever, generator and entailment scorer are all stubs,
per §8.1.
"""

from __future__ import annotations

import pytest

from app.domain.assembly import (
    FACT_CONTRADICTED_MESSAGE,
    FACT_SUPPORTED_MESSAGE,
    FACT_UNSUPPORTED_MESSAGE,
)
from app.domain.classification import classify_input, first_word
from app.domain.models import Chunk, Claim, ClassifiedInput
from app.domain.verification import ClaimVerifier
from app.services.query_service import QueryService


# --- The heuristic -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # The four cases the routing was specified against.
        "what is RISC?",
        "why is CISC preferred?",
        # Interrogative opener, no question mark — rule 2 carries these.
        "what is RISC",
        "how does pipelining work",
        "when is an interrupt serviced",
        "where does the stack pointer live",
        "who defines the ISA",
        "is RISC faster than CISC",
        "does the MMU translate addresses",
        "can an embedded system run without an OS",
        "has the watchdog timer expired",
        # Question mark, no interrogative opener — rule 1 carries these.
        "RISC or CISC?",
        "pipelining?",
        # Case and surrounding punctuation must not hide the opener.
        "WHAT IS RISC",
        "  What is RISC  ",
        '"What is RISC"',
        "- what is RISC",
    ],
)
def test_questions_are_classified_as_questions(text):
    assert classify_input(text).input_type == "question"


@pytest.mark.parametrize(
    "text",
    [
        # The two cases the routing was specified against.
        "RISC has instruction pipelining",
        "CISC has fewer registers",
        # Assertions that open with a word appearing *inside* the
        # interrogative set but not in first position.
        "An embedded system is task-specific",
        "The MMU does address translation",
        "Pipelining can improve throughput",
        # A declarative opening with a noun, which is the common case.
        "RISC is better than CISC",
    ],
)
def test_statements_are_classified_as_facts(text):
    assert classify_input(text).input_type == "fact"


def test_a_question_mark_beats_a_declarative_opener():
    """Rule 1 runs first on purpose: explicit punctuation is the strongest
    signal a caller can give, and it is the only one available for a question
    phrased as an inversion the opener set does not cover."""
    assert classify_input("RISC is better than CISC?").input_type == "question"


def test_trailing_whitespace_does_not_change_the_verdict():
    """`echo "what is RISC?" | python -m app.cli.query` pipes a trailing
    newline; it must classify identically to the typed form."""
    assert classify_input("what is RISC?\n").input_type == "question"


def test_the_original_text_is_carried_through_unmodified():
    """The route operates on `ClassifiedInput.text`, so the classifier is the
    single place that could ever normalise an input. This one does not."""
    text = "  RISC has instruction pipelining  "
    assert classify_input(text).text == text


@pytest.mark.parametrize("text", ["", "   ", "???", "..."])
def test_degenerate_input_classifies_rather_than_raises(text):
    """Empty or punctuation-only input is a degenerate value, not an error.
    `first_word` returning "" must not blow up the lookup."""
    assert classify_input(text).input_type in ("question", "fact")


def test_first_word_strips_framing_punctuation():
    assert first_word('  "What is RISC?"') == "what"
    assert first_word("• has the timer expired") == "has"
    assert first_word("!!!") == ""


# --- Routing -----------------------------------------------------------------


CHUNKS = [
    Chunk(source="qdrant", document_id=1, chunk_index=0, text="RISC pipelines instructions."),
    Chunk(source="elastic", document_id=1, chunk_index=7, text="CISC has a large register file."),
]


class _StubRetriever:
    def __init__(self, chunks=CHUNKS):
        self._chunks = chunks
        self.queries: list[str] = []

    def search(self, query, k):
        self.queries.append(query)
        return self._chunks


class _ExplodingGenerator:
    """Fails the test if generation is reached.

    The fact route's whole justification is that it does not call an LLM, and
    "did not call it" is only checkable by making the call an error.
    """

    def generate(self, question, context_block, chunk_ids=None):
        raise AssertionError("the fact route must not call the generator")

    def stream(self, question, context_block):
        raise AssertionError("the fact route must not call the generator")


class _RecordingGenerator:
    def __init__(self, claims):
        self._claims = claims
        self.calls: list[tuple[str, str]] = []

    def generate(self, question, context_block, chunk_ids=None):
        self.calls.append((question, context_block))
        return self._claims

    def stream(self, question, context_block):
        self.calls.append((question, context_block))
        yield '{"claims": []}'


def _service(generator, label="entailment", score=0.99, retriever=None):
    """A fully offline `QueryService` with a fixed NLI verdict."""
    return QueryService(
        retriever=retriever or _StubRetriever(),
        generator=generator,
        verifier=ClaimVerifier(scorer=lambda premise, hypothesis: (label, score), threshold=0.55),
        top_k=2,
    )


def test_a_question_routes_to_the_generator():
    generator = _RecordingGenerator(
        [Claim(kind="assertion", text="RISC pipelines instructions.", citations=["C1"])]
    )

    response = _service(generator).run("what is RISC?")

    assert response.input_type == "question"
    assert len(generator.calls) == 1, "the question route must generate"
    assert response.state == "ok"


def test_a_fact_skips_generation_entirely():
    response = _service(_ExplodingGenerator()).run("RISC has instruction pipelining")

    assert response.input_type == "fact"
    assert response.state == "ok"
    assert response.answer == FACT_SUPPORTED_MESSAGE


def test_the_fact_route_reports_no_generation_latency():
    """No generation happened, so there is no `generation_ms` to report. A
    zero would claim a call was made and took no time."""
    response = _service(_ExplodingGenerator()).run("CISC has fewer registers")

    assert "generation_ms" not in response.latency_ms
    assert "retrieval_ms" in response.latency_ms
    assert "verification_ms" in response.latency_ms


def test_the_fact_route_verifies_the_callers_own_sentence():
    """Not a model's paraphrase of it — the exact text that arrived."""
    statement = "RISC has instruction pipelining"
    scored: list[str] = []

    service = QueryService(
        retriever=_StubRetriever(),
        generator=_ExplodingGenerator(),
        verifier=ClaimVerifier(
            scorer=lambda premise, hypothesis: (scored.append(hypothesis), ("entailment", 0.9))[1],
            threshold=0.55,
        ),
        top_k=2,
    )
    response = service.run(statement)

    assert scored == [statement, statement], "scored against each retrieved chunk, unaltered"
    assert response.question == statement
    assert len(response.claims) == 1
    assert response.claims[0].text == statement


def test_the_fact_route_retrieves_using_the_statement():
    retriever = _StubRetriever()
    _service(_ExplodingGenerator(), retriever=retriever).run("CISC has fewer registers")

    assert retriever.queries == ["CISC has fewer registers"]


def test_a_contradicted_statement_is_not_reported_as_insufficient_evidence():
    """The reason `state` grew a third value. "The corpus refutes this" and
    "the corpus is silent on this" are different findings, and collapsing them
    would understate a refutation."""
    response = _service(_ExplodingGenerator(), label="contradiction", score=0.95).run(
        "RISC has no pipelining"
    )

    assert response.state == "contradicted"
    assert response.answer == FACT_CONTRADICTED_MESSAGE
    assert response.claims[0].status == "CONTRADICTED"


def test_an_unsupported_statement_is_insufficient_evidence():
    response = _service(_ExplodingGenerator(), label="neutral", score=0.4).run(
        "RISC was invented in Antarctica"
    )

    assert response.state == "insufficient_evidence"
    assert response.answer == FACT_UNSUPPORTED_MESSAGE
    assert response.claims[0].status == "UNSUPPORTED"


def test_a_below_threshold_entailment_is_unsupported():
    response = _service(_ExplodingGenerator(), label="entailment", score=0.30).run(
        "RISC has instruction pipelining"
    )

    assert response.state == "insufficient_evidence"
    assert response.claims[0].status == "UNSUPPORTED"


class TestThresholdAppliesToFactsOnly:
    """The 2026-08-09 asymmetry: an identical sub-threshold entailment score
    decides the fact route's answer and only annotates the question route's.

    A statement's verdict *is* the result the caller asked for. A question's
    verdict grades an answer that was generated from retrieved context, and
    withholding it over a score of 0.489 was losing correct answers — the
    reported "what is pipelining" failure.
    """

    CLAIM = Claim(
        kind="assertion",
        text="Pipelining executes instruction stages in parallel.",
        citations=["C1"],
    )

    def test_a_question_answers_despite_a_sub_threshold_score(self):
        service = _service(_RecordingGenerator([self.CLAIM]), label="entailment", score=0.489)

        response = service.run("what is pipelining")

        assert response.input_type == "question"
        assert response.state == "ok"
        assert "Pipelining executes instruction stages in parallel." in response.answer

    def test_the_score_is_still_recorded_on_the_claim(self):
        """Not filtering is not the same as not checking. The verdict, the
        score and the reason all stay on the response."""
        service = _service(_RecordingGenerator([self.CLAIM]), label="entailment", score=0.489)

        response = service.run("what is pipelining")

        assert response.claims[0].status == "UNSUPPORTED"
        assert response.claims[0].evidence_score == pytest.approx(0.489)
        assert "below threshold" in response.claims[0].reason
        assert "verification_ms" in response.latency_ms, "the NLI pass still runs"

    def test_the_same_score_still_blocks_a_fact(self):
        response = _service(_ExplodingGenerator(), label="entailment", score=0.489).run(
            "pipelining executes instruction stages in parallel"
        )

        assert response.input_type == "fact"
        assert response.state == "insufficient_evidence"
        assert response.answer == FACT_UNSUPPORTED_MESSAGE

    def test_a_contradicted_claim_is_still_kept_out_of_an_answer(self):
        """The exception to the above. Evidence that disagrees is a finding,
        not an unconfirmed phrasing, so the question route still withholds it."""
        service = _service(_RecordingGenerator([self.CLAIM]), label="contradiction", score=0.95)

        response = service.run("what is pipelining")

        assert response.claims[0].status == "CONTRADICTED"
        assert "Pipelining executes" not in response.answer
        assert response.state == "insufficient_evidence"

    def test_a_streamed_question_answers_on_the_same_terms(self):
        """`/query/stream` and `/query` must not disagree about whether an
        answer survives."""
        generator = _RecordingGenerator([])
        generator.stream = lambda question, context_block: iter(
            [f'{{"claims": [{self.CLAIM.model_dump_json()}]}}']
        )
        service = _service(generator, label="entailment", score=0.489)

        events = dict(service.stream("what is pipelining"))

        assert events["verification"]["state"] == "ok"
        assert "Pipelining executes" in events["verification"]["answer"]


def test_the_fact_route_keeps_the_evidence_trail():
    response = _service(_ExplodingGenerator()).run("RISC has instruction pipelining")

    assert response.retrieved_chunk_ids == ["1:0", "1:7"]
    assert response.claims[0].chunk_ids == ["1:0", "1:7"]
    assert response.claims[0].evidence_score == pytest.approx(0.99)


def test_the_fact_route_never_appends_every_citation_tag_to_the_answer():
    """The statement is scored against every retrieved chunk, so its citation
    list is "what was considered". Rendering all of it as `[C1][C2]` on the
    answer would assert corroboration that was never established."""
    response = _service(_ExplodingGenerator()).run("RISC has instruction pipelining")

    assert "[C1]" not in response.answer
    assert "[C2]" not in response.answer


def test_an_injected_classifier_overrides_the_heuristic():
    """The seam an LLM-backed classifier will arrive through: swapping it must
    change routing without touching the service."""
    generator = _RecordingGenerator([])
    service = QueryService(
        retriever=_StubRetriever(),
        generator=generator,
        verifier=ClaimVerifier(scorer=lambda premise, hypothesis: ("entailment", 0.9)),
        # The heuristic would call this a fact; the injected classifier does not.
        classifier=lambda text: ClassifiedInput(input_type="question", text=text),
        top_k=2,
    )

    response = service.run("RISC has instruction pipelining")

    assert response.input_type == "question"
    assert len(generator.calls) == 1


def test_a_plain_function_satisfies_the_classifier_port():
    """`InputClassifier` is a callable protocol, so `classify_input` is an
    implementation with no wrapper class — the property that lets a test pass
    a lambda."""
    from app.domain.ports import InputClassifier

    def _stub(text: str) -> ClassifiedInput:
        return ClassifiedInput(input_type="fact", text=text)

    classifier: InputClassifier = _stub
    assert classifier("x").input_type == "fact"


# --- Streaming ---------------------------------------------------------------


def test_streaming_routes_a_fact_with_no_token_events():
    events = list(_service(_ExplodingGenerator()).stream("RISC has instruction pipelining"))

    assert [name for name, _ in events] == ["retrieval", "verification"]
    assert events[-1][1]["input_type"] == "fact"
    assert events[-1][1]["state"] == "ok"


def test_streaming_still_generates_for_a_question():
    generator = _RecordingGenerator([])
    events = list(_service(generator).stream("what is RISC?"))

    assert [name for name, _ in events] == ["retrieval", "token", "verification"]
    assert len(generator.calls) == 1
    assert events[-1][1]["input_type"] == "question"


# --- The wire contract -------------------------------------------------------


def test_input_type_defaults_to_question_for_a_pre_routing_payload():
    """An older cached or hand-built response deserializes to what it always
    meant, not to a guess."""
    from app.domain.models import FactCheckedResponse

    response = FactCheckedResponse(
        question="q", answer="a", state="ok", claims=[], retrieved_chunk_ids=[], latency_ms={}
    )

    assert response.input_type == "question"


def test_the_fact_response_round_trips_with_its_computed_fields():
    """A1's contract rule, on the route that was added last."""
    from app.domain.models import FactCheckedResponse

    model = _service(_ExplodingGenerator()).run("RISC has instruction pipelining")

    assert FactCheckedResponse(**model.model_dump()) == model
