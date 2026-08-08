"""§8.1 area 2 — claim extraction parsing, and the citation-tag contract.

No Gemini call is ever made: `app.infrastructure.llm.gemini.call` is
monkeypatched, so
these run offline. What is under test is everything *around* the model —
the `[Cn]` tags the prompt is built from, the dict-to-`Claim` conversion,
and the dev cache key — because that is where a fault produces confidently
wrong output rather than an obvious error.

The tag map matters more than it looks: `format_context` decides which
chunk `"C2"` means, and `verify_claim` resolves the model's citations
against that same mapping. If the two ever disagree, verification scores a
claim against evidence the model never saw and returns `SUPPORTED`, which
is exactly the failure this system exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.domain.context import format_context
from app.domain.models import Chunk, Claim
from app.infrastructure.llm import cache, gemini, provider
from app.infrastructure.llm.provider import LLMClaimGenerator


def _chunk(doc: int, idx: int, text: str) -> Chunk:
    return Chunk(source="qdrant", score=1.0, document_id=doc, chunk_index=idx, text=text)


# --- §5.1 context formatting ------------------------------------------------


def test_format_context_numbers_chunks_from_c1():
    block, tags = format_context([_chunk(1, 0, "alpha"), _chunk(1, 5, "beta")])

    assert "[C1] (doc: 1, chunk: 0)" in block
    assert "[C2] (doc: 1, chunk: 5)" in block
    assert "alpha" in block and "beta" in block
    assert list(tags) == ["C1", "C2"]


def test_format_context_tags_are_stable_across_calls():
    chunks = [_chunk(1, 0, "a"), _chunk(2, 9, "b"), _chunk(1, 3, "c")]

    first_block, first_tags = format_context(chunks)
    second_block, second_tags = format_context(chunks)

    assert first_block == second_block
    assert {t: (c.document_id, c.chunk_index) for t, c in first_tags.items()} == {
        t: (c.document_id, c.chunk_index) for t, c in second_tags.items()
    }


def test_format_context_tag_map_points_at_the_chunk_shown_under_that_tag():
    chunks = [_chunk(1, 0, "first"), _chunk(1, 1, "second")]
    _block, tags = format_context(chunks)

    assert tags["C2"].text == "second"
    assert tags["C2"].chunk_index == 1


def test_format_context_on_empty_chunk_list_is_empty_not_an_error():
    block, tags = format_context([])

    assert block == ""
    assert tags == {}


# --- Claim parsing -----------------------------------------------------------


def test_claims_parse_into_the_claim_contract(monkeypatch):
    payload = {
        "claims": [
            {"text": "RISC uses an orthogonal instruction set.", "citations": ["C1", "C3"]},
            {"text": "The context does not specify headcount.", "citations": []},
        ]
    }
    monkeypatch.setattr(gemini, "call", lambda q, c: payload)
    monkeypatch.setattr(cache, "CACHE_ENABLED", False)

    claims = [Claim(**c) for c in provider.generate_claims("q", "ctx")["claims"]]

    assert [c.text for c in claims] == [
        "RISC uses an orthogonal instruction set.",
        "The context does not specify headcount.",
    ]
    assert claims[0].citations == ["C1", "C3"]
    # An uncited claim is representable, not an error — §5.2 requires the
    # model to emit "not found in context" statements with empty citations,
    # and §6.2 turns those into UNSUPPORTED rather than rejecting the parse.
    assert claims[1].citations == []


def test_empty_claim_list_is_valid(monkeypatch):
    monkeypatch.setattr(gemini, "call", lambda q, c: {"claims": []})
    monkeypatch.setattr(cache, "CACHE_ENABLED", False)

    assert provider.generate_claims("q", "ctx")["claims"] == []


def test_generator_converts_dicts_to_claim_objects(monkeypatch):
    """The adapter is where provider payloads stop being dicts.

    Above `LLMClaimGenerator` nothing sees the raw shape — the service layer
    receives `Claim` objects, which is what keeps payload parsing out of the
    orchestration.
    """
    monkeypatch.setattr(
        provider,
        "generate_claims",
        lambda q, c, chunk_ids=None: {"claims": [{"text": "t", "citations": ["C1"]}]},
    )

    claims = LLMClaimGenerator().generate("q", "ctx", ["1:0"])

    assert len(claims) == 1
    assert isinstance(claims[0], Claim)
    assert claims[0].citations == ["C1"]


def test_missing_claims_key_yields_no_claims(monkeypatch):
    """A response without `claims` must not raise — it means "nothing said"."""
    monkeypatch.setattr(provider, "generate_claims", lambda q, c, chunk_ids=None: {})

    assert LLMClaimGenerator().generate("q", "ctx", []) == []


# --- Dev cache key (§12.1) ---------------------------------------------------


def test_chunk_ids_recovered_from_context_block_tags():
    block, _tags = format_context([_chunk(3, 4, "x"), _chunk(3, 5, "y")])

    assert cache.chunk_ids_from_context(block) == ["3:4", "3:5"]


def test_cache_key_ignores_chunk_id_ordering_but_not_membership():
    same = cache.cache_key("q", ["1:1", "1:2"], "m") == cache.cache_key("q", ["1:2", "1:1"], "m")
    different = cache.cache_key("q", ["1:1"], "m") != cache.cache_key("q", ["1:1", "1:2"], "m")

    assert same
    assert different


def test_cache_key_varies_with_question_and_model():
    base = cache.cache_key("q", ["1:1"], "m")

    assert cache.cache_key("other", ["1:1"], "m") != base
    assert cache.cache_key("q", ["1:1"], "other-model") != base


def test_cache_hit_skips_the_model_call(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "CACHE_ENABLED", True)

    calls = []

    def _fake(question, context):
        calls.append(question)
        return {"claims": [{"text": "cached", "citations": []}]}

    monkeypatch.setattr(gemini, "call", _fake)

    first = provider.generate_claims("q", "ctx", chunk_ids=["1:0"])
    second = provider.generate_claims("q", "ctx", chunk_ids=["1:0"])

    assert first == second
    assert len(calls) == 1, "second identical call should have been served from cache"


def test_use_cache_false_forces_a_live_call(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "CACHE_ENABLED", True)

    calls = []
    monkeypatch.setattr(
        gemini, "call", lambda q, c: (calls.append(q), {"claims": []})[1]
    )

    provider.generate_claims("q", "ctx", chunk_ids=["1:0"])
    provider.generate_claims("q", "ctx", chunk_ids=["1:0"], use_cache=False)

    assert len(calls) == 2


def test_unreadable_cache_entry_is_ignored_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(cache, "CACHE_ENABLED", True)

    key = cache.cache_key("q", ["1:0"], settings.gemini_model)
    (tmp_path / f"{key}.json").write_text("{not valid json")

    monkeypatch.setattr(gemini, "call", lambda q, c: {"claims": [{"text": "fresh", "citations": []}]})

    assert provider.generate_claims("q", "ctx", chunk_ids=["1:0"])["claims"][0]["text"] == "fresh"


# --- Retry policy (§12.1: 429s are expected steady state) --------------------


class _ApiError(Exception):
    def __init__(self, code):
        self.code = code


def test_rate_limit_and_server_errors_are_retryable(monkeypatch):
    monkeypatch.setattr(gemini.errors, "APIError", _ApiError)

    assert gemini._is_retryable(_ApiError(429))
    assert gemini._is_retryable(_ApiError(503))


def test_client_errors_other_than_429_are_not_retried(monkeypatch):
    """Retrying a 400/401/404 just burns quota on a call that cannot succeed."""
    monkeypatch.setattr(gemini.errors, "APIError", _ApiError)

    assert not gemini._is_retryable(_ApiError(400))
    assert not gemini._is_retryable(_ApiError(401))
    assert not gemini._is_retryable(ValueError("unrelated"))


class _QuotaError(_ApiError):
    def __init__(self, code, quota_id):
        super().__init__(code)
        self.quota_id = quota_id

    def __str__(self):
        return f"429 RESOURCE_EXHAUSTED {{'quotaId': '{self.quota_id}'}}"


def test_per_minute_quota_is_retried(monkeypatch):
    monkeypatch.setattr(gemini.errors, "APIError", _ApiError)

    exc = _QuotaError(429, "GenerateRequestsPerMinutePerProjectPerModel-FreeTier")

    assert gemini._is_retryable(exc)
    assert not gemini._is_daily_quota_exhausted(exc)


def test_daily_quota_is_not_retried(monkeypatch):
    """No backoff clears a 24-hour window; retrying burns minutes per call
    to produce the identical error."""
    monkeypatch.setattr(gemini.errors, "APIError", _ApiError)

    exc = _QuotaError(429, "GenerateRequestsPerDayPerProjectPerModel-FreeTier")

    assert gemini._is_daily_quota_exhausted(exc)
    assert not gemini._is_retryable(exc)


def test_gemini_sdk_appears_in_exactly_one_module():
    """§5.2's one-function-swap guarantee, enforced rather than documented.

    Read from the import graph, not by grepping source text: several modules
    *mention* `google.genai` in a docstring explaining why they must not
    import it, and a text search counts those as violations of the rule they
    exist to describe.

    Two modules may import it. `gemini.py` is the adapter. `provider.py`
    imports `errors` alone, to catch provider exceptions and translate them
    into the neutral `GenerationUnavailable` — that translation is the reason
    nothing above infrastructure ever needs the SDK.
    """
    import ast
    import pathlib

    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"

    hits = []
    for path in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            if any(n == "google" or n.startswith("google.") for n in names):
                hits.append(str(path.relative_to(app_dir)))
                break

    assert sorted(hits) == [
        "infrastructure/llm/gemini.py",
        "infrastructure/llm/provider.py",
    ], f"the Gemini SDK must stay inside app/infrastructure/llm/, found in {sorted(hits)}"
