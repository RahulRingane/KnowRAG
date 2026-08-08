"""§9 — structured logging, trace IDs, and the six metrics.

The property that matters most here is the one A8's acceptance criteria
state last and that is easiest to break: **instrumentation must be
additive**. A verdict counter that swallows an exception, a log call that
mutates a list, or a timer that changes a return value would all make the
eval numbers depend on whether logging was enabled — so the observability
layer is tested for having no effect as carefully as it is tested for
recording one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app import main
from app.core import observability
from app.domain.models import ClaimVerdict

client = TestClient(main.app)


def _verdict(status_, text="c"):
    return ClaimVerdict(
        text=text, status=status_, citations=["C1"], evidence_score=0.9, chunk_ids=["1:0"]
    )


def _counter(name: str) -> float:
    return REGISTRY.get_sample_value(name) or 0.0


# --- §9's six metrics --------------------------------------------------------


def test_all_six_specified_metrics_are_exported():
    body = client.get("/metrics").text

    for metric in (
        "retrieval_latency_seconds",
        "generation_latency_seconds",
        "verification_latency_seconds",
        "claims_supported_total",
        "claims_unsupported_total",
        "claims_contradicted_total",
    ):
        assert metric in body, f"{metric} missing from /metrics"


def test_metrics_endpoint_uses_the_prometheus_content_type():
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_verdict_counters_increment_per_status():
    before = {
        s: _counter(f"claims_{s.lower()}_total")
        for s in ("supported", "unsupported", "contradicted")
    }

    observability.record_verdicts(
        [_verdict("SUPPORTED"), _verdict("SUPPORTED"), _verdict("UNSUPPORTED"), _verdict("CONTRADICTED")]
    )

    assert _counter("claims_supported_total") == before["supported"] + 2
    assert _counter("claims_unsupported_total") == before["unsupported"] + 1
    assert _counter("claims_contradicted_total") == before["contradicted"] + 1


def test_record_verdicts_returns_the_distribution_without_mutating_input():
    verdicts = [_verdict("SUPPORTED"), _verdict("UNSUPPORTED")]
    snapshot = [v.model_copy(deep=True) for v in verdicts]

    distribution = observability.record_verdicts(verdicts)

    assert distribution == {"SUPPORTED": 1, "UNSUPPORTED": 1}
    assert verdicts == snapshot, "instrumentation must not alter verdicts"


def test_unknown_status_does_not_raise():
    """A future verdict status must degrade to "uncounted", never to a 500
    in the middle of a request that had already produced a valid answer."""
    fake = type("V", (), {"status": "MYSTERY"})()

    assert observability.record_verdicts([fake]) == {"MYSTERY": 1}


# --- Timing ------------------------------------------------------------------


def test_observe_records_latency():
    before = REGISTRY.get_sample_value(
        "verification_latency_seconds_count", {}
    ) or 0.0

    with observability.observe(observability.verification_latency_seconds):
        pass

    after = REGISTRY.get_sample_value("verification_latency_seconds_count", {}) or 0.0
    assert after == before + 1


def test_observe_records_even_when_the_block_raises():
    """The slow failure is the observation an operator most needs."""
    before = REGISTRY.get_sample_value("generation_latency_seconds_count", {}) or 0.0

    with pytest.raises(RuntimeError):
        with observability.observe(observability.generation_latency_seconds):
            raise RuntimeError("429")

    after = REGISTRY.get_sample_value("generation_latency_seconds_count", {}) or 0.0
    assert after == before + 1


def test_observe_does_not_swallow_the_exception():
    with pytest.raises(ValueError, match="propagated"):
        with observability.observe(observability.verification_latency_seconds):
            raise ValueError("propagated")


def test_retrieval_latency_is_labelled_per_path():
    for path in ("semantic", "keyword", "hybrid"):
        with observability.observe(observability.retrieval_latency_seconds, path=path):
            pass

    body = client.get("/metrics").text
    for path in ("semantic", "keyword", "hybrid"):
        assert f'path="{path}"' in body


# --- Trace IDs ---------------------------------------------------------------


def test_response_carries_a_trace_id():
    response = client.get("/")

    assert response.headers.get(observability.TRACE_ID_HEADER)


def test_inbound_trace_id_is_honoured():
    """Lets a KnowRAG trace be correlated with the caller's own logs."""
    response = client.get("/", headers={observability.TRACE_ID_HEADER: "abc123"})

    assert response.headers[observability.TRACE_ID_HEADER] == "abc123"


def test_each_request_gets_a_distinct_trace_id():
    first = client.get("/").headers[observability.TRACE_ID_HEADER]
    second = client.get("/").headers[observability.TRACE_ID_HEADER]

    assert first != second


def test_trace_id_reaches_log_events_from_pipeline_modules(capsys):
    """§9's actual requirement: a bad answer must be traceable to the chunks
    retrieved for it, which only works if the ID is on *those* log lines."""
    observability.configure_logging()
    observability.clear_trace_context()
    observability.bind_trace_id("trace-xyz")

    observability.get_logger("app.retriver").info("semantic_search", chunk_ids=["1:7"])

    captured = capsys.readouterr()
    assert "trace-xyz" in captured.out + captured.err
    assert "1:7" in captured.out + captured.err

    observability.clear_trace_context()


def test_log_output_is_single_encoded_json(capsys):
    """Guards a double-render that reads fine to a human and is unparseable
    to a log pipeline: structlog renders the event to a JSON string, stdlib
    treats it as the message, and ProcessorFormatter renders it again —
    nesting the entire event inside the outer `event` field."""
    import json

    observability.configure_logging()
    observability.clear_trace_context()
    observability.bind_trace_id("trace-parse")

    observability.get_logger("app.retriver").info("hybrid_search", returned=5)

    line = (capsys.readouterr().err or capsys.readouterr().out).strip().splitlines()[-1]
    record = json.loads(line)

    assert record["event"] == "hybrid_search", f"event was re-encoded: {record['event']!r}"
    assert record["returned"] == 5, "structured keys must stay top-level, not nested in a string"
    assert record["trace_id"] == "trace-parse"

    observability.clear_trace_context()


def test_third_party_logs_also_carry_the_trace_id(capsys):
    """A Qdrant or SQLAlchemy failure during a traced request must stay
    correlated — losing it exactly when a datastore breaks defeats §9."""
    import logging as stdlib_logging

    observability.configure_logging()
    observability.clear_trace_context()
    observability.bind_trace_id("trace-thirdparty")

    stdlib_logging.getLogger("qdrant_client").warning("connection reset")

    captured = capsys.readouterr()
    assert "trace-thirdparty" in captured.out + captured.err

    observability.clear_trace_context()


def test_trace_context_is_cleared_between_requests():
    observability.bind_trace_id("leaky")
    observability.clear_trace_context()

    import structlog

    assert "trace_id" not in structlog.contextvars.get_contextvars()
