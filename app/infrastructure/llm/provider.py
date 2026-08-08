"""Provider selection, caching, and the `ClaimGenerator` adapter.

This is the seam §5.2 asks for: everything above it depends only on
`app.domain.ports.ClaimGenerator` and on `Claim` objects, and never learns
which vendor produced them. Adding or swapping a provider is a change
confined to this package.

Two providers, selected by `settings.llm_provider`, each in its own module:

- **gemini** (default) — `gemini.py`, native `response_schema` JSON mode.
- **openai** — `openai_client.py`, Structured Outputs (`strict: true`).

Both are imported lazily inside the dispatch functions. That is load-bearing,
not tidiness: `gemini.py` imports `google.genai` at module scope and
`openai_client.py` needs `openai`, so importing both eagerly would make a
single-provider deployment require both SDKs to be installed.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.core.exceptions import GenerationUnavailable
from app.domain.models import Claim
from app.infrastructure.llm import cache

logger = logging.getLogger(__name__)


def _active_model() -> str:
    """The model name for the currently selected provider.

    Used for cache keys and error messages. The dev cache keys on the model
    (§12.1), so this must follow `llm_provider` — keying an OpenAI response
    under `gemini_model` would let the two providers collide in the cache and
    serve one's output as the other's.
    """
    return settings.openai_model if settings.llm_provider == "openai" else settings.gemini_model


def generate_claims(
    question: str,
    context_block: str,
    *,
    chunk_ids: list[str] | None = None,
    use_cache: bool = True,
) -> dict:
    """Generate structured claims for `question` given `context_block`.

    Returns the parsed `{"claims": [{"kind": ..., "text": ..., "citations":
    [...]}]}` dict — the exact shape `app.domain.models.Claim` validates
    against, with no prose parsing or repair step, courtesy of each
    provider's native structured-output mode.

    `chunk_ids` is an optional caching hint: pass the canonical `chunk_key()`
    strings of the retrieved chunks so the dev cache keys on `(question,
    retrieved_chunk_ids, model)` exactly as §12.1 specifies. If omitted, the
    ids are recovered from the `(doc: X, chunk: Y)` tags already present in
    `context_block`.

    `use_cache=False` (or `cache.CACHE_ENABLED = False`) forces a live call
    regardless of what's cached.
    """
    ids = chunk_ids if chunk_ids is not None else cache.chunk_ids_from_context(context_block)
    key = cache.cache_key(question, ids, _active_model())

    if use_cache and cache.CACHE_ENABLED:
        cached = cache.load(key)
        if cached is not None:
            logger.debug("generate_claims cache hit (key=%s)", key[:12])
            return cached

    if settings.llm_provider == "openai":
        from app.infrastructure.llm import openai_client

        try:
            result = openai_client.call(question, context_block)
        except GenerationUnavailable:
            raise
        except Exception as exc:  # openai SDK errors; kept out of the caller's sight
            raise openai_client.as_unavailable(exc) from exc
    else:
        from google.genai import errors

        from app.infrastructure.llm import gemini

        try:
            result = gemini.call(question, context_block)
        except errors.APIError as exc:
            raise gemini.as_unavailable(exc) from exc

    if use_cache and cache.CACHE_ENABLED:
        cache.store(key, result)

    return result


def stream_claims(question: str, context_block: str):
    """Yield raw text deltas as the model produces them (§7's `/query/stream`).

    The caller accumulates the deltas, parses the completed JSON, and only
    then verifies — verification needs the whole answer, which is exactly
    why §7 specifies it as a *final* event rather than something
    interleaved with the stream.

    What streams is the structured-output JSON itself, because the schema
    mode is what removes the prose-parsing step (§5.2) and is not worth
    giving up for prettier deltas. A client that wants to render prose should
    wait for the parsed claims in the final event; the deltas are a progress
    signal.

    Two deliberate differences from `generate_claims`:

    - **No retry.** `tenacity` cannot retry a call whose partial output has
      already been written to the client's socket. A 429 surfaces as a
      stream error rather than a silent stall.
    - **No cache.** A cache hit has nothing to stream, and writing to the
      cache from here would store a response the caller may have abandoned
      mid-stream. `POST /query` remains the cached path.
    """
    if settings.llm_provider == "openai":
        from app.infrastructure.llm import openai_client

        try:
            yield from openai_client.stream(question, context_block)
        except GenerationUnavailable:
            raise
        except Exception as exc:
            raise openai_client.as_unavailable(exc) from exc
        return

    from google.genai import errors

    from app.infrastructure.llm import gemini

    try:
        yield from gemini.stream(question, context_block)
    except errors.APIError as exc:
        raise gemini.as_unavailable(exc) from exc


class LLMClaimGenerator:
    """`app.domain.ports.ClaimGenerator` over the provider dispatch above.

    Thin by design: its whole job is to turn the provider's dict into
    validated `Claim` objects, so no caller anywhere else has to know the
    payload shape. That one conversion used to live in `chain.py`, which
    meant the orchestration layer was parsing provider output.
    """

    def generate(
        self, question: str, context_block: str, chunk_ids: list[str] | None = None
    ) -> list[Claim]:
        raw = generate_claims(question, context_block, chunk_ids=chunk_ids)
        return [Claim(**c) for c in raw.get("claims", [])]

    def stream(self, question: str, context_block: str):
        return stream_claims(question, context_block)
