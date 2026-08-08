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

        semantic_docs = self._semantic.search(query, self._candidate_k)
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

        with observe(retrieval_latency_seconds, path="hybrid"):
            scores = self._get_reranker().predict([(query, doc.text) for doc in docs])

            for doc, score in zip(docs, scores):
                doc.rerank_score = float(score)

            docs.sort(key=lambda x: x.rerank_score, reverse=True)

        results = docs[:k]

        # §9 asks for rerank scores specifically, and this is why: when an
        # answer is wrong, the first question is whether the right chunk was
        # retrieved and ranked away. Logging the kept scores alongside the
        # candidate count answers that without a re-run.
        logger.info(
            "hybrid_search",
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
