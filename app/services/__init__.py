"""The service layer — one class per use case.

    QueryService      ask a question, get a fact-checked answer
    IngestionService  put a document into the corpus
    HealthService     report whether the dependencies are reachable
    warmup            load the models once, at startup

Each service is constructed with the ports it needs and holds no global
state, so the object graph is assembled in exactly one place
(`app.api.dependencies` for the API, `app.cli` for the command line) and
every dependency is visible in a constructor signature rather than hidden in
an import.

What belongs here, and what does not:

- **Here**: sequencing a use case, transactions across several
  collaborators, translating between what a caller asked for and what the
  domain needs, and deciding what counts as a failure of the *operation*.
- **Not here**: business rules (they belong in `app.domain`, so they are
  reusable and testable without a service), and anything HTTP — no status
  codes, no request objects, no `HTTPException`. A service must be callable
  from a CLI, a test, or a queue worker with no adaptation, which is the
  property that makes the API layer thin.
"""

from app.services.health_service import HealthService
from app.services.ingestion_service import IngestionService
from app.services.query_service import QueryService

__all__ = ["HealthService", "IngestionService", "QueryService"]
