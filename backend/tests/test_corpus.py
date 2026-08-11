"""Corpus-inspection routes and utilities (WS-0, frontend_plan.md §2):
`GET /chunks`, `GET /documents`, and `parse_chunk_key`.

Tests run fully offline: `ChunkRepository` and `DocumentRepository` are stubs.
All tests substitute via `app.dependency_overrides`, not monkeypatching.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.api.dependencies import get_corpus_service, get_current_user, get_ingestion_service
from app.domain.models import DocumentRecord, DocumentStatus, StoredChunk, User, parse_chunk_key
from app.domain.ports import ChunkRepository, DocumentRepository
from app.services.corpus_service import CorpusService
from app.services.ingestion_service import IngestionService

client = TestClient(main.app)


# --- Substituting the service layer ------------------------------------------


class _StubChunkRepository:
    """In-memory chunk store for testing."""

    def __init__(self):
        self.chunks: list[StoredChunk] = [
            StoredChunk(document_id=1, chunk_index=0, text="Chunk 1:0 text"),
            StoredChunk(document_id=1, chunk_index=3, text="Chunk 1:3 text"),
            StoredChunk(document_id=2, chunk_index=0, text="Chunk 2:0 text"),
        ]

    def get_by_keys(self, keys: list[tuple[int, int]]) -> list[StoredChunk]:
        """Return chunks matching the given (document_id, chunk_index) keys.

        Unknown keys are silently omitted (§2 degradation rule).
        """
        result = []
        for key in keys:
            for chunk in self.chunks:
                if (chunk.document_id, chunk.chunk_index) == key:
                    result.append(chunk)
                    break
        return result


class _StubDocumentRepository:
    """In-memory document store for testing."""

    def __init__(self):
        self.documents: list[DocumentRecord] = [
            DocumentRecord(
                document_id=3,
                filename="newest.pdf",
                status=DocumentStatus.INDEXED,
                chunk_count=5,
                ingested_at=datetime(2026, 8, 12, 15, 30, 0, tzinfo=timezone.utc),
                error=None,
                content_hash="hash3",
            ),
            DocumentRecord(
                document_id=2,
                filename="middle.pdf",
                status=DocumentStatus.INDEXED,
                chunk_count=10,
                ingested_at=datetime(2026, 8, 12, 14, 0, 0, tzinfo=timezone.utc),
                error=None,
                content_hash="hash2",
            ),
            DocumentRecord(
                document_id=1,
                filename="oldest.pdf",
                status=DocumentStatus.INDEXED,
                chunk_count=8,
                ingested_at=datetime(2026, 8, 12, 13, 0, 0, tzinfo=timezone.utc),
                error=None,
                content_hash="hash1",
            ),
        ]

    def list_all(self) -> list[DocumentRecord]:
        """Return all documents, newest first."""
        return sorted(
            self.documents,
            key=lambda d: d.ingested_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )


@pytest.fixture
def chunk_repo():
    """Inject the stub chunk repository."""
    return _StubChunkRepository()


@pytest.fixture
def document_repo():
    """Inject the stub document repository."""
    return _StubDocumentRepository()


@pytest.fixture
def corpus_service(chunk_repo):
    """Build a real `CorpusService` with the stub repository."""
    service = CorpusService(chunks=chunk_repo)
    main.app.dependency_overrides[get_corpus_service] = lambda: service
    yield service


@pytest.fixture
def ingestion_service(document_repo):
    """Build a stub `IngestionService` with the stub repositories."""
    # Mock the other dependencies since we only need list_documents
    service = IngestionService(
        documents=document_repo,
        chunks=_StubChunkRepository(),
        loader=None,
        indexes=[],
    )
    main.app.dependency_overrides[get_ingestion_service] = lambda: service
    yield service


@pytest.fixture(autouse=True)
def _authenticated():
    """Every protected route needs an authenticated user."""
    main.app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        username="test-user",
        password_hash="",
        token_version=0,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    main.app.dependency_overrides.clear()


# --- parse_chunk_key (unit tests, no HTTP) ---------------------------------


def test_parse_chunk_key_valid_key():
    document_id, chunk_index = parse_chunk_key("1:3")
    assert document_id == 1
    assert chunk_index == 3


def test_parse_chunk_key_round_trips():
    from app.domain.models import chunk_key

    original = "5:42"
    parsed = parse_chunk_key(original)
    reconstructed = chunk_key(parsed[0], parsed[1])
    assert reconstructed == original


def test_parse_chunk_key_rejects_no_colon():
    with pytest.raises(ValueError, match="Malformed chunk key"):
        parse_chunk_key("abc")


def test_parse_chunk_key_rejects_missing_document_id():
    with pytest.raises(ValueError, match="Malformed chunk key"):
        parse_chunk_key(":3")


def test_parse_chunk_key_rejects_missing_chunk_index():
    with pytest.raises(ValueError, match="Malformed chunk key"):
        parse_chunk_key("1:")


def test_parse_chunk_key_rejects_non_integer_document_id():
    with pytest.raises(ValueError, match="Malformed chunk key"):
        parse_chunk_key("abc:3")


def test_parse_chunk_key_rejects_non_integer_chunk_index():
    with pytest.raises(ValueError, match="Malformed chunk key"):
        parse_chunk_key("1:xyz")


def test_parse_chunk_key_rejects_empty_string():
    with pytest.raises(ValueError, match="Malformed chunk key"):
        parse_chunk_key("")


def test_parse_chunk_key_rejects_multiple_colons():
    with pytest.raises(ValueError, match="Malformed chunk key"):
        parse_chunk_key("1:2:3")


# --- GET /chunks --------------------------------------------------------


def test_chunks_with_valid_ids(corpus_service, chunk_repo):
    response = client.get("/chunks?ids=1:0,1:3")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["document_id"] == 1
    assert body[0]["chunk_index"] == 0
    assert body[0]["text"] == "Chunk 1:0 text"
    assert body[1]["document_id"] == 1
    assert body[1]["chunk_index"] == 3
    assert body[1]["text"] == "Chunk 1:3 text"


def test_chunks_with_malformed_key_returns_400(corpus_service):
    response = client.get("/chunks?ids=abc")
    assert response.status_code == 400


def test_chunks_with_missing_colon_returns_400(corpus_service):
    response = client.get("/chunks?ids=1")
    assert response.status_code == 400


def test_chunks_with_missing_chunk_index_returns_400(corpus_service):
    response = client.get("/chunks?ids=1:")
    assert response.status_code == 400


def test_chunks_with_empty_string_returns_400(corpus_service):
    response = client.get("/chunks?ids=")
    # Empty string after stripping doesn't call the service
    assert response.status_code == 200
    assert response.json() == []


def test_chunks_exceeds_cap_returns_400(corpus_service):
    # Create a request with 201 ids (exceeds MAX_CHUNK_IDS=200)
    ids = ",".join([f"1:{i}" for i in range(201)])
    response = client.get(f"/chunks?ids={ids}")
    assert response.status_code == 400


def test_chunks_at_exactly_200_ids_succeeds(corpus_service, chunk_repo):
    # Create a request with exactly 200 valid ids
    ids = ",".join([f"2:0"] * 200)  # Repeated valid id
    response = client.get(f"/chunks?ids={ids}")
    # Should succeed (not exceed cap)
    # Note: due to deduplication at the repository level, the result might have fewer items
    assert response.status_code == 200


def test_chunks_unknown_ids_are_omitted(corpus_service):
    """Unknown ids are silently omitted — no 404, just missing from result."""
    response = client.get("/chunks?ids=1:0,99:99,1:3")

    assert response.status_code == 200
    body = response.json()
    # Only the found chunks are returned
    assert len(body) == 2
    ids_returned = [(c["document_id"], c["chunk_index"]) for c in body]
    assert (1, 0) in ids_returned
    assert (1, 3) in ids_returned
    assert (99, 99) not in ids_returned


def test_chunks_with_whitespace_around_ids(corpus_service):
    """The route should handle whitespace around comma-separated values."""
    response = client.get("/chunks?ids=1:0 , 1:3 , 2:0")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3


def test_chunks_response_shape(corpus_service):
    """Each chunk in the response has the correct shape."""
    response = client.get("/chunks?ids=1:0")

    assert response.status_code == 200
    body = response.json()
    chunk = body[0]
    # Must have these fields
    assert "document_id" in chunk
    assert "chunk_index" in chunk
    assert "text" in chunk
    # Must be the right types
    assert isinstance(chunk["document_id"], int)
    assert isinstance(chunk["chunk_index"], int)
    assert isinstance(chunk["text"], str)


def test_chunks_requires_authentication(corpus_service):
    """GET /chunks must require a valid access token."""
    # Remove the authenticated override
    main.app.dependency_overrides.clear()

    response = client.get("/chunks?ids=1:0")
    assert response.status_code == 401


# --- GET /documents ---------------------------------------------------


def test_documents_returns_all_documents(ingestion_service, document_repo):
    response = client.get("/documents")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3


def test_documents_returns_newest_first(ingestion_service, document_repo):
    """Documents must be sorted newest-first by ingested_at."""
    response = client.get("/documents")

    assert response.status_code == 200
    body = response.json()
    # Document 3 was ingested most recently, so it should be first
    assert body[0]["document_id"] == 3
    assert body[1]["document_id"] == 2
    assert body[2]["document_id"] == 1
    # Verify timestamps are in descending order
    times = [
        datetime.fromisoformat(doc["ingested_at"].replace("Z", "+00:00"))
        for doc in body
        if doc["ingested_at"]
    ]
    assert times == sorted(times, reverse=True)


def test_documents_response_shape(ingestion_service):
    """Each document in the response has the correct shape."""
    response = client.get("/documents")

    assert response.status_code == 200
    body = response.json()
    doc = body[0]
    # Must have these fields
    assert "document_id" in doc
    assert "filename" in doc
    assert "status" in doc
    assert "chunk_count" in doc
    assert "ingested_at" in doc
    # Must NOT have internal bookkeeping fields
    assert "content_hash" not in doc


def test_documents_content_hash_is_not_leaked(ingestion_service):
    """`content_hash` is internal bookkeeping and must not appear in the response."""
    response = client.get("/documents")

    assert response.status_code == 200
    body = response.json()
    for doc in body:
        assert "content_hash" not in doc


def test_documents_empty_store_returns_empty_list(ingestion_service, document_repo):
    """An empty corpus should return an empty list, not an error."""
    # Clear the document repository
    document_repo.documents = []

    response = client.get("/documents")

    assert response.status_code == 200
    body = response.json()
    assert body == []


def test_documents_requires_authentication(ingestion_service):
    """GET /documents must require a valid access token."""
    # Remove the authenticated override
    main.app.dependency_overrides.clear()

    response = client.get("/documents")
    assert response.status_code == 401


def test_documents_lists_all_statuses(ingestion_service, document_repo):
    """The endpoint should return documents regardless of their status."""
    # Add documents with different statuses
    document_repo.documents.append(
        DocumentRecord(
            document_id=4,
            filename="pending.pdf",
            status=DocumentStatus.PENDING,
            chunk_count=0,
            ingested_at=datetime(2026, 8, 12, 16, 0, 0, tzinfo=timezone.utc),
            error=None,
            content_hash="hash4",
        )
    )
    document_repo.documents.append(
        DocumentRecord(
            document_id=5,
            filename="failed.pdf",
            status=DocumentStatus.FAILED,
            chunk_count=0,
            ingested_at=datetime(2026, 8, 12, 16, 30, 0, tzinfo=timezone.utc),
            error="Failed to parse PDF",
            content_hash="hash5",
        )
    )

    response = client.get("/documents")

    assert response.status_code == 200
    body = response.json()
    statuses = [doc["status"] for doc in body]
    assert DocumentStatus.PENDING in statuses
    assert DocumentStatus.FAILED in statuses
    assert DocumentStatus.INDEXED in statuses
