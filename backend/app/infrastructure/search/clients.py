"""The one place a Qdrant or Elasticsearch client is constructed.

There were three. `app/vdb.py`, `app/es.py` and `app/retriver.py` each
carried their own `_qdrant_client`/`_es_client` global and their own
`get_*_client()` factory, so a single process held two Qdrant connections
and two Elasticsearch connections — one pair for indexing, another for
retrieval — and a change to connection settings had to be made in three
places to take effect everywhere.

Nothing here connects at import; both factories build on first use and cache
for the process lifetime.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from elasticsearch import Elasticsearch
from qdrant_client import QdrantClient

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)


@lru_cache(maxsize=1)
def get_es_client() -> Elasticsearch:
    return Elasticsearch(settings.es_url)


# --- Readiness probes (§7.2) -------------------------------------------------
#
# Next to the clients they exercise, for the same reason `check_postgres` sits
# next to the engine. Each returns `(ok, detail)` and never raises.


def check_qdrant() -> tuple[bool, str | None]:
    try:
        get_qdrant_client().get_collections()
        return True, None
    except Exception as exc:
        logger.warning("Qdrant health check failed: %s", exc)
        return False, str(exc)


def check_elasticsearch() -> tuple[bool, str | None]:
    try:
        if not get_es_client().ping():
            return False, "ping returned False"
        return True, None
    except Exception as exc:
        logger.warning("Elasticsearch health check failed: %s", exc)
        return False, str(exc)
