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

from app.core.exceptions import (
    DocumentNotFound,
    GenerationUnavailable,
    InvalidChunkSelection,
    InvalidCredentials,
    InvalidToken,
    RegistrationClosed,
    UnsupportedUpload,
)

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


async def _invalid_chunk_selection(request: Request, exc: InvalidChunkSelection) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": str(exc)},
    )


async def _registration_closed(request: Request, exc: RegistrationClosed) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": str(exc)},
    )


async def _invalid_credentials(request: Request, exc: InvalidCredentials) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc)},
    )


async def _invalid_token(request: Request, exc: InvalidToken) -> JSONResponse:
    # `WWW-Authenticate` on every 401 this endpoint can produce, per RFC 7235
    # — the same header FastAPI's own `HTTPBearer` would set on a missing
    # header, kept consistent here since `get_current_user`
    # (`app.api.dependencies`) raises this instead of letting `HTTPBearer`
    # answer (its default is a 403 on a missing header, not the 401 every
    # other auth failure in this API uses).
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": str(exc)},
        headers={"WWW-Authenticate": "Bearer"},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(GenerationUnavailable, _generation_unavailable)
    app.add_exception_handler(DocumentNotFound, _document_not_found)
    app.add_exception_handler(UnsupportedUpload, _unsupported_upload)
    app.add_exception_handler(InvalidChunkSelection, _invalid_chunk_selection)
    app.add_exception_handler(RegistrationClosed, _registration_closed)
    app.add_exception_handler(InvalidCredentials, _invalid_credentials)
    app.add_exception_handler(InvalidToken, _invalid_token)
