"""The composition root: build the FastAPI app, and nothing else.

This file used to be 295 lines holding every route, every response model,
the SSE framing and the startup logic. What is left is the assembly: create
the app, attach middleware, register exception handlers, include the router,
and define what happens at startup. Routes live in `app.api.routes`, their
shapes in `app.api.dto`, and the work behind them in `app.services`.

The layering, top to bottom, with dependencies pointing only downward:

    app.api            HTTP: routes, DTOs, dependency wiring, error mapping
    app.services       use cases: QueryService, IngestionService, HealthService
    app.domain         rules and vocabulary — imports no layer but app.core
    app.infrastructure adapters: Postgres, Qdrant, Elasticsearch, LLMs, models
    app.core           config, observability, exceptions (a leaf, used by all)

`app.infrastructure` sits below `app.domain` in the diagram but depends *on*
it: it implements the ports the domain declares. That inversion is the point
— it is why swapping Qdrant, Postgres, or the LLM vendor cannot touch a
single line of the logic that decides whether an answer is trustworthy.

`uvicorn app.main:app` still works, and so does `app.main:create_app` as a
factory for tests that want an isolated instance.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import build_router
from app.api.errors import register_exception_handlers
from app.core.observability import TraceIDMiddleware, configure_logging
from app.infrastructure.db.session import create_tables
from app.services.warmup import preload_models

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup work, per §10.

    Table creation is best-effort: compose already gates this container on
    Postgres reporting healthy, but a transient failure here should leave
    the app running and let `/health` report the problem, rather than
    killing the process and turning a recoverable blip into a restart loop.
    """
    configure_logging()

    try:
        create_tables()
    except Exception:
        logger.exception("Could not create tables at startup; /health will report Postgres state")

    # Non-blocking: see `app.services.warmup.preload_models` for why startup
    # does not wait.
    preload_models()

    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="KnowRAG",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Added as raw ASGI rather than via `@app.middleware("http")`, which wraps
    # every response in BaseHTTPMiddleware and interferes with the long-lived
    # generator behind /query/stream. See observability.TraceIDMiddleware.
    app.add_middleware(TraceIDMiddleware)

    register_exception_handlers(app)
    app.include_router(build_router())

    return app


app = create_app()
