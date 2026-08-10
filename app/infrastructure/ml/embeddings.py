"""Lazy, cached singleton loaders for the retrieval-time models.

From the old `app/models.py` (renamed because "models" now unambiguously
means `app.domain.models`, the data contracts — having both in one codebase
made every `from app import models` ambiguous at a glance).

Per KNowRAG_SPEC.md §10: these load once — at FastAPI startup via the warmup
path, or on first use if nothing has warmed them — never per request.
Importing this module must never trigger a download or a model load by
itself; only calling an accessor does.
"""

from functools import lru_cache
from typing import TYPE_CHECKING

from app.core import timing
from app.core.config import settings
from app.infrastructure.ml.loading import from_cache_first, guard

if TYPE_CHECKING:  # import only for type checkers, never at runtime
    from sentence_transformers import CrossEncoder, SentenceTransformer

# One lock per cached model — see `loading.guard`. `lru_cache` caches a result
# but does not hold a lock while producing one, so without these two threads
# racing an uncached loader both construct the model.
_embedding_lock = guard()
_reranker_lock = guard()


@lru_cache(maxsize=1)
def _load_embedding_model() -> "SentenceTransformer":
    from sentence_transformers import SentenceTransformer

    # Recorded because `lru_cache` hides the cost rather than removing it: the
    # first caller in a process pays the whole load, and in a CLI run that
    # caller is the query being timed. Cached calls record nothing measurable,
    # so `model_load_ms` reads as the cold-start cost specifically.
    with timing.timed("model_load_ms"):
        return from_cache_first(SentenceTransformer, settings.embedding_model)


def get_embedding_model() -> "SentenceTransformer":
    """Return the shared `SentenceTransformer` embedding model.

    Loaded once on first call and cached for the process lifetime (per
    §10 — models load once at startup/first-use, not per request).

    `sentence_transformers` is imported inside the function on purpose. At
    module scope it pulls in torch/transformers (~2.5s) for *any* importer,
    including CLI entrypoints and unit tests that never touch a model —
    §8.1 requires the test suite to run offline in under 30s. Deferring it
    here keeps importing the search package in the millisecond range.

    The lock is uncontended after the first call — a `lru_cache` hit behind an
    uncontended lock is tens of nanoseconds against a load measured in seconds,
    so this costs nothing on the path that runs per query.
    """
    with _embedding_lock:
        return _load_embedding_model()


@lru_cache(maxsize=1)
def _load_reranker() -> "CrossEncoder":
    from sentence_transformers import CrossEncoder

    with timing.timed("model_load_ms"):
        return from_cache_first(CrossEncoder, settings.reranker_model)


def get_reranker() -> "CrossEncoder":
    """Return the shared `CrossEncoder` reranker model.

    Loaded once on first call and cached for the process lifetime. See
    `get_embedding_model` for why the import is function-local and why the
    load is guarded.
    """
    with _reranker_lock:
        return _load_reranker()
