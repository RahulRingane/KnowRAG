"""The presentation layer — HTTP, and only HTTP.

    dto.py           request/response shapes that exist because of the wire
    dependencies.py  the composition root: which implementations get injected
    errors.py        exception -> status code, registered once on the app
    routes/          one module per resource

The rule this layer follows: **a route body calls exactly one service method
and shapes the result.** No SQL, no Qdrant or Elasticsearch calls, no
chunking, no prompt construction, no verification, and no try/except that
turns a domain condition into a status code.

That is §7.2's requirement, and it is also what makes the services usable
from the CLI: everything a route knows how to do that a CLI cannot is in
this package, and nothing else.
"""

from fastapi import APIRouter

from app.api.routes import auth, corpus, ingest, query, system


def build_router() -> APIRouter:
    """Every route in the application, assembled in one place."""
    router = APIRouter()
    router.include_router(system.router)
    router.include_router(auth.router)
    router.include_router(ingest.router)
    router.include_router(query.router)
    router.include_router(corpus.router)
    return router


__all__ = ["build_router"]
