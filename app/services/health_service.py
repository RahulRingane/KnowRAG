"""The readiness use case (§7.2).

From the old `app/deps.py`. §7.2 requires `GET /health` to actually exercise
Postgres, Qdrant, and Elasticsearch rather than report process liveness, and
requires the route body to stay a thin call rather than reimplement what the
storage modules already do.

This service runs probes and shapes the report; it does not know what any
individual probe does. Each concrete probe lives next to the client it
exercises (`app.infrastructure.db.session.check_postgres`,
`app.infrastructure.search.clients.check_qdrant` / `.check_elasticsearch`),
which is why there is no `sqlalchemy` import anywhere in this file.

Probes arrive as a mapping of name -> callable, so a test builds
`HealthService({"postgres": lambda: (False, "boom")})` and asserts the
report shape with nothing running.
"""

from __future__ import annotations

from typing import Mapping

from app.domain.ports import HealthProbe


def default_probes() -> dict[str, HealthProbe]:
    """The production probe set — a composition root, hence the local imports.

    Deferred rather than module-scope so that importing this module neither
    constructs a client nor requires every SDK to be installed, and so the
    service layer keeps no static dependency on infrastructure.
    """
    from app.infrastructure.db.session import check_postgres
    from app.infrastructure.search.clients import check_elasticsearch, check_qdrant

    return {
        "postgres": check_postgres,
        "qdrant": check_qdrant,
        "elasticsearch": check_elasticsearch,
    }


class HealthService:
    """Probes every dependency and reports the result."""

    def __init__(self, probes: Mapping[str, HealthProbe] | None = None):
        self._probes = dict(probes) if probes is not None else default_probes()

    def report(self) -> tuple[bool, dict]:
        """Probe everything; return `(all_ok, response_body)`.

        Every probe runs even after one fails — a report that stopped at the
        first broken dependency would hide the fact that two are down, which
        is precisely when a health endpoint earns its keep.
        """
        checks = {name: probe() for name, probe in self._probes.items()}

        all_ok = all(ok for ok, _ in checks.values())

        body = {
            "status": "ok" if all_ok else "degraded",
            "service": "KnowRAG",
            "checks": {
                name: {"status": "ok" if ok else "error", **({"detail": detail} if detail else {})}
                for name, (ok, detail) in checks.items()
            },
        }
        return all_ok, body
