"""§7's ingestion routes: `POST /ingest` and `GET /ingest/{document_id}`."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile, status

from app.api.dependencies import get_ingestion_service
from app.api.dto import IngestAccepted, IngestStatus
from app.core.exceptions import UnsupportedUpload
from app.domain.models import DocumentStatus
from app.services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED, response_model=IngestAccepted)
async def ingest(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    service: IngestionService = Depends(get_ingestion_service),
):
    """Accept a PDF and index it in the background.

    Returns `202 Accepted` with a `document_id` from day one, per §7.1 —
    the caller polls `GET /ingest/{document_id}` for the outcome. That
    contract is what makes the eventual swap from `BackgroundTasks` to a
    real queue invisible to callers, so it holds even though the current
    implementation would happily run small files inline.
    """
    if not file.filename:
        raise UnsupportedUpload("No filename provided.")

    # `Path(...).name` strips any directory component: a multipart filename
    # is attacker-controlled and is about to be used to build a path.
    safe_name = Path(file.filename).name
    if not safe_name.lower().endswith(".pdf"):
        raise UnsupportedUpload("Only PDF uploads are supported.")

    # The upload is staged under its original basename inside a private temp
    # directory, not under a randomized temp filename. Ingestion derives the
    # document's identity from `Path(path).name`, so a mangled name here would
    # create a second `documents` row divorced from the id this response is
    # about to hand back.
    tmp_dir = tempfile.mkdtemp(prefix="knowrag-ingest-")
    tmp_path = Path(tmp_dir) / safe_name

    try:
        with tmp_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    finally:
        await file.close()

    # Reserved synchronously, so the id in this response is durable and
    # immediately pollable before the background task has run at all.
    document_id = service.reserve(safe_name)

    background_tasks.add_task(_run_ingestion, service, str(tmp_path), document_id, tmp_dir)

    return IngestAccepted(
        document_id=document_id,
        filename=safe_name,
        status=DocumentStatus.PENDING,
    )


def _run_ingestion(
    service: IngestionService, path: str, document_id: int, tmp_dir: str
) -> None:
    """Background half of `POST /ingest`.

    The service already records `failed` (with the reason) on the document
    row before re-raising, so the exception is caught and logged here rather
    than propagating out of a background task where nothing would report it
    to the caller.
    """
    try:
        service.ingest(path, document_id=document_id)
    except Exception:
        logger.exception("Background ingestion failed for document_id=%s", document_id)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/ingest/{document_id}", response_model=IngestStatus)
def ingest_status(
    document_id: int,
    service: IngestionService = Depends(get_ingestion_service),
):
    """Report `pending` | `indexed` | `failed` for a previously accepted upload.

    A missing id raises `DocumentNotFound`, which `app.api.errors` turns into
    a 404 — the route itself does no error mapping.
    """
    return IngestStatus.from_record(service.get_status(document_id))
