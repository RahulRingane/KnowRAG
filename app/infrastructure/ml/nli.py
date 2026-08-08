"""The NLI model behind `EntailmentScorer` — the only torch in verification.

Extracted from the old `app/verify.py`, which held this model *and* §6.2's
decision logic in one module. That coupling is why the verification tests
had to monkeypatch a module global to stay offline. The rules now live in
`app.domain.verification` and take a scorer; this module supplies the real
one.

Per §6.1 the model is a genuine NLI checkpoint, **not** the retrieval
cross-encoder in `embeddings.py`. That one is trained on relevance/ranking
signal — "is this chunk related to the query" — which is a different
question from "does this chunk entail this claim." Conflating the two is
exactly the mistake §6.1 warns against, which is why they are separate
settings and separate loaders.
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.domain.ports import NLILabel

logger = logging.getLogger(__name__)

# Label order is a property of the checkpoint and NOTHING ELSE. It was
# previously a hardcoded ("contradiction", "entailment", "neutral") described
# as "the standard order" — it is not standard. Three checkpoints measured for
# Direction D used three different orders:
#
#   cross-encoder/nli-deberta-v3-base             (contradiction, entailment, neutral)
#   MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli  (entailment, neutral, contradiction)
#   facebook/bart-large-mnli                      (contradiction, neutral, entailment)
#
# A constant that disagrees with the loaded weights does not fail — it silently
# reports entailment as contradiction, and every verdict in the system inverts
# while the tests (which substitute the scorer) stay green. So the order is
# read from the checkpoint's own `id2label` at load time and never assumed.
_nli_model = None  # lazy-cached singleton
_nli_labels: tuple[str, ...] | None = None


def _derive_labels(model) -> tuple[str, ...]:
    """Map the checkpoint's `id2label` onto KnowRAG's three NLI labels.

    Matches on substring because checkpoints spell them differently
    (`ENTAILMENT`, `entailment`, `LABEL_0` is rejected outright). Raises rather
    than guessing: an unrecognised head is a model this verifier cannot
    interpret, and defaulting to a positional guess is how the silent inversion
    above happens.
    """
    id2label = model.model.config.id2label
    labels: list[str] = []
    for index in range(len(id2label)):
        raw = str(id2label[index]).lower()
        if "entail" in raw:
            labels.append("entailment")
        elif "contra" in raw:
            labels.append("contradiction")
        elif "neutral" in raw:
            labels.append("neutral")
        else:
            raise RuntimeError(
                f"{settings.nli_model} exposes label {id2label[index]!r} at index {index}, "
                "which is not one of entailment/neutral/contradiction. Refusing to "
                "guess the order — every verdict depends on it."
            )
    if set(labels) != {"entailment", "neutral", "contradiction"}:
        raise RuntimeError(f"{settings.nli_model} does not expose a 3-way NLI head: {labels}")
    return tuple(labels)


def get_nli_model():
    """Lazily load and cache the NLI cross-encoder.

    Loading a ~400MB transformer model at import time would make importing
    this module slow and would download the model even for callers that only
    want the pure verdict-assembly logic. Deferring the import of
    `sentence_transformers` itself into this function means a test process
    that never calls `get_nli_model()`/`predict_nli()` doesn't need the
    dependency installed at all.
    """
    global _nli_model, _nli_labels
    if _nli_model is None:
        import torch
        from sentence_transformers import CrossEncoder

        logger.info("Loading NLI model %s", settings.nli_model)
        model = CrossEncoder(settings.nli_model)

        # This checkpoint is published in float16. x86 has no native fp16
        # compute, so torch emulates it: measured 3112ms per call versus 525ms
        # for the identical weights in fp32, producing byte-identical scores
        # (0.943/0.946/0.570 either way). Without this cast the model is ~6x
        # slower than the one it replaces for no accuracy gain whatsoever.
        if next(model.model.parameters()).dtype != torch.float32:
            logger.info("Casting %s to float32 (fp16 is emulated on CPU)", settings.nli_model)
            model.model.to(torch.float32)

        _nli_labels = _derive_labels(model)
        logger.info("NLI label order for %s: %s", settings.nli_model, _nli_labels)
        _nli_model = model
    return _nli_model


def predict_nli(premise: str, hypothesis: str) -> tuple[NLILabel, float]:
    """Score (premise, hypothesis) -> (label, confidence).

    `premise` is the retrieved chunk text, `hypothesis` is the claim text —
    matching §6.2's `nli_model.predict(premise=chunk.text,
    hypothesis=claim.text)` call exactly. `confidence` is the softmax
    probability of the returned label, in [0, 1], comparable against
    `settings.claim_verification_threshold`.

    This function *is* the `EntailmentScorer` port: its signature is the
    port's signature, so it is passed to `ClaimVerifier` directly with no
    adapter class in between.
    """
    model = get_nli_model()
    # apply_softmax=True turns the model's raw logits into a probability
    # distribution over the three NLI classes so the returned score is
    # directly comparable to `settings.claim_verification_threshold`. The
    # class *order* comes from the checkpoint (see `_derive_labels`), which
    # `get_nli_model()` has populated by the time we get here.
    (probs,) = model.predict([(premise, hypothesis)], apply_softmax=True)
    idx = int(probs.argmax())
    assert _nli_labels is not None  # set by get_nli_model() above
    return _nli_labels[idx], float(probs[idx])
