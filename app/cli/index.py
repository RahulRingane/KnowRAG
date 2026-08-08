"""`python -m app.cli.index [--document-id N] [--index qdrant|elasticsearch] [--full-reindex]`

Re-indexes chunks that are already in Postgres, without re-reading a PDF.
Replaces the separate `python app/vdb.py` and `python app/es.py` entrypoints;
`--index` selects one when you only want to rebuild that one.

This is the tool for the case the ingestion path deliberately does not
cover: Postgres is warm but a search index was wiped or its schema changed.
"""

from __future__ import annotations

import argparse
import logging

from app.infrastructure.db.chunk_repository import SqlChunkRepository
from app.infrastructure.search.elasticsearch_index import ElasticsearchIndex
from app.infrastructure.search.qdrant_index import QdrantIndex


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Index Postgres chunks into the search indexes.")
    parser.add_argument(
        "--document-id",
        type=int,
        default=None,
        help="Index only this document's chunks (default: all chunks).",
    )
    parser.add_argument(
        "--index",
        choices=["qdrant", "elasticsearch"],
        default=None,
        help="Index only this backend (default: both).",
    )
    parser.add_argument(
        "--full-reindex",
        action="store_true",
        help="Drop and recreate the collection/index before indexing, rather than upserting.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    args = _parse_args(argv)

    indexes = [QdrantIndex(), ElasticsearchIndex()]
    if args.index:
        indexes = [i for i in indexes if i.name == args.index]

    chunks = SqlChunkRepository().list_for_document(args.document_id)
    if not chunks:
        print("No chunks found in PostgreSQL.")
        return

    for index in indexes:
        count = index.index(chunks, full_reindex=args.full_reindex)
        print(f"{index.name}: {count} chunks indexed")


if __name__ == "__main__":
    main()
