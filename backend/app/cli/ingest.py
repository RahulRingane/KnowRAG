"""`python -m app.cli.ingest [path] [--force] [--full-reindex]`

Runs exactly what `POST /ingest` runs in its background task, because it
calls the same `IngestionService`.
"""

from __future__ import annotations

import argparse
import logging

from app.infrastructure.db.session import create_tables
from app.services.ingestion_service import build_default_ingestion_service


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest a PDF into Postgres and index it into Qdrant + Elasticsearch."
    )
    parser.add_argument("path", nargs="?", default="data.pdf", help="Path to the PDF to ingest.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-chunk even if content_hash matches the last ingest for this filename.",
    )
    parser.add_argument(
        "--full-reindex",
        action="store_true",
        help="Drop and recreate the Qdrant collection and ES index before indexing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    args = _parse_args(argv)
    create_tables()

    result = build_default_ingestion_service().ingest(
        args.path,
        force=args.force,
        full_reindex=args.full_reindex,
    )

    print(
        f"document_id={result.document_id} status={result.status} "
        f"chunks={result.chunk_count} vectors={result.vector_count} "
        f"keyword_docs={result.keyword_count} (postgres: {result.pg_status})"
    )


if __name__ == "__main__":
    main()
