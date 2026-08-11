"""OpenAI provider path — fully mocked, no network, no live credit spend.

Not one test in this file may reach `api.openai.com`. The provider runs
against a fixed $5 credit, so a test suite that quietly spent it would be a
worse bug than anything it could catch: every client call is monkeypatched at
`_openai_completion` or above, and `test_no_test_constructs_a_real_client`
guards the boundary directly.

What's actually under test is the seam around Structured Outputs. `strict:
true` makes a schema violation unreachable, so the interesting failures are
the ones it does *not* cover — refusals, truncation, rate limits, spent
credit — and the rule that none of them may reach verification looking like
"the model found nothing to say". A silently dropped claim set and an honest
refusal are indistinguishable downstream, and only one of them is true.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.exceptions import GenerationUnavailable
from app.infrastructure.llm import cache, gemini, openai_client, prompts, provider


class FakeAPIError(Exception):
    """Stands in for `openai.APIStatusError`.

    The production predicates read `status_code` and `str(exc)` rather than
    isinstance-checking the SDK's exception tree, so a fake carrying those two
    things exercises the real code path without importing openai's internals.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class FakeConnectionError(Exception):
    pass


FakeConnectionError.__name__ = "APIConnectionError"


def choice(content: str | None = None, *, refusal: str | None = None, finish_reason: str = "stop"):
    """A `chat.completions` choice, shaped like the SDK's response object."""
    return SimpleNamespace(
        message=SimpleNamespace(content=content, refusal=refusal),
        finish_reason=finish_reason,
    )


def delta(*, content: str | None = None, refusal: str | None = None):
    """One `chat.completions` streaming frame."""
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, refusal=refusal))]
    )


# The shape a usage-only final frame arrives in: a frame with no choices at all.
NO_CHOICES = SimpleNamespace(choices=[])


def fake_stream_client(frames):
    """A client whose `chat.completions.create` returns `frames`."""
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: iter(frames)),
        )
    )


@pytest.fixture
def openai(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")
    monkeypatch.setattr(settings, "openai_api_key", "test-key-not-a-real-credential")
    monkeypatch.setattr(cache, "CACHE_ENABLED", False)
    return settings


# Every claim now declares its kind (OQ-5). A payload without it is invalid by
# schema, so the fixture carries it rather than relying on a default.
VALID = {
    "claims": [
        {"kind": "assertion", "text": "Embedded systems are safety-critical.", "citations": ["C1"]}
    ]
}


# --- the strict-mode schema --------------------------------------------------


def test_strict_schema_is_derived_from_the_shared_claim_schema():
    # Hand-maintaining a second literal is how the two providers would end up
    # disagreeing about what a claim is.
    assert set(openai_client._OPENAI_CLAIM_SCHEMA["properties"]) == set(prompts.CLAIM_SCHEMA["properties"])


def test_strict_schema_meets_structured_outputs_requirements():
    # `strict: true` is rejected outright unless every object closes
    # additionalProperties and lists every property in required.
    root = openai_client._OPENAI_CLAIM_SCHEMA
    claim = root["properties"]["claims"]["items"]

    for obj in (root, claim):
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])

    assert sorted(claim["properties"]) == ["citations", "kind", "text"]


def test_response_format_requests_strict_json_schema_mode():
    fmt = openai_client._OPENAI_RESPONSE_FORMAT
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] is openai_client._OPENAI_CLAIM_SCHEMA


def test_deriving_strict_schema_leaves_the_original_untouched():
    # CLAIM_SCHEMA is what the Gemini path sends; mutating it in place would
    # change the other provider's request as a side effect.
    assert "additionalProperties" not in prompts.CLAIM_SCHEMA
    assert prompts.CLAIM_SCHEMA["required"] == ["claims"]


def test_prompt_carries_no_schema_instruction(openai, monkeypatch):
    # The schema travels out-of-band in response_format, so the system prompt
    # stays exactly the §5.2 rules and both providers are prompted identically.
    captured = {}

    def fake_completion(messages):
        captured["messages"] = messages
        return choice(json.dumps(VALID))

    monkeypatch.setattr(openai_client, "_completion", fake_completion)
    openai_client.call("q", "[C1] ctx")

    system = captured["messages"][0]
    assert system["role"] == "system"
    assert system["content"] == prompts.SYSTEM_PROMPT
    assert captured["messages"][1]["content"] == "[C1] ctx\n\nQuestion: q"


# --- schema validation -------------------------------------------------------


def test_valid_payload_passes_through_unchanged():
    assert prompts.validate_claims_payload(VALID) == VALID


def test_empty_claim_list_is_valid_not_an_error():
    # A model that correctly finds nothing to say must not look like a failure.
    assert prompts.validate_claims_payload({"claims": []}) == {"claims": []}


@pytest.mark.parametrize(
    "payload, expected",
    [
        ([], "expected a JSON object"),
        ({"answer": "..."}, "missing required top-level key 'claims'"),
        ({"claims": "C1"}, "'claims' must be an array"),
        ({"claims": ["not an object"]}, "claims[0] must be an object"),
        ({"claims": [{"citations": []}]}, "claims[0].text must be a string"),
        ({"claims": [{"text": "x"}]}, "claims[0].citations must be an array of strings"),
        ({"claims": [{"text": "x", "citations": "C1"}]}, "citations must be an array"),
        ({"claims": [{"text": "x", "citations": [1]}]}, "citations must be an array"),
    ],
)
def test_malformed_payloads_are_rejected_with_a_specific_reason(payload, expected):
    # Defensive behind strict mode, but this is the last gate before
    # verification, so the message has to name the actual problem.
    with pytest.raises(ValueError) as exc:
        prompts.validate_claims_payload(payload)
    assert expected in str(exc.value)


def test_non_json_response_is_a_value_error_not_a_crash():
    with pytest.raises(ValueError, match="not valid JSON"):
        openai_client._parse_and_validate("Here are the claims: ...")


def test_malformed_json_fails_loudly_rather_than_being_repaired(openai, monkeypatch):
    # No repair round-trip on this path: strict mode makes a violation
    # unreachable, and a second call would spend credit chasing a response
    # re-asking cannot fix.
    calls = []

    def fake_completion(messages):
        calls.append(messages)
        return choice("not json at all")

    monkeypatch.setattr(openai_client, "_completion", fake_completion)

    with pytest.raises(GenerationUnavailable, match="malformed claim JSON"):
        openai_client.call("q", "[C1] ctx")
    assert len(calls) == 1


# --- failures strict mode does not cover -------------------------------------


def test_refusal_is_raised_not_returned_as_an_empty_claim_set(openai, monkeypatch):
    # The SDK puts a declined answer in `message.refusal`, leaving `content`
    # None. Returning {"claims": []} here would report "insufficient evidence"
    # when the truth is that the generator declined.
    monkeypatch.setattr(
        openai_client,
        "_completion",
        lambda messages: choice(None, refusal="I can't help with that."),
    )

    with pytest.raises(GenerationUnavailable, match="declined to answer"):
        openai_client.call("q", "[C1] ctx")


def test_truncated_response_is_raised_not_silently_half_parsed(openai, monkeypatch):
    # finish_reason="length" means the claim set is incomplete; strict mode
    # guarantees shape, not completeness.
    monkeypatch.setattr(
        openai_client,
        "_completion",
        lambda messages: choice('{"claims": [', finish_reason="length"),
    )

    with pytest.raises(GenerationUnavailable, match="truncated"):
        openai_client.call("q", "[C1] ctx")


# --- retry classification ----------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_rate_limit_and_server_errors_are_retryable(status):
    assert openai_client._is_openai_retryable(FakeAPIError("boom", status_code=status))


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(status):
    # Retrying these spends credit on a call that can never succeed.
    assert not openai_client._is_openai_retryable(FakeAPIError("bad request", status_code=status))


def test_connection_errors_are_retryable():
    assert openai_client._is_openai_retryable(FakeConnectionError("socket dropped"))


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 429 - insufficient_quota",
        "You exceeded your current quota, please check your plan and billing details",
        "429 billing hard limit reached",
    ],
)
def test_spent_credit_is_not_retried(message):
    # The $5 running out is a real end state, not a blip; seven retries spend
    # ~2 minutes to produce the same error and hide the actual problem.
    exc = FakeAPIError(message, status_code=429)
    assert openai_client._is_openai_quota_exhausted(exc)
    assert not openai_client._is_openai_retryable(exc)


def test_rate_limit_is_retried_not_mistaken_for_spent_credit():
    exc = FakeAPIError(
        "Rate limit reached for gpt-4o-mini. Please try again in 7.2s", status_code=429
    )
    assert not openai_client._is_openai_quota_exhausted(exc)
    assert openai_client._is_openai_retryable(exc)


def test_exhaustion_detection_ignores_the_balance(openai):
    # §12.1: match on the kind of failure, never on a number — balances move.
    exc = FakeAPIError("insufficient_quota: $0.0000 remaining of $5.00", status_code=429)
    assert openai_client._is_openai_quota_exhausted(exc)


def test_retry_budget_outlasts_a_one_minute_window():
    # 7 attempts capped at 60s is ~2+4+8+16+32+60 = 122s of waiting; a shorter
    # schedule drops items while the rate-limit window is still open.
    assert openai_client._completion.retry.stop.max_attempt_number == 7
    assert openai_client._completion.retry.wait.exp_base ** 1 * 1 == 2


# --- error translation -------------------------------------------------------


def test_spent_credit_surfaces_as_unavailable_with_no_retry_hint(openai):
    exc = openai_client.as_unavailable(FakeAPIError("insufficient_quota", status_code=429))
    assert isinstance(exc, GenerationUnavailable)
    assert exc.daily_quota is True
    assert exc.retry_after is None


def test_rate_limit_carries_the_providers_wait_hint(openai):
    exc = openai_client.as_unavailable(
        FakeAPIError("Rate limit reached. Please try again in 7.2s", status_code=429)
    )
    assert exc.daily_quota is False
    assert exc.retry_after == pytest.approx(7.2)


def test_millisecond_wait_hint_is_not_read_as_seconds(openai):
    # "try again in 133ms" parsed as a bare number would stall a request for
    # 133 seconds over a 0.133-second limit.
    exc = openai_client.as_unavailable(
        FakeAPIError("Rate limit reached. Please try again in 133ms", status_code=429)
    )
    assert exc.retry_after == pytest.approx(0.133)


def test_raw_provider_payload_stays_out_of_the_message(openai):
    # This message reaches an HTTP body and an SSE frame, and the raw payload
    # carries account and quota metadata.
    secret = "org_abc123_quota_metadata"
    exc = openai_client.as_unavailable(FakeAPIError(f"400 bad request {secret}", status_code=400))
    assert secret not in str(exc)
    assert "400" in str(exc)


# --- provider dispatch -------------------------------------------------------


def test_generate_claims_routes_to_openai_and_never_touches_gemini(openai, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("Gemini must not be called when llm_provider='openai'")

    monkeypatch.setattr(gemini, "call", explode)
    monkeypatch.setattr(gemini, "_get_gemini_client", explode)
    monkeypatch.setattr(openai_client, "_completion", lambda messages: choice(json.dumps(VALID)))

    assert provider.generate_claims("q", "[C1] ctx", chunk_ids=["1:0"]) == VALID


def test_generate_claims_still_routes_to_gemini_by_default(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(cache, "CACHE_ENABLED", False)

    def explode(*a, **k):
        raise AssertionError("OpenAI must not be called when llm_provider='gemini'")

    monkeypatch.setattr(openai_client, "call", explode)
    monkeypatch.setattr(openai_client, "_get_openai_client", explode)
    monkeypatch.setattr(gemini, "call", lambda q, c: VALID)

    assert provider.generate_claims("q", "[C1] ctx", chunk_ids=["1:0"]) == VALID


def test_gemini_is_the_shipped_default():
    assert type(settings).model_fields["llm_provider"].default == "gemini"


def test_stream_routes_to_openai_so_the_wrong_key_is_never_used(openai, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("Gemini must not be streamed when llm_provider='openai'")

    monkeypatch.setattr(gemini, "_get_gemini_client", explode)
    monkeypatch.setattr(openai_client, "stream", lambda q, c: iter(['{"claims"', ": []}"]))

    assert "".join(provider.stream_claims("q", "[C1] ctx")) == '{"claims": []}'


def test_streamed_refusal_is_raised_not_read_as_insufficient_evidence(openai, monkeypatch):
    # The failure this guards is worse on the streaming path than the buffered
    # one: a refusal lands in `delta.refusal`, so `delta.content` stays empty,
    # the stream yields nothing, and `QueryService.stream()` reads the empty
    # accumulation as {"claims": []} — reporting that the corpus lacked
    # support when in fact the generator declined.
    monkeypatch.setattr(
        openai_client,
        "_get_openai_client",
        lambda: fake_stream_client([delta(refusal="I can't help with that.")]),
    )

    with pytest.raises(GenerationUnavailable, match="declined to answer"):
        list(openai_client.stream("q", "[C1] ctx"))


def test_streamed_refusal_yields_nothing_before_it_raises(openai, monkeypatch):
    # Partial refusal prose must never reach the caller's accumulator, where it
    # would be parsed as claim JSON.
    monkeypatch.setattr(
        openai_client,
        "_get_openai_client",
        lambda: fake_stream_client([delta(refusal="I can't"), delta(content='{"claims": []}')]),
    )

    emitted = []
    with pytest.raises(GenerationUnavailable):
        for chunk in openai_client.stream("q", "[C1] ctx"):
            emitted.append(chunk)

    assert emitted == []


def test_stream_survives_a_frame_carrying_no_choices(openai, monkeypatch):
    # A usage-only final frame (or a proxy keepalive) has choices == []; an
    # unconditional [0] would raise IndexError with tokens already on the wire.
    monkeypatch.setattr(
        openai_client,
        "_get_openai_client",
        lambda: fake_stream_client(
            [delta(content='{"claims"'), NO_CHOICES, delta(content=": []}")]
        ),
    )

    assert "".join(openai_client.stream("q", "[C1] ctx")) == '{"claims": []}'


def test_stream_sends_the_same_prompt_and_schema_as_the_buffered_path(openai, monkeypatch):
    # Two providers is already one axis of drift; two code paths per provider
    # sending different prompts would be another.
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return iter([delta(content="{}")])

    monkeypatch.setattr(
        openai_client,
        "_get_openai_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )

    list(openai_client.stream("q", "[C1] ctx"))

    assert captured["messages"] == openai_client._messages("q", "[C1] ctx")
    assert captured["response_format"] is openai_client._OPENAI_RESPONSE_FORMAT
    assert captured["model"] == "gpt-4o-mini"
    assert captured["stream"] is True


def test_streamed_provider_errors_are_translated_at_the_boundary(openai, monkeypatch):
    def fail(q, c):
        raise FakeAPIError("Rate limit reached. Please try again in 3.0s", status_code=429)
        yield  # pragma: no cover — makes this a generator, as the real one is

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(openai_client, "stream", fail)

    with pytest.raises(GenerationUnavailable) as exc:
        list(provider.stream_claims("q", "[C1] ctx"))
    assert exc.value.retry_after == pytest.approx(3.0)


def test_openai_errors_are_translated_before_reaching_the_caller(openai, monkeypatch):
    def fail(messages):
        raise FakeAPIError("Rate limit reached. Please try again in 3.0s", status_code=429)

    monkeypatch.setattr(openai_client, "_completion", fail)

    # §5.2's one-integration-point guarantee is worth nothing if callers have
    # to catch the SDK's own exception type.
    with pytest.raises(GenerationUnavailable) as exc:
        provider.generate_claims("q", "[C1] ctx", chunk_ids=["1:0"])
    assert exc.value.retry_after == pytest.approx(3.0)


# --- cache isolation ---------------------------------------------------------


def test_cache_key_follows_the_active_provider(monkeypatch):
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.6-flash")
    monkeypatch.setattr(settings, "openai_model", "gpt-4o-mini")

    monkeypatch.setattr(settings, "llm_provider", "gemini")
    gemini_model = provider._active_model()
    monkeypatch.setattr(settings, "llm_provider", "openai")
    openai_model = provider._active_model()

    assert gemini_model == "gemini-3.6-flash"
    assert openai_model == "gpt-4o-mini"

    # Two providers answering the same question over the same chunks must not
    # collide in the cache — one would be served as the other's output.
    assert cache.cache_key("q", ["1:0"], gemini_model) != cache.cache_key("q", ["1:0"], openai_model)


def test_openai_response_is_cached_under_the_openai_model(openai, monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "CACHE_ENABLED", True)
    monkeypatch.setattr(openai_client, "_completion", lambda messages: choice(json.dumps(VALID)))

    provider.generate_claims("q", "[C1] ctx", chunk_ids=["1:0"])

    expected = cache._cache_path(cache.cache_key("q", ["1:0"], "gpt-4o-mini"))
    assert expected.exists()


def test_cache_hit_spends_no_credit(openai, monkeypatch, tmp_path):
    # The whole point of the dev cache (§12.1) under a fixed budget: a re-run
    # of an unchanged eval item must not reach the provider at all.
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "CACHE_ENABLED", True)

    calls = []

    def fake_completion(messages):
        calls.append(messages)
        return choice(json.dumps(VALID))

    monkeypatch.setattr(openai_client, "_completion", fake_completion)

    assert provider.generate_claims("q", "[C1] ctx", chunk_ids=["1:0"]) == VALID
    assert provider.generate_claims("q", "[C1] ctx", chunk_ids=["1:0"]) == VALID
    assert len(calls) == 1


# --- lazy client construction ------------------------------------------------


def test_neither_client_is_constructed_at_import():
    # The blocker the lazy-client refactor existed to fix: a module-level
    # genai.Client(...) made importing the LLM package fail on an OpenAI-only
    # deployment, before provider selection could run.
    assert gemini._gemini_client is None or openai_client._openai_client is None
    for module in (gemini, openai_client):
        assert not hasattr(module, "client"), (
            f"module-level eager client is back in {module.__name__}"
        )


def test_openai_selected_without_a_key_fails_with_a_clear_message(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(openai_client, "_openai_client", None)

    with pytest.raises(GenerationUnavailable, match="OPENAI_API_KEY is not set"):
        openai_client._get_openai_client()


def test_no_test_constructs_a_real_client(openai, monkeypatch):
    # The guard on the credit: every test above patches at _openai_completion
    # or higher, so the real client factory must never be reached.
    monkeypatch.setattr(openai_client, "_openai_client", None)
    monkeypatch.setattr(openai_client, "_completion", lambda messages: choice(json.dumps(VALID)))

    provider.generate_claims("q", "[C1] ctx", chunk_ids=["1:0"])

    assert openai_client._openai_client is None


# --- cache key covers the prompt ---------------------------------------------


def test_cache_key_changes_when_the_system_prompt_changes(monkeypatch):
    # Without this, editing SYSTEM_PROMPT leaves every cached entry looking
    # valid and the next eval replays generations from the OLD prompt — a
    # prompt change then measures as "no effect", with nothing erroring and
    # the numbers looking stable. The most convincing way to be wrong.
    before = cache.cache_key("q", ["1:0"], "gpt-4o-mini")
    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", prompts.SYSTEM_PROMPT + "\n5. One more rule.")
    after = cache.cache_key("q", ["1:0"], "gpt-4o-mini")
    assert before != after


def test_cache_key_is_stable_for_an_unchanged_prompt():
    assert cache.cache_key("q", ["1:0"], "gpt-4o-mini") == cache.cache_key("q", ["1:0"], "gpt-4o-mini")


# --- claim kind (OQ-5) --------------------------------------------------------


def test_kind_is_required_by_the_strict_schema():
    claim = openai_client._OPENAI_CLAIM_SCHEMA["properties"]["claims"]["items"]
    assert "kind" in claim["required"]
    assert claim["properties"]["kind"]["enum"] == ["assertion", "refusal"]


def test_missing_kind_is_rejected_not_defaulted():
    # Defaulting an unknown kind to "assertion" is precisely the path where a
    # refusal gets scored as evidence — the bug OQ-5 exists to close.
    with pytest.raises(ValueError, match="kind must be"):
        prompts.validate_claims_payload({"claims": [{"text": "x", "citations": []}]})


@pytest.mark.parametrize("kind", ["Assertion", "REFUSAL", "note", "", None, 1])
def test_unknown_kind_values_are_rejected(kind):
    with pytest.raises(ValueError, match="kind must be"):
        prompts.validate_claims_payload({"claims": [{"kind": kind, "text": "x", "citations": []}]})


@pytest.mark.parametrize("kind", ["assertion", "refusal"])
def test_both_valid_kinds_pass(kind):
    payload = {"claims": [{"kind": kind, "text": "x", "citations": []}]}
    assert prompts.validate_claims_payload(payload) == payload


def test_prompt_teaches_the_two_kinds():
    # The model declares kind at generation time; if the prompt stops
    # explaining the distinction the schema still forces a value and the model
    # will guess, which is worse than failing.
    assert '"kind": "assertion"' in prompts.SYSTEM_PROMPT
    assert '"kind": "refusal"' in prompts.SYSTEM_PROMPT
