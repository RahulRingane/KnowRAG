"""§7's query routes: `POST /query` and `POST /query/stream`.

Both bodies are orchestration only — one call into `QueryService` and one
serialization decision. No retrieval, no prompt construction, no
verification logic, and no exception-to-status mapping (that is
`app.api.errors`).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_current_user, get_query_service
from app.api.dto import QueryRequest
from app.domain.models import FactCheckedResponse, User
from app.services.query_service import QueryService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["query"])


def _sse(event: str, data: dict) -> str:
    """One Server-Sent Event frame.

    `default=str` covers datetimes and any other non-JSON-native value that
    reaches here from a model dump — a serialization error mid-stream would
    truncate the response with no way to signal why.
    """
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.post("/query", response_model=FactCheckedResponse)
def query(
    request: QueryRequest,
    service: QueryService = Depends(get_query_service),
    _user: User = Depends(get_current_user),
):
    """Classify the input, run the route it belongs to, return §6.4's contract.

    The route body does not classify or branch — `QueryService.run()` does
    both, which is what keeps this endpoint and `python -m app.cli.query` on
    one code path (§7.2). A question is answered from the corpus; a statement
    is verified against it and comes back with a single verdict on the
    caller's own sentence. Which happened is on `input_type`, and the response
    shape is identical either way, so a client that predates routing is
    unaffected.

    Defined as `def`, not `async def`, so FastAPI runs it in a threadpool:
    retrieval, the cross-encoder rerank and the NLI verification pass are
    all blocking CPU work, and awaiting them on the event loop would stall
    every other request including `/health`.

    A question the corpus cannot answer comes back with
    `state="insufficient_evidence"` and every rejected claim listed in
    `rejected_claims` with its reason — that path is the reason this system
    exists, so it is a normal 200 response, not an error. A provider that is
    out of quota is a 503 instead; see `app.api.errors`.
    """
    return service.run(request.question)


@router.post("/query/stream")
def query_stream(
    request: QueryRequest,
    service: QueryService = Depends(get_query_service),
    _user: User = Depends(get_current_user),
):
    """SSE variant: generation deltas, then verification as a final event (§7).

    Event sequence is `retrieval` -> `token`* -> `verification` -> `done`,
    or `error` if generation fails partway. Verification is terminal
    because it needs the finished answer; nothing before the `verification`
    event has been fact-checked, and a client must not present `token`
    output as an answer.

    Routed identically to `POST /query`. An input classified as a statement
    emits zero `token` events — that route runs no generation — and goes
    straight from `retrieval` to `verification`, which is why `token` has
    always been documented as `*` and not `+`.
    """

    def event_stream():
        try:
            for name, payload in service.stream(request.question):
                yield _sse(name, payload)
        except Exception as exc:
            # The response has already begun, so the status code is long
            # since sent; an error event is the only way left to tell the
            # client that what it received is incomplete. This is also why
            # the exception handlers in `app.api.errors` cannot help here —
            # by the time this raises, there is no status line to set.
            logger.exception("Streaming query failed")
            yield _sse("error", {"detail": str(exc)})
            return
        yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
