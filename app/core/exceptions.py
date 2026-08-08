"""The exception types that cross layer boundaries.

Each one exists because some layer needs to signal a condition to a layer
*above* it without dragging its own dependencies up with it. That is the
whole reason they live in `core` rather than beside the code that raises
them:

- `GenerationUnavailable` is raised by `app.infrastructure.llm` and handled
  in `app.api`. Defining it next to the provider clients would mean the
  route layer has to `import app.infrastructure.llm.provider` just to write
  an `except` clause — the SDK-shaped dependency would leak upward through
  the exception type, which is exactly what a provider-neutral return type
  was supposed to prevent.
- `DocumentNotFound` is raised by the service layer and turned into a 404 by
  the API layer. The service must not know that HTTP exists, and the route
  must not know that Postgres does; a shared exception is the seam.

Nothing here imports from any other layer, so an `except` clause never costs
the catcher a dependency it did not already have.
"""

from __future__ import annotations


class KnowRAGError(Exception):
    """Base class for every error this application raises on purpose.

    Lets an outermost handler distinguish "a condition this system models"
    from "an unexpected bug", without enumerating subclasses.
    """


class GenerationUnavailable(KnowRAGError, RuntimeError):
    """The generation provider refused or is unreachable.

    Provider-neutral on purpose. `app.infrastructure.llm` is the only place
    permitted to import a provider SDK, and that guarantee is worth nothing
    if callers have to catch `google.genai.errors.APIError` to handle a
    failure — the SDK would leak into the service layer and the routes
    through the exception type instead of the return type.

    Kept a `RuntimeError` subclass as well as a `KnowRAGError` one so that
    existing `except RuntimeError` handlers and any caller written against
    the pre-refactor hierarchy keep behaving identically.

    `retry_after` is the provider's own hint in seconds where one is
    offered, and is not to be trusted blindly: the API reports ~50s for a
    daily-quota failure whose real window is 24 hours, which is what
    `daily_quota` distinguishes.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        daily_quota: bool = False,
    ):
        super().__init__(message)
        self.retry_after = retry_after
        self.daily_quota = daily_quota


class DocumentNotFound(KnowRAGError):
    """No `documents` row exists for the requested id.

    Raised by `IngestionService.get_status()`. The service layer models this
    as an error rather than returning `None` so the route body stays a
    single expression and cannot forget the check — a `None` that silently
    serialized would produce a 200 describing a document that does not
    exist.
    """

    def __init__(self, document_id: int):
        super().__init__(f"No document with id {document_id}.")
        self.document_id = document_id


class UnsupportedUpload(KnowRAGError):
    """The uploaded file is not something the ingestion pipeline accepts.

    Carries the reason rather than a status code: mapping it onto 400 is the
    API layer's job, and the service layer that raises it does not know it is
    being called over HTTP.
    """
