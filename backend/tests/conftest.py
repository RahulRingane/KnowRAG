"""Test-session bootstrap.

`app.config.Settings()` requires `DATABASE_URL` and `GEMINI_API_KEY` (no
defaults, per app/config.py). Neither is needed to test `app.verify` (which
touches no database or LLM), but importing `app.verify` transitively imports
`app.config`, which instantiates the `settings` singleton at import time.

This module runs at collection time, before any test module (and therefore
before `app.config`) is imported, so setting placeholder env vars here —
only if not already set by a real environment/.env — is enough to let the
whole `app` package import cleanly in a bare test environment with no real
Postgres/Gemini credentials configured.
"""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-a-real-credential")
# WS-1 (frontend_plan.md §3): `jwt_secret` has no default on purpose — a
# signing key with a guessable fallback is worse than a startup crash. That
# means `Settings()` raises unless something sets `JWT_SECRET` before
# `app.core.config` is first imported, exactly like `DATABASE_URL` and
# `GEMINI_API_KEY` above; without this line the whole suite fails to
# collect, not just the auth tests.
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-a-real-credential")

# Pinned, not `setdefault`: this must beat whatever `LLM_PROVIDER` the
# developer's `.env` carries.
#
# `test_claims.py` patches `app.llm._call_gemini` and documents that no model
# call is ever made. That is only true while `settings.llm_provider` is
# "gemini" — the moment a real `.env` selects "openai", `generate_claims()`
# takes the other branch, sails straight past the patch, and reaches
# api.openai.com with the developer's real key. Observed exactly that: four
# tests failed with an empty call list while billing live requests against a
# fixed credit budget, which is the worst version of this failure because the
# assertion that fired says nothing about the spend.
#
# Environment beats `.env` in pydantic-settings, so setting it here (before
# `app.config` is first imported) makes the suite hermetic with respect to
# local configuration. Tests that want the OpenAI path still select it
# explicitly with monkeypatch, as `tests/test_openai.py` does.
os.environ["LLM_PROVIDER"] = "gemini"


@pytest.fixture(autouse=True)
def _no_real_openai_client(monkeypatch):
    """Fail any test that tries to construct a real OpenAI client.

    The `LLM_PROVIDER` pin above fixes the leak that actually happened, but it
    fixes it in configuration: a future test that selects "openai" itself and
    forgets to patch the call would reach api.openai.com again, and bill a
    fixed credit budget while reporting an ordinary assertion failure that
    says nothing about spend.

    This closes it structurally instead. Tests that exercise the OpenAI path
    patch at `_openai_completion` or above (see `tests/test_openai.py`) and
    never reach the client factory, so this is invisible to them — it only
    fires on the mistake.

    `_get_openai_client` resolves `OpenAI` from the module at call time, so
    patching the attribute is enough. It checks for a missing key *before*
    importing the SDK, which is why the missing-key test still gets its
    `GenerationUnavailable` rather than this error.
    """
    try:
        import openai
    except ModuleNotFoundError:  # Gemini-only environment; nothing to guard
        return

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "A test tried to construct a real OpenAI client, which would send "
            "a billable request to api.openai.com. Patch app.llm._openai_completion "
            "(or higher) instead."
        )

    monkeypatch.setattr(openai, "OpenAI", _blocked)
