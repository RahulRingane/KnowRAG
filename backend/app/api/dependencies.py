"""The composition root for HTTP requests — where the object graph is built.

Every route gets its collaborators through `Depends()` on one of these
providers. That is what makes the service layer worth having:

    app.dependency_overrides[get_query_service] = lambda: FakeQueryService()

replaces the entire retrieval/generation/verification stack for a test with
no monkeypatching of module globals, which is how the route tests used to
substitute behaviour.

The services are cached per process rather than rebuilt per request. They
are stateless and their expensive parts (clients, model singletons) are
already process-wide caches, so rebuilding them per request would allocate
wrappers around the same objects for no benefit. `lru_cache` also means the
graph is constructed on the *first request*, not at import — so importing
`app.main` still connects to nothing.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import InvalidToken
from app.domain.models import User
from app.services.auth_service import AuthService, build_default_auth_service
from app.services.corpus_service import CorpusService, build_default_corpus_service
from app.services.health_service import HealthService
from app.services.ingestion_service import IngestionService, build_default_ingestion_service
from app.services.query_service import QueryService, build_default_query_service


@lru_cache(maxsize=1)
def get_query_service() -> QueryService:
    return build_default_query_service()


@lru_cache(maxsize=1)
def get_ingestion_service() -> IngestionService:
    return build_default_ingestion_service()


@lru_cache(maxsize=1)
def get_health_service() -> HealthService:
    return HealthService()


@lru_cache(maxsize=1)
def get_corpus_service() -> CorpusService:
    return build_default_corpus_service()


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return build_default_auth_service()


# `auto_error=False`: FastAPI's `HTTPBearer` answers a missing
# `Authorization` header with its own `403`, not the `401` every other auth
# failure in this API uses (`app.api.errors` maps `InvalidToken` to 401).
# Disabling its auto-error and raising `InvalidToken` ourselves on a missing
# header keeps that one case consistent with "expired token", "wrong `typ`",
# and "revoked" — all four are the same fact to a caller ("not authenticated")
# and should not surface as two different status codes depending on which
# one happened.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    service: AuthService = Depends(get_auth_service),
) -> User:
    """The protected-route dependency: `Depends(get_current_user)` on
    `/query`, `/query/stream`, `/ingest`, `/ingest/{document_id}`,
    `/documents`, and `/chunks` (frontend_plan.md §2.2's list — `/`,
    `/health`, `/metrics`, and `/auth/*` stay open).

    A thin adapter from "HTTP Bearer credentials" to
    `AuthService.authenticate()`; every actual decision (signature, expiry,
    `typ`, revocation) lives in that call, not here.
    """
    if credentials is None:
        raise InvalidToken("No bearer token was provided.")
    return service.authenticate(credentials.credentials)
