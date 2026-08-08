"""Local model weights: the embedder, the retrieval reranker, the NLI scorer.

Three separate models with three separate jobs. `embeddings.py` holds the two
retrieval-time ones; `nli.py` holds the verification one. They are kept apart
because §6.1 turns on their not being interchangeable — see `nli.py`.
"""

from app.infrastructure.ml.embeddings import get_embedding_model, get_reranker
from app.infrastructure.ml.nli import get_nli_model, predict_nli

__all__ = ["get_embedding_model", "get_nli_model", "get_reranker", "predict_nli"]
