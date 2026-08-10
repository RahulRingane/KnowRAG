"""Operational endpoints: `/`, `/health`, `/metrics`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse, Response

from app.api.dependencies import get_health_service
from app.core.observability import render_metrics
from app.services.health_service import HealthService

router = APIRouter(tags=["system"])


@router.get("/")
def root():
    return {"message": "KnowRAG API is running"}


@router.get("/metrics")
def metrics():
    """Prometheus exposition of §9's six metrics.

    Deliberately unauthenticated and outside `/health`: scrapers expect a
    plain endpoint, and the payload carries only counters and latency
    histograms — no question text, no chunk contents.
    """
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@router.get("/health")
def health(service: HealthService = Depends(get_health_service)):
    """Extended per §7.2: actually connects to all three datastores.

    Returns 503 when any dependency is unreachable so the compose
    healthcheck (and any load balancer in front of this) sees a real
    signal. The body always carries the full per-datastore breakdown,
    healthy or not.
    """
    all_ok, body = service.report()

    if not all_ok:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body)

    return body
