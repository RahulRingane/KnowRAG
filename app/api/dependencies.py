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
