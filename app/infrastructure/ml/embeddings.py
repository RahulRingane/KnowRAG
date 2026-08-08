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

from app.core.config import settings

if TYPE_CHECKING:  # import only for type checkers, never at runtime
    from sentence_transformers import CrossEncoder, SentenceTransformer


@lru_cache(maxsize=1)
def get_embedding_model() -> "SentenceTransformer":
    """Return the shared `SentenceTransformer` embedding model.

    Loaded once on first call and cached for the process lifetime (per
    §10 — models load once at startup/first-use, not per request).

    `sentence_transformers` is imported inside the function on purpose. At
    module scope it pulls in torch/transformers (~2.5s) for *any* importer,
    including CLI entrypoints and unit tests that never touch a model —
    §8.1 requires the test suite to run offline in under 30s. Deferring it
    here keeps importing the search package in the millisecond range.
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model)


@lru_cache(maxsize=1)
def get_reranker() -> "CrossEncoder":
    """Return the shared `CrossEncoder` reranker model.

    Loaded once on first call and cached for the process lifetime. See
    `get_embedding_model` for why the import is function-local.
    """
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings.reranker_model)
