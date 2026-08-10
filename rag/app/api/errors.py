"""Translating domain/service exceptions into HTTP responses.

Registered once on the app, so no route body carries a `try/except` that
turns an exception into a status code. That is what keeps the route bodies
one-liners and, more importantly, keeps the mapping consistent — the old
`/query` handler caught `GenerationUnavailable` inline, and `/query/stream`
did not, so the same failure was a 503 on one route and a generic error
event on the other.

The mapping itself is the interesting part, and one entry carries real
weight:

`GenerationUnavailable` is **503, never a 200 carrying an empty answer.** A
question the corpus cannot answer comes back `200` with
`state="insufficient_evidence"` — that path is the reason this system
exists. A generation provider that is out of quota is a different thing
entirely, and reporting "insufficient evidence" when the truth is "the
generator never ran" would be a lie of exactly the kind this system is built
to refuse.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import DocumentNotFound, GenerationUnavailable, UnsupportedUpload

logger = logging.getLogger(__name__)


async def _generation_unavailable(request: Request, exc: GenerationUnavailable) -> JSONResponse:
    headers = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(int(exc.retry_after) + 1)

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": str(exc)},
        headers=headers or None,
    )


async def _document_not_found(request: Request, exc: DocumentNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )


async def _unsupported_upload(request: Request, exc: UnsupportedUpload) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(GenerationUnavailable, _generation_unavailable)
    app.add_exception_handler(DocumentNotFound, _document_not_found)
    app.add_exception_handler(UnsupportedUpload, _unsupported_upload)
