"""Postgres persistence: engine, ORM tables, and the three repositories.

Split out of the old `app/pg.py`, which held the engine, the table
definitions, the queries, the PDF chunking, the text normalization and a
CLI in one 877-line module. What is left here is only the persistence:

    session.py              engine + sessionmaker + create_tables()
    orm.py                  the SQLAlchemy table classes
    document_repository.py  DocumentRepository over the documents table
    chunk_repository.py     ChunkRepository over the chunks table
    user_repository.py      UserRepository over the users table (WS-1)
"""

from app.infrastructure.db.chunk_repository import SqlChunkRepository
from app.infrastructure.db.document_repository import SqlDocumentRepository
from app.infrastructure.db.session import SessionLocal, create_tables, engine
from app.infrastructure.db.user_repository import SqlUserRepository

__all__ = [
    "SessionLocal",
    "SqlChunkRepository",
    "SqlDocumentRepository",
    "SqlUserRepository",
    "create_tables",
    "engine",
]
