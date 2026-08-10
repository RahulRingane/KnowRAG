"""How the three local models get loaded: from cache first, once each.

Two policies, shared by `embeddings.py` and `nli.py` because getting either
wrong is invisible — the model still loads and still returns correct scores,
it just costs seconds nobody can account for.

**Cache first.** `SentenceTransformer(name)` and `CrossEncoder(name)` contact
huggingface.co on every construction to revalidate files that are already in
the local cache. On this machine that is 4-7s per model, so a cold process
spent ~15s of an 18s load waiting on the network to be told nothing had
changed. Measured, three models, serial:

    default            18.14s
    local_files_only    2.87s

Identical weights, identical scores — the round-trips are pure overhead once
the cache is warm. So the load is attempted with `local_files_only=True` and
falls back to a networked load if anything is missing, which keeps first-run
download working exactly as it did (§10: weights are not baked into the image
and download on first run into the `model_cache` volume).

**Once each.** `functools.lru_cache` caches a *result*; it does not hold a lock
while computing one. Two threads calling an uncached `get_embedding_model()`
therefore both construct the model — 440MB loaded twice, concurrently, for one
cached entry. That was latent while `preload_models` loaded serially on one
thread; it stops being latent now that it loads three in parallel, and it was
already reachable via a request arriving mid-warmup. `guard()` is the lock the
warmup path's docstring always claimed was there.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def from_cache_first(constructor: Callable[..., T], model_name: str, **kwargs: Any) -> T:
    """Construct `model_name` from the local HF cache, falling back to network.

    The fallback catches broadly on purpose. The failure this is recovering
    from is "not in the cache", which `huggingface_hub` has spelled with
    several exception types across versions, and every *other* reason the
    cached load could fail — a partial download, a corrupted blob — has the
    same correct remedy: fetch it. A genuine failure (no disk, no network,
    a model name that does not exist) still raises, from the second attempt,
    carrying its own real error rather than a masked one.
    """
    try:
        return constructor(model_name, local_files_only=True, **kwargs)
    except Exception as exc:
        logger.info(
            "%s is not fully in the local cache (%s: %s) — fetching it. This is "
            "expected on a first run and costs a download; later runs load offline.",
            model_name,
            type(exc).__name__,
            exc,
        )
        return constructor(model_name, **kwargs)


def guard() -> threading.Lock:
    """A fresh lock, for a module that caches exactly one model.

    Each loader takes its own rather than sharing one, so the three preload
    threads still load in parallel — a single shared lock would serialize them
    and undo the reason they are on threads at all.
    """
    return threading.Lock()
