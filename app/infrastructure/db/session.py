"""The SQLAlchemy engine and session factory — constructed once, here.

Previously there were three: `app/pg.py` built the engine, and both
`app/es.py` and `app/vdb.py` built their own `sessionmaker` on top of it so
they could run their own queries. Two indexers owning database sessions is
the coupling this refactor removes — they now receive chunks from a
repository and never touch a session at all.

Creating the engine at import is deliberate and safe: `create_engine` does
not connect, it only parses the URL and sets up a pool. The first real
connection happens when something executes a statement.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def check_postgres() -> tuple[bool, str | None]:
    """Readiness probe for `GET /health` (§7.2).

    Lives here, next to the engine, rather than in the health service: the
    service's job is to run probes and shape the report, and it should not
    have to know what "connected to Postgres" means. Never raises — a probe
    that propagated its own exception would take out the whole health
    response and say nothing about the other dependencies.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        logger.warning("Postgres health check failed: %s", exc)
        return False, str(exc)


def create_tables() -> None:
    """Create `chunks`/`documents` if they don't exist yet. Safe to call repeatedly."""
    # Imported for the side effect of registering the mappers on `Base` before
    # `create_all` reads its metadata. Without this, calling `create_tables()`
    # from a process that has not otherwise imported the ORM module creates
    # nothing at all, silently.
    from app.infrastructure.db import orm  # noqa: F401

    Base.metadata.create_all(bind=engine)
