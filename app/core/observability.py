"""Structured logging, per-request trace IDs, and metrics (§9).

§9's stated purpose is the one that shapes everything here: *"so a bad
answer can be traced back to which chunks were retrieved and why a claim
was accepted or rejected."* That is an incident-response requirement, not a
dashboard requirement. It means the trace ID has to reach the log lines
emitted deep inside retrieval and verification, not just the request log.

Rather than thread a `trace_id` parameter through every function signature
in the pipeline — which would change the public API of modules that have
nothing to do with logging — the ID is bound to a `contextvar` via
`structlog.contextvars`. Every event logged during that request, from any
module, carries it automatically. Context variables propagate into the
threadpool FastAPI runs sync routes in, so the blocking retrieval, rerank
and NLI work all inherit it.

Everything in this module is **additive**: it records what happens and
never changes it. No function here alters a return value, a verdict, or
control flow, so re-running the eval with instrumentation in place must
produce identical numbers.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager

import structlog
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

TRACE_ID_HEADER = "x-request-id"


# --- Metrics (§9's six, named exactly as specified) --------------------------
#
# Latency buckets are set for this system's actual shape rather than the
# library defaults: local retrieval lands in the tens of milliseconds while
# a free-tier LLM call under backoff can take a minute or more, so one
# bucket set across all three stages would put every retrieval observation
# in the first bucket and every generation observation in +Inf.

_RETRIEVAL_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_GENERATION_BUCKETS = (0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
_VERIFICATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

retrieval_latency_seconds = Histogram(
    "retrieval_latency_seconds",
    "Time spent retrieving and reranking evidence chunks.",
    labelnames=("path",),  # semantic | keyword | hybrid
    buckets=_RETRIEVAL_BUCKETS,
)

generation_latency_seconds = Histogram(
    "generation_latency_seconds",
    "Time spent in the LLM generation call, including retry backoff.",
    buckets=_GENERATION_BUCKETS,
)

verification_latency_seconds = Histogram(
    "verification_latency_seconds",
    "Time spent scoring claims against cited evidence with the NLI model.",
    buckets=_VERIFICATION_BUCKETS,
)

claims_supported_total = Counter(
    "claims_supported_total", "Claims verified as supported by their cited evidence."
)
claims_unsupported_total = Counter(
    "claims_unsupported_total", "Claims rejected as unsupported by their cited evidence."
)
claims_contradicted_total = Counter(
    "claims_contradicted_total", "Claims contradicted by their cited evidence."
)

_VERDICT_COUNTERS = {
    "SUPPORTED": claims_supported_total,
    "UNSUPPORTED": claims_unsupported_total,
    "CONTRADICTED": claims_contradicted_total,
}


def render_metrics() -> tuple[bytes, str]:
    """Prometheus exposition payload and its content type."""
    return generate_latest(), CONTENT_TYPE_LATEST


def record_verdicts(verdicts) -> dict[str, int]:
    """Bump the per-status counters; return the distribution for logging."""
    distribution: dict[str, int] = {}
    for verdict in verdicts:
        counter = _VERDICT_COUNTERS.get(verdict.status)
        if counter is not None:
            counter.inc()
        distribution[verdict.status] = distribution.get(verdict.status, 0) + 1
    return distribution


@contextmanager
def observe(histogram, **labels):
    """Time a block into `histogram`, recording even when it raises.

    The failure path is the one worth measuring: a generation call that
    spends two minutes in backoff and then fails is exactly the latency an
    operator needs to see, and a naive timer that only records on success
    hides it.
    """
    started = time.perf_counter()
    try:
        yield
    finally:
        target = histogram.labels(**labels) if labels else histogram
        target.observe(time.perf_counter() - started)


# --- Trace IDs ---------------------------------------------------------------


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def bind_trace_id(trace_id: str) -> None:
    """Attach `trace_id` to every subsequent log event in this context."""
    structlog.contextvars.bind_contextvars(trace_id=trace_id)


def clear_trace_context() -> None:
    structlog.contextvars.clear_contextvars()


def get_logger(name: str):
    return structlog.get_logger(name)


class TraceIDMiddleware:
    """Pure ASGI middleware binding a trace ID for the life of a request.

    Deliberately not `BaseHTTPMiddleware`: that wraps the response in an
    anyio stream, which interferes with the long-lived generator behind
    `/query/stream` — the endpoint most likely to need tracing when
    something goes wrong. A raw ASGI wrapper leaves streaming untouched.

    An inbound `X-Request-ID` is honoured so a trace can be correlated with
    whatever called us; otherwise one is generated.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        trace_id = headers.get(TRACE_ID_HEADER) or new_trace_id()

        clear_trace_context()
        bind_trace_id(trace_id)

        async def send_with_trace(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append(
                    (TRACE_ID_HEADER.encode("latin-1"), trace_id.encode("latin-1"))
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            clear_trace_context()


# --- Logging configuration ---------------------------------------------------


def configure_logging(level: int = logging.INFO, json_logs: bool = True) -> None:
    """Configure structlog and route stdlib logging through it.

    Third-party libraries (sqlalchemy, elasticsearch, qdrant_client, httpx)
    log through the stdlib, so they are routed through the same processor
    chain. Otherwise a Qdrant error during a traced request would print
    without the trace ID — losing exactly the correlation §9 asks for at
    exactly the moment it matters.

    Idempotent: safe to call from both the FastAPI lifespan and a CLI
    entrypoint.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,  # injects trace_id
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    # structlog hands off to stdlib rather than rendering itself: the chain
    # ends at `wrap_for_formatter`, and `ProcessorFormatter` on the handler
    # does the single, final render.
    #
    # Ending this chain with the renderer instead is the obvious-looking
    # mistake and produces double-encoded output — structlog renders the
    # event to a JSON *string*, stdlib treats that string as the log
    # message, and ProcessorFormatter renders it again, nesting the whole
    # event inside the outer `event` field. It still contains everything,
    # so it reads fine to a human and is unparseable to anything else.
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )

    # Set on the handler as well as the root logger. `root.setLevel()` only
    # governs loggers that have not set a level of their own, and several
    # dependencies set one at import (huggingface_hub is the one that bites:
    # it pins itself to WARNING, so its records reach an unlevelled handler
    # and print even when the root is at ERROR). A level on the handler is
    # the only filter every record must pass.
    handler.setLevel(level)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # These are informative at DEBUG and pure noise at INFO — the ES client
    # logs one line per indexed document, which buries the pipeline events
    # this module exists to surface.
    for noisy in ("elastic_transport.transport", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
