"""Loading the three models once, at startup (§10).

From the old `app/deps.py`. §10 requires the embedding, reranker, and NLI
models to load once at startup via a lifespan event, not per request.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_preload_thread: threading.Thread | None = None


def _load_all_models() -> None:
    from app.infrastructure.ml.embeddings import get_embedding_model, get_reranker
    from app.infrastructure.ml.nli import get_nli_model

    for name, loader in (
        ("embedding", get_embedding_model),
        ("reranker", get_reranker),
        ("nli", get_nli_model),
    ):
        try:
            loader()
            logger.info("Preloaded %s model", name)
        except Exception:
            # Never fatal. Each loader is a cached singleton, so a failure here
            # (usually a cold cache with no network) just means the first
            # request that needs the model pays the load cost and surfaces the
            # real error itself.
            logger.exception("Failed to preload %s model", name)


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
