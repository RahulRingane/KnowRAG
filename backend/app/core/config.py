"""Centralized configuration for KnowRAG.

Per KNowRAG_SPEC.md §3: replaces hardcoded URLs (e.g. the ES endpoint) and
scattered `os.getenv` calls with a single `Settings` object every module
imports (`from app.core.config import settings`).

This is the only module in the application permitted to read the
environment. Every layer reads configuration from here, and nothing else
calls `os.getenv` — so what the system is configured with is answerable by
reading one file rather than grepping for environment access.

Backward compatibility note: the codebase's existing `.env` uses
`DATABASE_URL`, `QURL`, and `QAPI`. §3 renames the latter two fields to
`qdrant_url` / `qdrant_api_key`. `validation_alias` with `AliasChoices` is
used below so both the old names (`QURL` / `QAPI`) and the new spec names
(`QDRANT_URL` / `QDRANT_API_KEY`) resolve to the same field, so an existing
`.env` keeps working unchanged.

The same treatment is applied to `gemini_api_key`: `GOOGLE_GENERATIVE_AI_API_KEY`
is the name Google's own SDK docs and several of their client libraries use,
and it is what this repo's `.env` carries, so it is accepted as an alias for
§3's `GEMINI_API_KEY` rather than silently failing startup over a naming
difference. `AliasChoices` is order-sensitive — `GEMINI_API_KEY` wins when
both are set.
"""

from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # The user's .env lives at app/.env, not the repo root; accept either
        # location so resolution doesn't depend on the current working
        # directory the app/scripts happen to be launched from.
        env_file=(".env", "app/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Required — no default. Everything else below has a working default.
    database_url: str
    gemini_api_key: str = Field(
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY"),
    )

    qdrant_url: str = Field(
        default="http://localhost:6333",
        validation_alias=AliasChoices("QDRANT_URL", "QURL"),
    )
    qdrant_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("QDRANT_API_KEY", "QAPI"),
    )
    es_url: str = "http://localhost:9200"
    qdrant_collection: str = "knowrag"
    es_index: str = "knowrag"
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Deliberately NOT the same model as `reranker_model`, and the two must
    # never be collapsed into one setting. The reranker is trained on
    # relevance — "is this chunk related to the query" — while verification
    # asks "does this chunk entail this claim". §6.1 calls conflating them
    # out by name.
    #
    # This was previously hardcoded inside the verifier, on the grounds that
    # the config contract was frozen. It lives here now that infrastructure
    # reads all of its model names from one place; the default is byte-identical
    # to the hardcoded value, so nothing about which weights load has changed.
    #
    # OQ-3 Direction D, adopted 2026-08-03. Was `cross-encoder/nli-deberta-v3-base`
    # (Apache-2.0, SNLI+MultiNLI), which returns *confident* neutral on long
    # multi-topic premises — it rejected claims that were verbatim sentences
    # inside their own cited chunk at 0.994. Same architecture, same 184M size,
    # same latency; the difference is FEVER+ANLI in training, and FEVER is fact
    # verification against retrieved evidence passages, i.e. this exact
    # workload. Measured: false rejection 0.385 -> 0.051 with no change to D1
    # (0.000). MIT licensed, as is its microsoft/deberta-v3-base base model.
    nli_model: str = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
    # Selects which client `app.llm.generate_claims()` calls. Everything
    # downstream (verification, assembly, the routes) consumes the same plain
    # dict either way and never learns which provider produced it.
    llm_provider: Literal["gemini", "openai"] = "gemini"

    # gemini-2.5-flash returns 404 for keys created after ~2026-08 ("no longer
    # available to new users"), so it is not a safe default even though
    # ListModels still reports it. Override with GEMINI_MODEL; a *-flash-lite
    # variant is the lever if higher RPM is needed.
    gemini_model: str = "gemini-3.6-flash"

    # --- OpenAI (optional second provider) ----------------------------------
    # Optional with no default: a Gemini-only deployment must keep starting
    # with no OpenAI key present, exactly as an OpenAI-only one must start with
    # no Gemini key. `app.llm` constructs each client lazily for that reason.
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY"),
    )

    # gpt-4o-mini, chosen on cost against a fixed $5 credit rather than on
    # benchmark quality. Pricing as published on 2026-08-03: $0.15/M input,
    # $0.60/M output.
    #
    # This workload sends ~3.5K tokens per call (5 reranked chunks averaging
    # 1525 chars + a 2195-char system prompt) and returns a short claim-set
    # JSON, so the spend is input-dominated: ~$0.0006/call at 150 output
    # tokens, ~$0.0011/call at 1K. That is 4.4K-8.8K calls for $5 — hundreds
    # of full eval-suite runs (§8.3-8.4) with room for real usage.
    #
    # Do not "upgrade" this to gpt-4o or an o-series model without redoing
    # that math: gpt-4o is ~20x the per-call cost here (~300 calls for $5),
    # which does not cover the eval suite. Prices recorded as observed on a
    # date, not as constants (§12.1).
    openai_model: str = "gpt-4o-mini"
    top_k_retrieval: int = 20
    top_k_rerank: int = 5
    claim_verification_threshold: float = 0.55

    # Frontend origins allowed to call this API with credentials (WS-0). Never
    # widen this to "*" — `CORSMiddleware` rejects that combination with
    # `allow_credentials=True` outright, and it would be the wrong fix anyway:
    # a refresh cookie is coming in WS-1, and a credentialed wildcard origin
    # is exactly the CSRF surface cookies are supposed to be scoped away from.
    #
    # `NoDecode` opts this field out of pydantic-settings' default behaviour
    # for list-typed env vars, which is to `json.loads` the raw string before
    # any validator sees it — so `CORS_ORIGINS=http://a,http://b` would fail
    # startup with a JSON parse error before `_split_cors_origins` ever ran.
    # With decoding suppressed, the validator receives the raw string and a
    # comma-separated list — the format every other multi-value var in this
    # stack would use if there were one — works the same as a hand-written
    # `.env` line, no JSON-array quoting required.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:3001"],
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept `CORS_ORIGINS=http://a,http://b` (comma-separated) in addition
        to a JSON array. Only a raw env string reaches here — the default
        value and any already-JSON-decoded list bypass this branch untouched.
        """
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # --- Auth (WS-1, frontend_plan.md §3) -----------------------------------
    #
    # Required, no default — on purpose: the alternative is signing every
    # access and refresh token with a guessable literal baked into the
    # source, which is worse than a startup crash. Two things follow from
    # that, both handled at the point they bite:
    #
    #   1. `tests/conftest.py` seeds a placeholder before `app.core.config`
    #      is first imported, the same way it already does for
    #      `DATABASE_URL`/`GEMINI_API_KEY` — otherwise every test fails to
    #      collect, not just the auth ones.
    #   2. The real `backend/.env` needs a generated value appended (never
    #      committed) before the app will boot at all. See `.env.example`
    #      for how to generate one.
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    # 15 minutes: short enough that a stolen access token (kept in memory on
    # the frontend, never persisted per §3.2) has a small window, long
    # enough that the silent-refresh dance in §3.2 isn't firing constantly.
    access_token_ttl_minutes: int = 15
    # 7 days, and rotated on every `/auth/refresh` call (§3.1) — a stale
    # refresh cookie that never got used expires on its own; one that is
    # actively being used is replaced each time, so a leaked-but-unused
    # older cookie stops working the moment the legitimate session refreshes.
    refresh_token_ttl_days: int = 7
    # `False`/`"lax"` is correct for local dev over plain http, where
    # `Secure` would make the browser refuse to ever send the cookie back.
    # A cross-domain production deploy (frontend and API on different
    # origins) needs `refresh_cookie_secure=True` with
    # `refresh_cookie_samesite="none"` — `SameSite=None` is only honoured by
    # browsers when `Secure` is also set, so the two must change together.
    refresh_cookie_secure: bool = False
    refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"


settings = Settings()
