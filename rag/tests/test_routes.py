"""§7 route contracts, with every datastore mocked.

`TestClient` is used *without* its context manager on purpose. Entering the
context runs the app's lifespan, which creates tables and preloads three
transformer models — neither of which belongs in an offline suite §8.1
requires to finish in under 30 seconds. Routes are exercised directly
instead, which is what these tests are actually about.

The contract worth protecting here is §7.1's: `POST /ingest` returns
`202 Accepted` **and a `document_id`** before any work has run. That shape
is what makes the eventual swap from `BackgroundTasks` to a real queue
invisible to callers, so it is asserted explicitly rather than assumed.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api.dependencies import get_health_service, get_ingestion_service, get_query_service
from app.core.exceptions import DocumentNotFound, GenerationUnavailable
from app.domain.models import ClaimVerdict, DocumentRecord, FactCheckedResponse
from app.services.health_service import HealthService

client = TestClient(main.app)


# --- Substituting the service layer ------------------------------------------
#
# Routes receive their collaborators through `Depends()`, so a test replaces a
# whole service with `app.dependency_overrides` instead of monkeypatching the
# module a route happens to import. That is the difference the service layer
# buys: these tests state what the route should do with a given service
# result, and never mention Postgres, Qdrant, or a provider SDK.


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    main.app.dependency_overrides.clear()


def _use(dependency, service):
    main.app.dependency_overrides[dependency] = lambda: service
    return service


class _StubIngestion:
    """Records what the route asked for; returns canned results."""

    def __init__(self, document_id: int = 77, record: DocumentRecord | None = None):
        self.document_id = document_id
        self.record = record
        self.calls: dict = {}

    def reserve(self, filename):
        self.calls["filename"] = filename
        return self.document_id

    def ingest(self, path, document_id=None, force=False, full_reindex=False):
        self.calls["path"] = path
        self.calls["document_id"] = document_id
        return None

    def get_status(self, document_id):
        if self.record is None:
            raise DocumentNotFound(document_id)
        return self.record


class _StubQuery:
    """A query service that returns, or raises, whatever the test says."""

    def __init__(self, run=None, stream=None):
        self._run = run
        self._stream = stream

    def run(self, question, k=None):
        return self._run(question)

    def stream(self, question, k=None):
        return self._stream(question)


# --- /health -----------------------------------------------------------------


def _health(**probes):
    return _use(get_health_service, HealthService(probes))


def test_health_reports_every_datastore_when_all_are_up():
    _health(
        postgres=lambda: (True, None),
        qdrant=lambda: (True, None),
        elasticsearch=lambda: (True, None),
    )

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"postgres", "qdrant", "elasticsearch"}
    assert all(c["status"] == "ok" for c in body["checks"].values())


def test_health_returns_503_and_names_the_broken_dependency():
    _health(
        postgres=lambda: (True, None),
        qdrant=lambda: (True, None),
        elasticsearch=lambda: (False, "ping returned False"),
    )

    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["elasticsearch"]["detail"] == "ping returned False"
    # The healthy ones are still reported: a health endpoint that stops at
    # the first failure hides how much is actually broken.
    assert body["checks"]["postgres"]["status"] == "ok"


def test_health_probe_failure_does_not_take_down_the_endpoint(monkeypatch):
    """The *real* Elasticsearch probe, with its client unable to connect.

    Uses the production probe rather than a stub returning `(False, ...)`,
    because the property under test is that the probe swallows its own
    exception — a stub that already returns a tuple cannot demonstrate that.
    """

    def _explode():
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.infrastructure.search.clients.get_es_client", _explode)

    from app.infrastructure.search.clients import check_elasticsearch

    _health(
        postgres=lambda: (True, None),
        qdrant=lambda: (True, None),
        elasticsearch=check_elasticsearch,
    )

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["checks"]["elasticsearch"]["status"] == "error"


# --- POST /ingest ------------------------------------------------------------


@pytest.fixture
def captured_ingest():
    """Substitute the ingestion service; record what the route handed it."""
    return _use(get_ingestion_service, _StubIngestion(document_id=77)).calls


def test_ingest_returns_202_with_a_document_id(captured_ingest):
    response = client.post(
        "/ingest",
        files={"file": ("data.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["document_id"] == 77
    assert body["filename"] == "data.pdf"
    assert body["status"] == "pending"


def test_ingest_runs_the_shared_pipeline_with_the_reserved_id(captured_ingest):
    client.post(
        "/ingest",
        files={"file": ("data.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )

    assert captured_ingest["document_id"] == 77


def test_upload_is_staged_under_its_original_basename(captured_ingest):
    """Ingestion derives document identity from the filename, so a randomized
    temp name would create a second, orphaned documents row."""
    client.post(
        "/ingest",
        files={"file": ("data.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )

    assert captured_ingest["path"].endswith("/data.pdf")


def test_directory_components_are_stripped_from_the_upload_name(captured_ingest):
    client.post(
        "/ingest",
        files={"file": ("../../etc/passwd.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
    )

    assert captured_ingest["path"].endswith("/passwd.pdf")
    assert ".." not in captured_ingest["path"]


def test_non_pdf_upload_is_rejected(captured_ingest):
    response = client.post(
        "/ingest",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )

    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]
    assert captured_ingest == {}, "rejected upload must not reach the pipeline"


# --- GET /ingest/{document_id} ----------------------------------------------


@pytest.mark.parametrize("state", ["pending", "indexed", "failed"])
def test_ingest_status_reports_each_state(state):
    _use(
        get_ingestion_service,
        _StubIngestion(
            record=DocumentRecord(
                document_id=1,
                filename="data.pdf",
                status=state,
                chunk_count=29,
                error="boom" if state == "failed" else None,
            )
        ),
    )

    response = client.get("/ingest/1")

    assert response.status_code == 200
    assert response.json()["status"] == state


def test_failed_ingest_surfaces_its_reason():
    _use(
        get_ingestion_service,
        _StubIngestion(
            record=DocumentRecord(
                document_id=1,
                filename="data.pdf",
                status="failed",
                chunk_count=0,
                error="Qdrant unreachable",
            )
        ),
    )

    assert client.get("/ingest/1").json()["error"] == "Qdrant unreachable"


def test_ingest_status_does_not_leak_internal_bookkeeping():
    """`DocumentRecord` carries `content_hash`; the wire shape must not."""
    _use(
        get_ingestion_service,
        _StubIngestion(
            record=DocumentRecord(
                document_id=1,
                filename="data.pdf",
                status="indexed",
                chunk_count=29,
                content_hash="deadbeef" * 8,
            )
        ),
    )

    assert "content_hash" not in client.get("/ingest/1").json()


def test_unknown_document_is_404():
    _use(get_ingestion_service, _StubIngestion(record=None))

    assert client.get("/ingest/9999").status_code == 404


# --- POST /query -------------------------------------------------------------


def _response(state, claims, input_type="question"):
    return FactCheckedResponse(
        input_type=input_type,
        question="q",
        answer="a" if state == "ok" else "Insufficient evidence.",
        state=state,
        claims=claims,
        retrieved_chunk_ids=["1:0"],
        latency_ms={"retrieval_ms": 1.0},
    )


def _verdict(status_, text="claim", reason=None):
    return ClaimVerdict(
        text=text, status=status_, citations=["C1"], evidence_score=0.9,
        chunk_ids=["1:0"], reason=reason,
    )


def test_query_returns_the_fact_checked_contract():
    _use(get_query_service, _StubQuery(run=lambda q: _response("ok", [_verdict("SUPPORTED")])))

    response = client.post("/query", json={"question": "what is X?"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {
        "input_type", "question", "answer", "state", "claims",
        "retrieved_chunk_ids", "latency_ms", "rejected_claims",
    }


def test_the_response_reports_which_route_produced_it():
    """Classification and routing happen inside `QueryService`, so the route
    body has nothing to branch on — but the caller must still be able to tell
    an answer to a question from a verdict on a statement."""
    verdict = _verdict("SUPPORTED", "RISC has instruction pipelining")
    _use(get_query_service, _StubQuery(run=lambda q: _response("ok", [verdict], "fact")))

    body = client.post("/query", json={"question": "RISC has instruction pipelining"}).json()

    assert body["input_type"] == "fact"
    assert len(body["claims"]) == 1


def test_a_contradicted_statement_is_a_200_with_its_own_state():
    """The corpus refuting a caller's statement is a finding, not an error —
    the same reasoning that makes `insufficient_evidence` a 200."""
    verdict = _verdict("CONTRADICTED", "RISC has no pipelining", "cited chunk [C1] contradicts")
    _use(get_query_service, _StubQuery(run=lambda q: _response("contradicted", [verdict], "fact")))

    response = client.post("/query", json={"question": "RISC has no pipelining"})

    assert response.status_code == 200
    assert response.json()["state"] == "contradicted"


def test_unanswerable_question_is_insufficient_evidence_with_reasons():
    """§6.3 / A6b's single most important acceptance test: the system must
    refuse rather than answer, and must say what it threw away and why."""
    rejected = _verdict("UNSUPPORTED", "The capital of France is Paris.", "no cited chunk entails this claim")
    _use(get_query_service, _StubQuery(run=lambda q: _response("insufficient_evidence", [rejected])))

    body = client.post("/query", json={"question": "capital of France?"}).json()

    assert body["state"] == "insufficient_evidence"
    assert body["answer"], "must never be an empty string — callers cannot distinguish that from a crash"
    assert len(body["rejected_claims"]) == 1
    assert body["rejected_claims"][0]["reason"] == "no cited chunk entails this claim"


def test_rejected_claims_is_derived_from_claims_not_stored_twice():
    claims = [_verdict("SUPPORTED"), _verdict("UNSUPPORTED"), _verdict("CONTRADICTED")]
    _use(get_query_service, _StubQuery(run=lambda q: _response("ok", claims)))

    body = client.post("/query", json={"question": "q"}).json()

    assert len(body["claims"]) == 3
    assert [c["status"] for c in body["rejected_claims"]] == ["UNSUPPORTED", "CONTRADICTED"]


def test_response_still_round_trips_with_the_computed_field():
    """A1's contract rule: Model(**m.model_dump()) == m, computed field included."""
    model = _response("ok", [_verdict("SUPPORTED")])

    assert FactCheckedResponse(**model.model_dump()) == model


def test_empty_question_is_rejected():
    assert client.post("/query", json={"question": ""}).status_code == 422
    assert client.post("/query", json={}).status_code == 422


def test_quota_exhaustion_is_503_not_a_fake_insufficient_evidence():
    """"The generator never ran" and "the evidence does not support this"
    are different answers, and conflating them would have this system
    report a confident refusal it did not actually derive."""

    def _boom(question):
        raise GenerationUnavailable("Generation quota exhausted (daily quota).", daily_quota=True)

    _use(get_query_service, _StubQuery(run=_boom))

    response = client.post("/query", json={"question": "q"})

    assert response.status_code == 503
    assert "quota" in response.json()["detail"].lower()


def test_rate_limited_response_advertises_retry_after():
    def _boom(question):
        raise GenerationUnavailable("rate limited", retry_after=42.5)

    _use(get_query_service, _StubQuery(run=_boom))

    response = client.post("/query", json={"question": "q"})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "43"


def test_daily_quota_sends_no_retry_after():
    """The provider advertises ~50s for a daily cap whose real window is a
    day; echoing that would just invite a client to hammer it."""

    def _boom(question):
        raise GenerationUnavailable("daily", daily_quota=True, retry_after=None)

    _use(get_query_service, _StubQuery(run=_boom))

    assert "retry-after" not in client.post("/query", json={"question": "q"}).headers


# --- POST /query/stream ------------------------------------------------------


def _events(raw: str) -> list[tuple[str, dict]]:
    out = []
    for frame in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_stream_emits_verification_after_all_tokens():
    final = _response("ok", [_verdict("SUPPORTED")]).model_dump()

    def _stream(question):
        yield "retrieval", {"retrieved_chunk_ids": ["1:0"]}
        yield "token", {"text": '{"claims":'}
        yield "token", {"text": "[]}"}
        yield "verification", final

    _use(get_query_service, _StubQuery(stream=_stream))

    response = client.post("/query/stream", json={"question": "q"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    names = [name for name, _ in _events(response.text)]
    assert names == ["retrieval", "token", "token", "verification", "done"]
    # Verification must be terminal: nothing before it has been fact-checked.
    assert names.index("verification") > max(i for i, n in enumerate(names) if n == "token")


def test_stream_reports_a_mid_stream_failure_as_an_error_event():
    def _stream(question):
        yield "retrieval", {"retrieved_chunk_ids": []}
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    _use(get_query_service, _StubQuery(stream=_stream))

    events = _events(client.post("/query/stream", json={"question": "q"}).text)

    assert events[-1][0] == "error"
    assert "429" in events[-1][1]["detail"]
    # No "done" — the client must be able to tell truncation from completion.
    assert "done" not in [n for n, _ in events]
