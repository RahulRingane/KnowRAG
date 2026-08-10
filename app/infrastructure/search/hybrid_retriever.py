"""Hybrid retrieval: two `Retriever`s, merged and reranked.

From the old `app/retriver.py` (the filename typo is retired along with the
module). The merge and rerank logic is unchanged; what changed is that this
class no longer *is* the Qdrant and Elasticsearch clients — it composes two
things that satisfy `Retriever` and does not know or care what they are.

That composition is what makes the whole thing testable: the unit tests
construct `HybridRetriever(semantic=<list-returning stub>, keyword=<stub>,
reranker=<scripted stub>)` and exercise the dedup and ordering rules with no
datastore and no model, which previously required monkeypatching three
module globals.

`HybridRetriever` satisfies `Retriever` itself, so the query service holds
one and cannot tell a hybrid retriever from a plain one.
"""

from __future__ import annotations

from app.core import timing
from app.core.config import settings
from app.core.observability import get_logger, observe, retrieval_latency_seconds
from app.domain.models import Chunk, chunk_key
from app.domain.ports import Reranker, Retriever

logger = get_logger(__name__)


class HybridRetriever:
    """Union of a semantic and a keyword retriever, cross-encoder reranked."""

    def __init__(
        self,
        semantic: Retriever,
        keyword: Retriever,
        reranker: Reranker | None = None,
        candidate_k: int | None = None,
    ):
        self._semantic = semantic
        self._keyword = keyword
        # Accepted as `None` and resolved on first use so that constructing a
        # retriever — which the API does at startup, per request — never
        # triggers the cross-encoder load. §10 wants that model loaded once by
        # the warmup path, not opportunistically by whoever wires the graph.
        self._reranker = reranker
        self._candidate_k = candidate_k or settings.top_k_retrieval

    def _get_reranker(self) -> Reranker:
        if self._reranker is None:
            from app.infrastructure.ml.embeddings import get_reranker

            self._reranker = get_reranker()
        return self._reranker

    def search(self, query: str, k: int = 0) -> list[Chunk]:
        k = k or settings.top_k_rerank

        # Timed separately because they answer different questions when this
        # gets slow: the semantic leg embeds the query with a local model, the
        # keyword leg is a network round-trip to Elasticsearch. One number over
        # both cannot tell a slow model from a slow datastore.
        with timing.timed("search_semantic_ms"):
            semantic_docs = self._semantic.search(query, self._candidate_k)
        with timing.timed("search_keyword_ms"):
            keyword_docs = self._keyword.search(query, self._candidate_k)

        candidates: dict[tuple[int, int], Chunk] = {}

        for doc in semantic_docs + keyword_docs:
            key = (doc.document_id, doc.chunk_index)

            # keep first occurrence
            if key not in candidates:
                candidates[key] = doc

        docs = list(candidates.values())

        if not docs:
            logger.warning("hybrid_search_empty", k=k)
            return []

        # Resolved *before* the timed span, not inside it. On a cold process
        # `_get_reranker()` loads a cross-encoder, and that load already reports
        # itself as `model_load_ms`; timing it here too would have it counted
        # under both keys, which breaks the one invariant the breakdown rests on
        # — that the keys are disjoint and sum to the wall time. Measured on the
        # first run after this was instrumented: a 6.8s reranker load turned a
        # 24s query into a reported 38.9s. `rerank_ms` is the forward pass and
        # nothing else.
        reranker = self._get_reranker()

        # `rerank_ms` is reported as its own stage rather than folded into
        # retrieval: it is a cross-encoder forward pass over every candidate,
        # so it scales with `top_k_retrieval` while the two searches do not.
        # Reported to the caller by `app.services.query_service`, which
        # subtracts it back out of `retrieval_ms` so the two stay disjoint.
        with observe(retrieval_latency_seconds, path="hybrid"), timing.timed("rerank_ms"):
            scores = reranker.predict([(query, doc.text) for doc in docs])

            for doc, score in zip(docs, scores):
                doc.rerank_score = float(score)

            docs.sort(key=lambda x: x.rerank_score, reverse=True)

        results = docs[:k]

        # §9 asks for rerank scores specifically, and this is why: when an
        # answer is wrong, the first question is whether the right chunk was
        # retrieved and ranked away. Logging the kept scores alongside the
        # candidate count answers that without a re-run.
        # The two legs' timings are logged rather than returned. They overlap
        # `retrieval_ms` (they are what it is made of), so putting them on
        # `latency_ms` would break its disjointness; here they answer "which
        # leg" for anyone reading a slow retrieval, which is what they are for.
        # `search_semantic_ms` carries the embedding model's cold load on a
        # first call — see `model_load_ms` on the response.
        stage_costs = timing.snapshot()

        logger.info(
            "hybrid_search",
            semantic_ms=round(stage_costs.get("search_semantic_ms", 0.0), 1),
            keyword_ms=round(stage_costs.get("search_keyword_ms", 0.0), 1),
            rerank_ms=round(stage_costs.get("rerank_ms", 0.0), 1),
            semantic_count=len(semantic_docs),
            keyword_count=len(keyword_docs),
            candidates=len(docs),
            deduped=len(semantic_docs) + len(keyword_docs) - len(docs),
            returned=len(results),
            rerank_scores=[round(d.rerank_score, 4) for d in results],
            chunk_ids=[chunk_key(d.document_id, d.chunk_index) for d in results],
        )
        return results


def build_default_retriever() -> HybridRetriever:
    """The production wiring: Qdrant + Elasticsearch + the cross-encoder.

    Kept here rather than in `app.api.dependencies` so the CLI and the eval
    scripts get the identical graph without importing anything web-related.
    """
    from app.infrastructure.search.elasticsearch_index import ElasticsearchIndex
    from app.infrastructure.search.qdrant_index import QdrantIndex

    return HybridRetriever(semantic=QdrantIndex(), keyword=ElasticsearchIndex())
