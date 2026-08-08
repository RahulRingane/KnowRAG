"""HTTP routes, one module per resource.

Split out of the old single `app/main.py` so that adding a route does not
mean editing the file that also owns startup, middleware and the app object.
Each module exposes a `router`; `app.api.build_router` assembles them.
"""

from app.api.routes import ingest, query, system

__all__ = ["ingest", "query", "system"]
