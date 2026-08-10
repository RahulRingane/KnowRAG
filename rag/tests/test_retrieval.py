"""§8.1 area 1 — chunk dedup logic in `hybrid_search`.

Qdrant, Elasticsearch and the cross-encoder are all replaced with stubs, so
these run offline in milliseconds and assert the *merge* behavior rather
than retrieval quality (that is `eval/run_retrieval_eval.py`'s job).

The property under test is the one that silently corrupts everything
downstream if it breaks: a chunk returned by both retrieval paths must
appear exactly once in the candidate set. A duplicate survives dedup, gets
its own `[Cn]` citation tag in `format_context`, and the generator can then
cite the same evidence twice as if it were two independent sources.
"""

from __future__ import annotations

import pytest

from app.domain.models import Chunk
from app.infrastructure.search.hybrid_retriever import HybridRetriever


def _chunk(doc: int, idx: int, source: str = "qdrant", score: float = 1.0, text: str | None = None):
    return Chunk(
        source=source,
        score=score,
        document_id=doc,
        chunk_index=idx,
        text=text if text is not None else f"text for {doc}:{idx}",
    )


class _StubReranker:
    """Scores by position in a caller-supplied table, so tests control order."""

    def __init__(self, scores_by_text: dict[str, float]):
        self.scores_by_text = scores_by_text
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs):
        self.calls.append(list(pairs))
        return [self.scores_by_text.get(text, 0.0) for _query, text in pairs]


class _StubRetriever:
    """Returns a fixed result list, ignoring the query."""

    def __init__(self, results: list[Chunk]):
        self.results = results

    def search(self, query: str, k: int) -> list[Chunk]:
        return list(self.results)


class _Harness:
    """Builds a `HybridRetriever` over stubs, then searches through it.

    Constructor injection rather than monkeypatching: `HybridRetriever`
    composes two things satisfying `Retriever` plus a `Reranker`, so a test
    supplies all three directly and no module global is touched.
    """

    def __init__(self):
        self.retriever: HybridRetriever | None = None

    def __call__(self, semantic: list[Chunk], keyword: list[Chunk], scores: dict[str, float]):
        reranker = _StubReranker(scores)
        self.retriever = HybridRetriever(
            semantic=_StubRetriever(semantic),
            keyword=_StubRetriever(keyword),
            reranker=reranker,
        )
        return reranker

    def search(self, query: str, k: int) -> list[Chunk]:
        return self.retriever.search(query, k)


@pytest.fixture
def patch_paths():
    return _Harness()


def test_chunk_returned_by_both_paths_appears_once(patch_paths):
    shared = (1, 7)
    semantic = [_chunk(*shared, source="qdrant"), _chunk(1, 8)]
    keyword = [_chunk(*shared, source="elastic"), _chunk(1, 9)]

    reranker = patch_paths(semantic, keyword, {})
    results = patch_paths.search("q", k=10)

    keys = [(c.document_id, c.chunk_index) for c in results]
    assert keys.count(shared) == 1
    assert len(keys) == len(set(keys)) == 3
    # The reranker must never be asked to score the duplicate either —
    # otherwise dedup is only cosmetic and the cost is still paid.
    assert len(reranker.calls[0]) == 3


def test_dedup_keeps_first_occurrence_so_semantic_wins(patch_paths):
    """Semantic results are merged first, so its copy is the one retained."""
    semantic = [_chunk(1, 7, source="qdrant", score=0.9)]
    keyword = [_chunk(1, 7, source="elastic", score=42.0)]

    patch_paths(semantic, keyword, {})
    (result,) = patch_paths.search("q", k=10)

    assert result.source == "qdrant"
    assert result.score == 0.9


def test_same_chunk_index_in_different_documents_is_not_deduped(patch_paths):
    """Identity is the (document_id, chunk_index) pair, not chunk_index alone."""
    semantic = [_chunk(1, 3), _chunk(2, 3)]
    patch_paths(semantic, [], {})

    results = patch_paths.search("q", k=10)

    assert sorted((c.document_id, c.chunk_index) for c in results) == [(1, 3), (2, 3)]


def test_results_are_ordered_by_rerank_score_descending(patch_paths):
    semantic = [_chunk(1, 1, text="low"), _chunk(1, 2, text="high"), _chunk(1, 3, text="mid")]
    patch_paths(semantic, [], {"low": -3.0, "mid": 0.5, "high": 8.0})

    results = patch_paths.search("q", k=10)

    assert [c.text for c in results] == ["high", "mid", "low"]
    assert [c.rerank_score for c in results] == [8.0, 0.5, -3.0]


def test_result_is_truncated_to_k_after_reranking_not_before(patch_paths):
    """Truncation must happen after the rerank, or the best chunk can be cut."""
    semantic = [_chunk(1, 1, text="worst"), _chunk(1, 2, text="best")]
    patch_paths(semantic, [], {"worst": -1.0, "best": 9.0})

    results = patch_paths.search("q", k=1)

    assert len(results) == 1
    assert results[0].text == "best"


def test_no_candidates_returns_empty_without_calling_reranker(patch_paths):
    reranker = patch_paths([], [], {})

    assert patch_paths.search("q", k=5) == []
    assert reranker.calls == []


def test_returned_objects_satisfy_the_chunk_contract(patch_paths):
    patch_paths([_chunk(1, 1)], [], {"text for 1:1": 1.0})

    (result,) = patch_paths.search("q", k=5)

    assert isinstance(result, Chunk)
    assert Chunk(**result.model_dump()) == result
