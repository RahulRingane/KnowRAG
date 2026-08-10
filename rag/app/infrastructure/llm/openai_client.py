"""The OpenAI provider adapter.

From the old `app/llm.py`. Uses the official `openai` client with
`response_format` json_schema mode (`strict: true`), which is the same
guarantee Gemini's `response_schema` gives by a different name: the API
itself constrains decoding to the schema, so a structurally malformed claim
set is not reachable and there is no prose-parsing or repair step here
either. Selected on cost (`gpt-4o-mini`, see `Settings.openai_model`), not
on benchmark quality.

What *is* still reachable is a refusal (the model declines, and the SDK puts
that in `message.refusal` rather than `content`) and a truncated response
(`finish_reason == "length"`). Neither is fixed by re-asking, so both fail
loudly instead of costing a second call against a fixed credit budget.

The SDK is imported inside the client factory so `openai` stays an optional
dependency for Gemini-only deployments.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.exceptions import GenerationUnavailable
from app.infrastructure.llm.prompts import (
    CLAIM_SCHEMA,
    SYSTEM_PROMPT,
    user_prompt,
    validate_claims_payload,
)

logger = logging.getLogger(__name__)

_openai_client: "Any | None" = None


def _get_openai_client() -> Any:
    global _openai_client
    if _openai_client is None:
        # Key first, import second: a missing key is a configuration error and
        # must say so, not surface as a ModuleNotFoundError from an SDK that
        # was never going to be usable without it.
        if not settings.openai_api_key:
            raise GenerationUnavailable(
                "llm_provider is 'openai' but OPENAI_API_KEY is not set."
            )

        from openai import OpenAI

        _openai_client = OpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Derive OpenAI's `strict: true` dialect from a plain JSON Schema.

    Structured Outputs is stricter than what Gemini accepts: every object must
    set `additionalProperties: false` and must list *every* property in
    `required`. Deriving that from `CLAIM_SCHEMA` rather than hand-maintaining
    a second literal is what keeps the two providers from quietly drifting
    apart on what a claim is.
    """
    out = dict(schema)
    if out.get("type") == "object":
        properties = out.get("properties", {})
        out["properties"] = {k: _strict_schema(v) for k, v in properties.items()}
        out["required"] = list(properties)
        out["additionalProperties"] = False
    elif out.get("type") == "array" and "items" in out:
        out["items"] = _strict_schema(out["items"])
    return out


_OPENAI_CLAIM_SCHEMA = _strict_schema(CLAIM_SCHEMA)

_OPENAI_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "claim_set",
        "strict": True,
        "schema": _OPENAI_CLAIM_SCHEMA,
    },
}


# A 429 from OpenAI is two different failures wearing the same status code,
# and only one of them is something backoff can clear:
#
#   rate_limit_exceeded  -> RPM/TPM, clears in seconds; wait it out
#   insufficient_quota   -> the account's credit is spent; waiting never helps
#
# The second one matters more than usual here: this provider is running
# against a fixed $5 credit (see `Settings.openai_model`), so exhaustion is a
# real end state, not a theoretical one. Retrying it seven times spends two
# minutes to produce the same error and hides the actual problem. Matched on
# the error *kind*, never on a number or a balance, per §12.1.
_OPENAI_EXHAUSTED = re.compile(r"insufficient_quota|exceeded your current quota|billing", re.IGNORECASE)

# Transient failures that carry no HTTP status (socket dropped, timeout).
_OPENAI_TRANSIENT = {"APIConnectionError", "APITimeoutError"}


def _openai_status(exc: BaseException) -> int | None:
    return getattr(exc, "status_code", None)


def _is_openai_quota_exhausted(exc: BaseException) -> bool:
    """True when a 429 means "out of credit" rather than "too fast"."""
    return _openai_status(exc) == 429 and bool(_OPENAI_EXHAUSTED.search(str(exc)))


def _is_openai_retryable(exc: BaseException) -> bool:
    """429 and 5xx are worth retrying; other 4xx never are.

    Mirrors the Gemini adapter's `_is_retryable`, including the carve-out for
    the quota failure backoff cannot fix — there it is a 24-hour window, here
    it is a spent balance, and in both cases failing fast surfaces the real
    problem instead of burying it under seven retries.
    """
    if _is_openai_quota_exhausted(exc):
        logger.error(
            "OpenAI credit exhausted for model %s — not retrying. Top up the "
            "account or switch llm_provider back to 'gemini'.",
            settings.openai_model,
        )
        return False

    if type(exc).__name__ in _OPENAI_TRANSIENT:
        return True

    status = _openai_status(exc)
    return status == 429 or (status is not None and status >= 500)


# OpenAI phrases its wait hint as "Please try again in 7.234s" — or in
# milliseconds ("in 133ms") when the limiter is only a fraction of a window
# away, which parsed as a bare number would become a 133-second wait.
_OPENAI_RETRY_AFTER = re.compile(r"try again in ([0-9.]+)(ms|s)\b", re.IGNORECASE)


def _openai_retry_after(exc: BaseException) -> float | None:
    match = _OPENAI_RETRY_AFTER.search(str(exc))
    if not match:
        return None
    value = float(match.group(1))
    return value / 1000 if match.group(2).lower() == "ms" else value


def as_unavailable(exc: BaseException) -> GenerationUnavailable:
    """OpenAI's analogue of the Gemini adapter's `as_unavailable`.

    Keeping the raw payload out of the message matters for the same reason it
    does on the Gemini side: it reaches an HTTP body and an SSE frame, and it
    carries account and quota metadata.
    """
    logger.warning("Generation provider error (openai): %s", exc)

    status = _openai_status(exc)
    exhausted = _is_openai_quota_exhausted(exc)

    if status == 429:
        kind = "credit exhausted, top up the account" if exhausted else "rate limit"
        return GenerationUnavailable(
            f"Generation quota exhausted for {settings.openai_model} ({kind}).",
            retry_after=None if exhausted else _openai_retry_after(exc),
            # Not literally a *daily* window, but the flag's meaning downstream
            # is "backoff will not fix this", which is exactly the case here.
            daily_quota=exhausted,
        )

    return GenerationUnavailable(f"Generation provider returned {status}.")


def _messages(question: str, context_block: str) -> list[dict[str, str]]:
    """The one prompt shape both the buffered and streaming paths send.

    No schema instruction is appended to `SYSTEM_PROMPT` the way a
    JSON-object-mode provider would need: `strict: true` carries the schema
    out-of-band, so the prompt stays exactly the §5.2 rules and the two
    providers are prompted identically.
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt(question, context_block)},
    ]


# 429s are less routine here than on a free tier, but RPM/TPM limits still
# bind during an eval run's burst, so the same 7-attempt/60s-cap budget
# applies — about 122s of waiting, which clears a one-minute window.
@retry(
    retry=retry_if_exception(_is_openai_retryable),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(7),
    reraise=True,
)
def _completion(messages: list[dict[str, str]]) -> Any:
    response = _get_openai_client().chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        response_format=_OPENAI_RESPONSE_FORMAT,
        temperature=0.0,  # determinism matters more than creativity here
    )
    return response.choices[0]


def _parse_and_validate(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response was not valid JSON: {exc}") from exc
    return validate_claims_payload(payload)


def call(question: str, context_block: str) -> dict:
    """Generate claims via OpenAI Structured Outputs.

    No repair round-trip, unlike a JSON-object-mode provider: `strict: true`
    makes a schema violation unreachable, so the remaining failure modes are a
    refusal and a truncated response — neither of which re-asking fixes, and
    both of which would cost a second call against a fixed credit budget.

    All three failures raise rather than returning an empty claim set. A
    dropped claim set that looked like "the model found nothing to say" would
    be indistinguishable from an honest refusal downstream, and only one of
    those is true — that confusion is exactly what this system exists to
    prevent.
    """
    choice = _completion(_messages(question, context_block))
    message = getattr(choice, "message", None)

    refusal = getattr(message, "refusal", None)
    if refusal:
        raise GenerationUnavailable(f"OpenAI declined to answer: {refusal}")

    if getattr(choice, "finish_reason", None) == "length":
        raise GenerationUnavailable(
            f"OpenAI response was truncated before the claim set was complete "
            f"({settings.openai_model} hit the output token limit)."
        )

    try:
        return _parse_and_validate(getattr(message, "content", None) or "")
    except ValueError as exc:
        raise GenerationUnavailable(
            f"OpenAI returned malformed claim JSON for {settings.openai_model}: {exc}"
        ) from exc


def stream(question: str, context_block: str):
    """OpenAI's half of `stream_claims`.

    Exists so that selecting OpenAI does not silently leave `/query/stream`
    calling Gemini — which would use the wrong key and the wrong model while
    every log line and metric said "openai".

    A refusal raises here for the same reason it does in `call`, and the
    streaming path makes the consequence worse rather than milder: the SDK
    puts a declined answer in `delta.refusal`, so `delta.content` stays empty
    and the stream ends having yielded nothing. The query service reads an
    empty accumulation as `{"claims": []}` and reports *insufficient
    evidence* — the generator declining becomes the corpus lacking support,
    which is the one confusion this system exists to prevent. Raised on the
    first refusal delta, before any partial refusal text can be mistaken for
    claim JSON.
    """
    stream_response = _get_openai_client().chat.completions.create(
        model=settings.openai_model,
        messages=_messages(question, context_block),
        response_format=_OPENAI_RESPONSE_FORMAT,
        temperature=0.0,
        stream=True,
    )
    for chunk in stream_response:
        # A frame can carry no choices at all (a usage-only final frame, or a
        # proxy keepalive); indexing [0] unconditionally turns that into an
        # IndexError mid-stream, after tokens are already on the wire.
        if not chunk.choices:
            continue

        delta = chunk.choices[0].delta
        refusal = getattr(delta, "refusal", None)
        if refusal:
            raise GenerationUnavailable(f"OpenAI declined to answer: {refusal}")

        if delta.content:
            yield delta.content
