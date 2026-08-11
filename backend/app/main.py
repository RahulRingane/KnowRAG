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
from fastapi.middleware.cors import CORSMiddleware

from app.api import build_router
from app.api.errors import register_exception_handlers
from app.core.config import settings
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

    # Starlette builds the middleware stack by wrapping the app in each
    # `add_middleware()` registration in turn, and — counter-intuitively —
    # the *last* call ends up *outermost*: `Starlette.add_middleware()`
    # inserts at the front of `user_middleware`, and `build_middleware_stack`
    # then wraps from that list's *end* inward, so whatever was added last is
    # the final wrap and therefore the first thing a request meets and the
    # last thing a response passes through.
    #
    # CORSMiddleware is added first (so it ends up *inner*, still outside
    # `ExceptionMiddleware` and every route) and TraceIDMiddleware is added
    # last (so it ends up *outermost*), on purpose: every request — including
    # a CORS preflight `OPTIONS` and any request CORS will go on to reject —
    # gets a trace id bound and logged, which matches this codebase's stance
    # elsewhere that observability should cover a request before anything
    # decides what to do with it. CORS still wraps `ExceptionMiddleware` and
    # every route either way, so its `Access-Control-*` headers still land on
    # every real response this API returns, error responses included.
    #
    # `CORSMiddleware` is pure ASGI, like `TraceIDMiddleware` (see its class
    # docstring on why that matters for `/query/stream`'s generator) — it
    # does not wrap responses in `BaseHTTPMiddleware`, so stacking it here
    # does not reintroduce the buffering problem raw ASGI was chosen to avoid.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # Never combine `allow_origins=["*"]` with this — browsers reject a
        # wildcard origin alongside credentialed requests outright, and a
        # refresh cookie (WS-1) needs `allow_credentials=True` to ever reach
        # this API cross-origin. `cors_origins` must therefore always be an
        # explicit list.
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        # So `fetch()` can read the 503 backoff hint `GenerationUnavailable`
        # attaches (see `app.api.errors`) — browsers only expose response
        # headers a CORS response explicitly allows, `Retry-After` included.
        expose_headers=["Retry-After"],
    )
    app.add_middleware(TraceIDMiddleware)

    register_exception_handlers(app)
    app.include_router(build_router())

    return app


app = create_app()
