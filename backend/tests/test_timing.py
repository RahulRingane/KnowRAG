"""The per-stage latency breakdown, and the one invariant it rests on.

`latency_ms` grew from four coarse stages into a breakdown that names the two
costs that actually dominated a cold query — the cross-encoder rerank and
loading a transformer. Both are incurred inside `app.infrastructure`, below the
service that reports them, and both sit *within* an existing stage. That makes
one property load-bearing:

    **every key in `latency_ms` is a disjoint span.**

`app.cli.query` has printed `sum(latency_ms.values())` as `took` since before
any of this existed, and the eval harness reads the same field. A key that
overlaps another silently inflates that total, and the failure is invisible —
the numbers still look like plausible seconds.

This is not hypothetical. The first cut of the instrumentation timed the
reranker's *load* inside the `rerank_ms` span while the loader also reported it
as `model_load_ms`, so a 6.8s load was subtracted from `retrieval_ms` twice and
a 24s query reported itself as 38.9s. Everything here is offline: the stubs
below simulate a model load by sleeping and recording, so the invariant is
tested without loading 400MB of weights.
"""

from __future__ import annotations

import time

import pytest

from app.core import timing
from app.domain.models import Chunk, Claim
from app.domain.verification import ClaimVerifier
from app.services.query_service import QueryService

# A stage has to be slow enough to clear the CLI's 50ms display floor and to
# survive scheduler jitter, but every test here pays it. 60ms is the compromise.
SLOW_MS = 60
SLOW = SLOW_MS / 1000


def _chunk(idx: int) -> Chunk:
    return Chunk(
        source="qdrant", score=1.0, document_id=1, chunk_index=idx, text=f"chunk {idx} text"
    )


class _SlowRetriever:
    """Retrieves, and reports a rerank and a model load the way the real one does.

    Mirrors `HybridRetriever`: the reranker load is recorded *outside* the
    `rerank_ms` span, which is precisely the arrangement the disjointness
    invariant depends on.
    """

    def __init__(self, load_ms: float = SLOW_MS, rerank_ms: float = SLOW_MS):
        self._load_ms = load_ms
        self._rerank_ms = rerank_ms

    def search(self, query: str, k: int = 0) -> list[Chunk]:
        if self._load_ms:
            with timing.timed("model_load_ms"):
                time.sleep(self._load_ms / 1000)
        with timing.timed("rerank_ms"):
            time.sleep(self._rerank_ms / 1000)
        return [_chunk(0), _chunk(1)]


class _SlowGenerator:
    def generate(self, question, context_block, chunk_ids=None):
        time.sleep(SLOW)
        return [Claim(kind="assertion", text="A claim.", citations=["C1"])]

    def stream(self, question, context_block):
        yield '{"claims": []}'


def _service(retriever=None, scorer=None) -> QueryService:
    return QueryService(
        retriever=retriever or _SlowRetriever(),
        generator=_SlowGenerator(),
        verifier=ClaimVerifier(
            scorer=scorer or (lambda premise, hypothesis: ("entailment", 0.99)),
            threshold=0.55,
        ),
        top_k=2,
    )


# --- the invariant ----------------------------------------------------------


@pytest.mark.parametrize("text", ["what is RISC?", "RISC has instruction pipelining"])
def test_stage_timings_sum_to_the_wall_time_of_the_call(text):
    """Both routes. This is what makes the CLI's `took` line true.

    Bounded on both sides deliberately: an upper bound alone passes a
    breakdown that lost a stage entirely, and a lower bound alone passes one
    that counts a nested cost twice — which is the bug that actually happened.
    """
    service = _service()

    started = time.perf_counter()
    response = service.run(text)
    wall_ms = (time.perf_counter() - started) * 1000

    total = sum(response.latency_ms.values())

    assert total <= wall_ms + 20, (
        f"stages sum to {total:.0f}ms but the call took {wall_ms:.0f}ms — "
        f"a nested cost is being counted under two keys: {response.latency_ms}"
    )
    assert total >= wall_ms * 0.8, (
        f"stages sum to {total:.0f}ms of a {wall_ms:.0f}ms call — "
        f"time is going somewhere unattributed: {response.latency_ms}"
    )


def test_a_model_load_is_reported_separately_not_as_slow_retrieval():
    """The finding this instrumentation exists for.

    A cold process spent ~18s loading three transformers and reported it as
    retrieval and verification, so the breakdown pointed at the datastores and
    the NLI model rather than at the load.
    """
    response = _service(_SlowRetriever(load_ms=200, rerank_ms=0)).run("what is RISC?")

    assert response.latency_ms["model_load_ms"] >= 190
    # The load was subtracted back out, so retrieval reads as the search it is.
    assert response.latency_ms["retrieval_ms"] < 100


def test_rerank_is_reported_separately_from_retrieval():
    response = _service(_SlowRetriever(load_ms=0, rerank_ms=200)).run("what is RISC?")

    assert response.latency_ms["rerank_ms"] >= 190
    assert response.latency_ms["retrieval_ms"] < 100


def test_a_warm_process_reports_no_model_load():
    """`model_load_ms` means cold-start cost specifically.

    The real loaders are `lru_cache`d and time only the branch that actually
    loads, so a warm process must not carry the key at all — a `0.0` would say
    a load happened instantly.
    """
    response = _service(_SlowRetriever(load_ms=0)).run("what is RISC?")

    assert "model_load_ms" not in response.latency_ms


def test_stage_timings_do_not_leak_between_calls():
    """Each route opens its own `timing.collect()` scope.

    A module-level accumulator would make the second query in a process report
    the first one's model load, which is exactly backwards — the second query
    is the one that is fast.
    """
    service = _service(_SlowRetriever(load_ms=200, rerank_ms=0))

    first = service.run("what is RISC?")
    second = service.run("what is CISC?")

    assert first.latency_ms["model_load_ms"] >= 190
    # The stub reports a load every call, so this is testing isolation, not
    # caching: the second call must see its own 200ms and not 400ms.
    assert second.latency_ms["model_load_ms"] < 300


# --- the collector itself ---------------------------------------------------


def test_recording_outside_a_scope_is_a_no_op():
    """Instrumentation that can raise is worse than instrumentation that is
    absent. A bare `HybridRetriever` in a unit test opens no scope."""
    timing.record("model_load_ms", 5.0)
    with timing.timed("rerank_ms"):
        pass

    assert timing.snapshot() == {}


def test_costs_accumulate_rather_than_overwrite():
    """Three model loads in one request are one `model_load_ms` figure."""
    with timing.collect() as costs:
        timing.record("model_load_ms", 10.0)
        timing.record("model_load_ms", 5.0)

        assert costs["model_load_ms"] == 15.0


def test_snapshot_is_a_copy():
    """The service diffs two snapshots to attribute a nested cost; a live view
    would make both sides of that subtraction the same object."""
    with timing.collect():
        timing.record("rerank_ms", 1.0)
        before = timing.snapshot()
        timing.record("rerank_ms", 1.0)

        assert before["rerank_ms"] == 1.0
        assert timing.snapshot()["rerank_ms"] == 2.0


# --- the loading policy -----------------------------------------------------


def test_a_cached_model_loads_without_touching_the_network():
    """The optimization itself.

    `SentenceTransformer(name)` revalidates already-cached files against
    huggingface.co on every construction — 4-7s per model here, ~15s of an 18s
    cold load. `local_files_only=True` skips it for identical weights.
    """
    from app.infrastructure.ml.loading import from_cache_first

    seen: list[dict] = []

    def constructor(name, **kwargs):
        seen.append(kwargs)
        return f"model:{name}"

    assert from_cache_first(constructor, "some/model") == "model:some/model"
    assert seen == [{"local_files_only": True}], "the cached load must be tried first"


def test_an_uncached_model_still_downloads():
    """§10 keeps weights out of the image, so a first run *must* be able to
    fetch them. Skipping the network entirely would trade a slow first query
    for a broken one."""
    from app.infrastructure.ml.loading import from_cache_first

    seen: list[dict] = []

    def constructor(name, **kwargs):
        seen.append(kwargs)
        if kwargs.get("local_files_only"):
            raise OSError("not in the local cache")
        return f"downloaded:{name}"

    assert from_cache_first(constructor, "some/model") == "downloaded:some/model"
    assert seen == [{"local_files_only": True}, {}]


def test_a_real_load_failure_still_raises_its_own_error():
    """The fallback must not mask a genuine fault as a cache miss — the second
    attempt's error is the one that reaches the caller."""
    from app.infrastructure.ml.loading import from_cache_first

    def constructor(name, **kwargs):
        if kwargs.get("local_files_only"):
            raise OSError("not in the local cache")
        raise RuntimeError("no such model on the hub")

    with pytest.raises(RuntimeError, match="no such model on the hub"):
        from_cache_first(constructor, "some/model")


def test_concurrent_callers_load_one_model_not_two():
    """`lru_cache` caches a result but holds no lock while producing one, so
    without the guard two threads racing an uncached loader each construct a
    ~400MB model. Latent while warmup was serial; reachable now that it is not.
    """
    import functools
    import threading

    from app.infrastructure.ml import embeddings

    loads: list[int] = []

    def fake_load():
        # Slow enough that four threads started back to back genuinely overlap
        # here. A barrier would be the tighter synchronisation, but the load it
        # has to synchronise on runs *inside* the lock, so only one thread would
        # ever reach it — the barrier deadlocks against the thing under test.
        time.sleep(0.1)
        loads.append(1)
        return "model"

    original_loader, original_lock = embeddings._load_embedding_model, embeddings._embedding_lock
    embeddings._load_embedding_model = functools.lru_cache(maxsize=1)(fake_load)
    embeddings._embedding_lock = threading.Lock()
    try:
        threads = [threading.Thread(target=embeddings.get_embedding_model) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        embeddings._load_embedding_model = original_loader
        embeddings._embedding_lock = original_lock

    assert len(loads) == 1, f"{len(loads)} concurrent loads of the same model"


# --- the CLI line -----------------------------------------------------------


def test_cli_timing_line_orders_stages_by_pipeline_not_by_dict_order():
    from app.cli.query import _format_timing

    line = _format_timing(
        {
            "verification_ms": 4900.0,
            "retrieval_ms": 1200.0,
            "model_load_ms": 15400.0,
            "generation_ms": 18100.0,
            "rerank_ms": 300.0,
        }
    )

    assert line == (
        "model_load 15.4s, retrieval 1.2s, rerank 0.3s, generation 18.1s, verification 4.9s"
    )


def test_cli_timing_line_collapses_negligible_stages():
    """Five rows reading `0.0s` bury the two that matter, but the time is real
    and is in `took`, so it is counted rather than dropped."""
    from app.cli.query import _format_timing

    line = _format_timing(
        {
            "retrieval_ms": 1200.0,
            "format_context_ms": 0.4,
            "generation_ms": 18100.0,
            "assembly_ms": 0.2,
        }
    )

    assert line == "retrieval 1.2s, generation 18.1s, 2 stage(s) <0.1s"


def test_cli_timing_line_keeps_stages_it_does_not_know_about():
    """A key added later must appear rather than vanish from the breakdown."""
    from app.cli.query import _format_timing

    line = _format_timing({"retrieval_ms": 1000.0, "future_stage_ms": 2000.0})

    assert line == "retrieval 1.0s, future_stage 2.0s"
