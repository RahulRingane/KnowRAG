"""The corpus-inspection use case — read-only access to text already ingested.

Added for the frontend's evidence panel (WS-0, frontend_plan.md §2):
`FactCheckedResponse.retrieved_chunk_ids` and `ClaimVerdict.chunk_ids` carry
canonical `"documentId:chunkIndex"` keys (`app.domain.models.chunk_key()`)
and nothing else, so a caller wanting to show someone *what a citation says*
had no way to turn that key back into text. `CorpusService` is that read
path.

Kept separate from `IngestionService` rather than added to it: "what text
does this citation point at" and "put this document into the corpus" are
different concerns that happen to share a repository, and folding the former
into the latter would give `IngestionService` a second responsibility it
does not need in order to answer either question.
"""

from __future__ import annotations

from app.core.exceptions import InvalidChunkSelection
from app.domain.models import StoredChunk, parse_chunk_key
from app.domain.ports import ChunkRepository

# A citation panel wants a handful of chunks, not the corpus. This bounds
# `GET /chunks?ids=...` so a hand-built or buggy client cannot turn a
# citation lookup into an unbounded query — see `get_chunks()`.
MAX_CHUNK_IDS = 200


class CorpusService:
    """Read-only chunk-text lookups by canonical key."""

    def __init__(self, chunks: ChunkRepository):
        self._chunks = chunks

    def get_chunks(self, ids: list[str]) -> list[StoredChunk]:
        """Resolve canonical `"documentId:chunkIndex"` keys to their text.

        Two different things can go wrong with `ids`, and they are handled
        differently on purpose. A key nothing in the corpus matches is not an
        error — a citation list built from a response that has gone slightly
        stale (a document re-ingested, a chunk boundary shifted since the
        answer was generated) should degrade to "some evidence missing", not
        fail the whole panel, so unknown keys are simply absent from the
        result. A key the parser cannot make sense of, or a request over
        `MAX_CHUNK_IDS`, is a caller bug rather than corpus state, and raises
        `InvalidChunkSelection` — which `app.api.errors` turns into a 400.
        """
        if len(ids) > MAX_CHUNK_IDS:
            raise InvalidChunkSelection(
                f"Requested {len(ids)} chunk ids, which exceeds the cap of {MAX_CHUNK_IDS}."
            )

        keys: list[tuple[int, int]] = []
        for raw in ids:
            try:
                keys.append(parse_chunk_key(raw))
            except ValueError as exc:
                raise InvalidChunkSelection(str(exc)) from exc

        return self._chunks.get_by_keys(keys)


def build_default_corpus_service() -> CorpusService:
    """The production object graph, in one place. See `IngestionService`'s twin."""
    from app.infrastructure.db.chunk_repository import SqlChunkRepository

    return CorpusService(chunks=SqlChunkRepository())
