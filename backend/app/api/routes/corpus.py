"""`GET /chunks` — resolve citation keys to the passage text they point at.

Added for the frontend's evidence panel (WS-0, frontend_plan.md §2): every
existing response carries `chunk_ids` like `"1:3"` and there was previously
no route that turned one back into text a person could read.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_corpus_service, get_current_user
from app.api.dto import ChunkText
from app.domain.models import User
from app.services.corpus_service import CorpusService

router = APIRouter(tags=["corpus"])


@router.get("/chunks", response_model=list[ChunkText])
def chunks(
    ids: str = Query(
        ...,
        description='Comma-separated canonical chunk keys, e.g. "1:3,1:5".',
    ),
    service: CorpusService = Depends(get_corpus_service),
    _user: User = Depends(get_current_user),
):
    """Resolve `ids` to the text of every chunk the corpus still has.

    Splitting the query string on commas is the only work this body does
    before handing off — it is HTTP-shape decoding, not business logic.
    Everything that *is* business logic (parsing each key with the canonical
    `parse_chunk_key()`, the cap on how many ids one request may ask for, and
    what happens to a key nothing matches) lives in
    `CorpusService.get_chunks()`. A malformed key or an over-cap request
    raises `InvalidChunkSelection`, which `app.api.errors` maps to 400 — this
    route body has no try/except.
    """
    keys = [part.strip() for part in ids.split(",") if part.strip()]
    return [
        ChunkText(document_id=chunk.document_id, chunk_index=chunk.chunk_index, text=chunk.text)
        for chunk in service.get_chunks(keys)
    ]
