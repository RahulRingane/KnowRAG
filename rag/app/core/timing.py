"""Per-request stage timing, collected across layers (§9).

`FactCheckedResponse.latency_ms` already reported the four coarse stages the
query service can time from the outside: retrieval, generation, verification,
assembly. That is not enough to answer "where did the 24 seconds go", because
the two costs that actually dominated were *inside* those stages and invisible
from the service:

- the cross-encoder rerank, which lives inside `retrieval_ms`, and
- loading a ~400MB transformer, which lands in whichever stage happened to
  touch an unloaded model first — so a cold process reported 15s of
  "retrieval" that was really 14s of model loading.

Both are incurred deep in `app.infrastructure`, several ports below the
service. Threading a timing dict down through `Retriever.search()` and
`EntailmentScorer.__call__` would put an observability concern into the
signature of every port — the same mistake §9's trace ID avoided, and for the
same reason. So this module does what `observability.bind_trace_id` does: a
`contextvars` accumulator that any layer can write to and the service reads
back, with no port learning that timing exists.

Everything here is **additive**, on the same terms as `app.core.observability`:
it records durations and never alters a value or a control path. Outside a
`collect()` scope every function here is a no-op, so a caller that never opens
one — a unit test, an eval script, a bare `HybridRetriever` — is unaffected.

`core` is a leaf and this module keeps it one: it imports nothing internal.
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager

# `None` rather than an empty dict as the default, so "no scope is open" is
# distinguishable from "a scope is open and nothing has been recorded yet".
# A module-level mutable default would also be shared by every context, which
# is exactly the leak this is meant to avoid.
_costs: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    "knowrag_stage_costs", default=None
)


@contextmanager
def collect():
    """Open a collection scope for one request; yield the dict it fills.

    Scopes nest and isolate the way any `contextvars` value does, so two
    concurrent requests in the same process each accumulate into their own
    dict. The dict is handed out by reference, and context variables propagate
    into the threadpool FastAPI runs sync routes in, so the blocking rerank and
    NLI work record into the scope their request opened.
    """
    costs: dict[str, float] = {}
    token = _costs.set(costs)
    try:
        yield costs
    finally:
        _costs.reset(token)


def record(name: str, milliseconds: float) -> None:
    """Add `milliseconds` to the running total for `name`.

    Accumulates rather than overwrites: three model loads in one request are
    one `model_load_ms` figure, and two calls into the reranker would be one
    `rerank_ms`. Silently does nothing outside a `collect()` scope — this is
    instrumentation, and instrumentation that can raise is worse than
    instrumentation that is absent.
    """
    costs = _costs.get()
    if costs is not None:
        costs[name] = costs.get(name, 0.0) + milliseconds


@contextmanager
def timed(name: str):
    """Time a block into `name`, recording even when it raises.

    Records on the failure path for the reason `observability.observe` does:
    the load that spent nine seconds and then failed is the one worth seeing.
    """
    started = time.perf_counter()
    try:
        yield
    finally:
        record(name, (time.perf_counter() - started) * 1000)


def snapshot() -> dict[str, float]:
    """A copy of what has been recorded so far in the current scope.

    Copied, not aliased: the query service diffs two snapshots to work out what
    a stage incurred, and a live view would make both sides of that subtraction
    the same object.
    """
    costs = _costs.get()
    return dict(costs) if costs is not None else {}
