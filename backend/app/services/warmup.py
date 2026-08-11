"""Loading the three models once, at startup (§10).

From the old `app/deps.py`. §10 requires the embedding, reranker, and NLI
models to load once at startup via a lifespan event, not per request.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_preload_thread: threading.Thread | None = None


def _warm_embedding(model) -> None:
    model.encode("warmup", normalize_embeddings=True)


def _warm_reranker(model) -> None:
    model.predict([("warmup query", "warmup passage")])


def _warm_nli(model) -> None:
    model.predict([("warmup premise", "warmup hypothesis")], apply_softmax=True)


def _load_and_warm(name: str, loader, warmer) -> None:
    """Load one model and put a single forward pass through it.

    The forward pass is not redundant with the load. Constructing the model
    reads the weights; the first `encode`/`predict` is what builds the tokenizer
    state and torch's execution graph, and it is measurably expensive — the
    embedding model's first call cost 2.8s against 35ms for every call after it,
    and that 2.8s was landing on the first user query as `retrieval_ms`. Paying
    it here moves it off the request that would otherwise wear it.

    Inputs are fixed nonsense strings on purpose: nothing here should depend on
    the corpus, and the output is discarded.
    """
    try:
        model = loader()
        logger.info("Preloaded %s model", name)
    except Exception:
        # Never fatal. Each loader is a cached singleton, so a failure here
        # (usually a cold cache with no network) just means the first
        # request that needs the model pays the load cost and surfaces the
        # real error itself.
        logger.exception("Failed to preload %s model", name)
        return

    try:
        warmer(model)
        logger.info("Warmed %s model", name)
    except Exception:
        # Same reasoning, one step later: the model is loaded and usable, so a
        # failed warm-up costs latency on the first request and nothing else.
        # Downgrading this to a warning would be wrong — a forward pass that
        # raises is worth a stack trace even when it is not fatal.
        logger.exception("Failed to warm %s model", name)


def _load_all_models() -> None:
    """Load and warm the three models concurrently.

    Serially this was ~18s cold; on three threads it is ~9s, and with the
    local-cache-first loading policy in `app.infrastructure.ml.loading` the
    whole thing is ~3s. Threads rather than processes for the reason the module
    docstring gives: the cost is file I/O and torch's C extensions, both of
    which release the GIL. Each loader holds its own lock, so the three run in
    parallel while a request arriving mid-warmup still blocks on the model it
    needs instead of loading a second copy of it.
    """
    from app.infrastructure.ml.embeddings import get_embedding_model, get_reranker
    from app.infrastructure.ml.nli import get_nli_model

    jobs = (
        ("embedding", get_embedding_model, _warm_embedding),
        ("reranker", get_reranker, _warm_reranker),
        ("nli", get_nli_model, _warm_nli),
    )

    threads = [
        threading.Thread(target=_load_and_warm, args=job, name=f"preload-{job[0]}", daemon=True)
        for job in jobs
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def preload_models(block: bool = False) -> threading.Thread:
    """Warm the three model singletons once, at startup.

    Runs on a background thread by default. Loading these serially on the
    startup path takes tens of seconds cold — and on a first run, downloads
    roughly a gigabyte of weights — during which uvicorn would not yet be
    serving. `/health` would then fail its container healthcheck for
    reasons that have nothing to do with whether the datastores are up,
    which is the opposite of what §10's startup gating is for.

    Threading is sufficient here despite the GIL: the cost is model file
    I/O and torch's own C-extension work, both of which release it. A
    request arriving mid-warmup simply blocks on the same cache entry it
    would have populated itself, so correctness does not depend on the
    warmup having finished.

    `block=True` waits for the load to finish — for tests and CLI callers
    that want the cost paid up front.
    """
    global _preload_thread

    if _preload_thread is not None and _preload_thread.is_alive():
        if block:
            _preload_thread.join()
        return _preload_thread

    thread = threading.Thread(target=_load_all_models, name="model-preload", daemon=True)
    thread.start()
    _preload_thread = thread

    if block:
        thread.join()

    return thread
