"""§8.2 — recall@k for each retrieval path, measured separately.

Separately is the point. A single blended number hides the case this
system is most likely to regress into: hybrid search looks fine while one
of its two legs has quietly stopped contributing. Reporting semantic,
keyword and hybrid side by side makes that visible the moment it happens —
and it did happen here, when a Qdrant server pinned below the API version
its client used answered 404 to every semantic query while ingestion and
`/health` stayed green.

Items whose `expected_chunk_ids` could not be produced are **excluded and
reported**, never silently counted as misses. Scoring an unlabelable item
would understate retrieval for a reason that has nothing to do with
retrieval; see `eval/README.md` for why three items are currently in that
state.

Usage:

    python -m eval.run_retrieval_eval
    python -m eval.run_retrieval_eval --k 1 3 5 10 20
    python -m eval.run_retrieval_eval --min-recall 0.8 --at-k 5   # CI gate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.domain.models import chunk_key
from app.infrastructure.search.elasticsearch_index import ElasticsearchIndex
from app.infrastructure.search.hybrid_retriever import build_default_retriever
from app.infrastructure.search.qdrant_index import QdrantIndex

# The three retrieval paths this eval scores, each a `Retriever`. Built once
# here rather than per item: they share the Qdrant/ES clients and the model
# singletons, so constructing them repeatedly would buy nothing.
_semantic = QdrantIndex()
_keyword = ElasticsearchIndex()
_hybrid = build_default_retriever()


def semantic_search(query: str, k: int):
    return _semantic.search(query, k)


def keyword_search(query: str, k: int):
    return _keyword.search(query, k)


def hybrid_search(query: str, k: int):
    return _hybrid.search(query, k)

RETRIEVAL_SET = Path(__file__).resolve().parent / "retrieval_set.jsonl"


def load_items() -> tuple[list[dict], list[dict]]:
    """Return `(labeled, unlabeled)` items."""
    items = [json.loads(line) for line in RETRIEVAL_SET.read_text().splitlines() if line.strip()]
    labeled = [i for i in items if i.get("expected_chunk_ids")]
    unlabeled = [i for i in items if not i.get("expected_chunk_ids")]
    return labeled, unlabeled


def recall_at_k(retrieved: list[str], expected: list[str], k: int) -> float:
    """Fraction of the expected chunks that appear in the top `k`.

    Set-based rather than hit-or-miss, so an item whose answer legitimately
    spans two chunks is not scored as a full success when only one of them
    was found.
    """
    if not expected:
        return 0.0
    top = set(retrieved[:k])
    return len({e for e in expected if e in top}) / len(expected)


def run_path(search_fn, items: list[dict], max_k: int, ks: list[int]) -> dict[int, float]:
    """Score one retrieval path at every k, from a single search per item."""
    totals = {k: 0.0 for k in ks}

    for item in items:
        results = search_fn(item["question"], max_k)
        retrieved = [chunk_key(c.document_id, c.chunk_index) for c in results]
        for k in ks:
            totals[k] += recall_at_k(retrieved, item["expected_chunk_ids"], k)

    return {k: totals[k] / len(items) for k in ks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, nargs="+", default=[1, 3, 5, 10, 20], help="k values.")
    parser.add_argument(
        "--min-recall",
        type=float,
        default=None,
        help="Fail (exit 1) if hybrid recall at --at-k falls below this. For CI gating.",
    )
    parser.add_argument("--at-k", type=int, default=5, help="Which k --min-recall applies to.")
    args = parser.parse_args(argv)

    ks = sorted(set(args.k))
    max_k = max(ks)

    labeled, unlabeled = load_items()
    if not labeled:
        print("No labeled items. Run `python -m eval.map_chunk_ids --write` first.")
        return 1

    print(f"Scoring {len(labeled)} labeled items at k={ks}")
    if unlabeled:
        print(f"Excluded {len(unlabeled)} unlabeled: {[i['id'] for i in unlabeled]}")
    print()

    paths = {
        "semantic": semantic_search,
        "keyword": keyword_search,
        "hybrid": hybrid_search,
    }
    results = {name: run_path(fn, labeled, max_k, ks) for name, fn in paths.items()}

    width = max(len(n) for n in paths)
    print(f"{'path'.ljust(width)}  " + "  ".join(f"r@{k}".rjust(6) for k in ks))
    print("-" * (width + 2 + 8 * len(ks)))
    for name, scores in results.items():
        print(f"{name.ljust(width)}  " + "  ".join(f"{scores[k]:.3f}".rjust(6) for k in ks))
    print()

    # Worth stating plainly rather than leaving to the reader: hybrid is
    # supposed to be at least as good as its best leg. When it is not, the
    # rerank is actively discarding chunks the retrievers found.
    for k in ks:
        best_leg = max(results["semantic"][k], results["keyword"][k])
        if results["hybrid"][k] < best_leg:
            print(
                f"NOTE: at k={k}, hybrid ({results['hybrid'][k]:.3f}) is below the best "
                f"single path ({best_leg:.3f}) — the reranker is dropping found chunks."
            )

    if args.min_recall is not None:
        actual = results["hybrid"].get(args.at_k)
        if actual is None:
            print(f"--at-k {args.at_k} was not among the measured k values {ks}.")
            return 1
        if actual < args.min_recall:
            print(f"FAIL: hybrid recall@{args.at_k} {actual:.3f} < {args.min_recall:.3f}")
            return 1
        print(f"PASS: hybrid recall@{args.at_k} {actual:.3f} >= {args.min_recall:.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
