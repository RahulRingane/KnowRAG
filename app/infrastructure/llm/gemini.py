"""The Gemini provider adapter.

From the old `app/llm.py`. Uses the `google-genai` SDK directly rather than
LangChain's wrapper so native `response_schema` JSON mode is available. That
removes any prose-parsing/repair step: output is valid `{"claims": [...]}`
JSON by construction.

§12.1 operational notes this module encodes directly:
- Free-tier 429s are expected steady-state behavior under any real eval or
  usage load, not an edge case — retry-with-backoff is mandatory.
- `temperature=0.0` because determinism matters more than creativity for a
  fact-checker; some SDK defaults are non-zero, so this is set explicitly.

The client is constructed lazily, so an OpenAI-only deployment imports this
module (and starts) with no Gemini key present.
"""

from __future__ import annotations

import json
import logging
import re

from google import genai
from google.genai import errors, types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.exceptions import GenerationUnavailable
from app.infrastructure.llm.prompts import CLAIM_SCHEMA, SYSTEM_PROMPT, user_prompt

logger = logging.getLogger(__name__)

# Constructed once, on first use rather than at import.
#
# This was previously a module-level `genai.Client(...)`. It cannot stay that
# way now that `llm_provider` can select OpenAI: `gemini_api_key` has no
# default, so an OpenAI-only deployment would raise on import — before
# provider selection ever got a chance to run.
_gemini_client: "genai.Client | None" = None


def _get_gemini_client() -> "genai.Client":
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


# --- Retry (§5.2, §12.1: 429s are expected steady-state, not an edge case) --


# Not every 429 is the same failure. The free tier meters per-minute *and*
# per-day, and only one of those is something backoff can fix. The quota
# identifier distinguishes them:
#
#   GenerateRequestsPerMinutePerProjectPerModel-FreeTier   -> wait it out
#   GenerateRequestsPerDayPerProjectPerModel-FreeTier      -> done for today
#
# Matched on the identifier, never on the quota *number*, because §12.1 is
# explicit that the limits change and must not be hardcoded.
_PER_DAY_QUOTA = re.compile(r"PerDay", re.IGNORECASE)


def _is_daily_quota_exhausted(exc: BaseException) -> bool:
    """True when a 429 is the daily cap rather than the per-minute one.

    The API sends `RetryInfo: retryDelay 50s` for this case too, which is
    misleading — the window is 24 hours, not 50 seconds. Believing it costs
    the full retry budget per call and still fails: an eval run of 35
    remaining items burns over an hour to produce 35 identical errors.
    """
    return (
        isinstance(exc, errors.APIError)
        and exc.code == 429
        and bool(_PER_DAY_QUOTA.search(str(exc)))
    )


def _is_retryable(exc: BaseException) -> bool:
    """429 (rate limit — the expected free-tier steady state) and 5xx
    (transient server-side failure) are worth retrying. 4xx errors other
    than 429 (bad auth, bad request, not found) are not — retrying those
    just burns quota on a call that will never succeed.

    Daily-quota 429s are explicitly excluded: no backoff schedule short of
    hours can clear one, so failing fast surfaces the real problem instead
    of burying it under retries.
    """
    if _is_daily_quota_exhausted(exc):
        logger.error(
            "Daily free-tier quota exhausted for model %s — not retrying. "
            "Wait for the quota to reset, switch to a model with a separate "
            "bucket (e.g. gemini-2.5-flash-lite), or use a billed key.",
            settings.gemini_model,
        )
        return False

    return isinstance(exc, errors.APIError) and (
        exc.code == 429 or (exc.code is not None and exc.code >= 500)
    )


# The retry budget has to outlast the quota window, not just smooth over a
# blip. Free-tier gemini-2.5-flash is metered per *minute* (measured: limit
# 5, with the API's own RetryInfo asking for ~50s), so a 5-attempt schedule
# capped at 30s tops out around 30s of total waiting and fails the call
# while the window is still open. That is not hypothetical — it dropped 2
# of 16 items on the first adversarial eval run.
#
# 7 attempts with a 60s cap gives roughly 2+4+8+16+32+60 = 122s of waiting,
# which clears a one-minute window with margin.
_RETRY_AFTER = re.compile(r"retry in ([0-9.]+)s", re.IGNORECASE)


def as_unavailable(exc: "errors.APIError") -> GenerationUnavailable:
    """Translate a provider error into the neutral exception callers catch.

    The provider's raw payload is logged in full but deliberately kept out
    of the exception message: it is a multi-line JSON blob that ends up in
    an HTTP response body and an SSE frame, and it carries the project's
    quota metadata. Callers get a one-line summary instead.
    """
    logger.warning("Generation provider error: %s", exc)

    daily = _is_daily_quota_exhausted(exc)
    match = _RETRY_AFTER.search(str(exc))
    retry_after = float(match.group(1)) if match else None

    if exc.code == 429:
        kind = "daily quota, resets on the provider's schedule" if daily else "per-minute rate limit"
        message = f"Generation quota exhausted for {settings.gemini_model} ({kind})."
        return GenerationUnavailable(
            message,
            retry_after=None if daily else retry_after,
            daily_quota=daily,
        )

    return GenerationUnavailable(f"Generation provider returned {exc.code}.")


def _config() -> "types.GenerateContentConfig":
    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=CLAIM_SCHEMA,
        temperature=0.0,  # determinism matters more than creativity here
    )


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(7),
    reraise=True,
)
def call(question: str, context_block: str) -> dict:
    response = _get_gemini_client().models.generate_content(
        model=settings.gemini_model,
        contents=user_prompt(question, context_block),
        config=_config(),
    )
    return json.loads(response.text)


def stream(question: str, context_block: str):
    """Yield raw text deltas. No retry — see `provider.stream_claims`."""
    stream_response = _get_gemini_client().models.generate_content_stream(
        model=settings.gemini_model,
        contents=user_prompt(question, context_block),
        config=_config(),
    )

    for chunk in stream_response:
        if chunk.text:
            yield chunk.text
