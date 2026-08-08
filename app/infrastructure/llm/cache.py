"""Dev-only on-disk generation cache (§12.1).

Split out of the old `app/llm.py`. Keyed on `(question, retrieved_chunk_ids,
model, prompt_fingerprint)` so the faithfulness/adversarial eval sets
(§8.3-8.4) don't re-spend free-tier daily quota every time they're re-run
against unchanged retrieval output.

Provider-agnostic by construction: it stores the validated claim dict, which
is the one thing both providers agree on.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

# Imported as a module, not `from ... import SYSTEM_PROMPT`: the fingerprint
# below must reflect the prompt as it is *at call time*. A bound name would
# freeze whatever the prompt was when this module was first imported, which
# is precisely the staleness the fingerprint exists to detect.
from app.infrastructure.llm import prompts

logger = logging.getLogger(__name__)

# Repo root / .cache / llm — four parents up from
# app/infrastructure/llm/cache.py, where the old app/llm.py needed two.
_CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache" / "llm"

# Flip off to force live calls regardless of what's cached. Also overridable
# per call via `generate_claims(..., use_cache=False)`.
CACHE_ENABLED = True

_TAG_RE = re.compile(r"\(doc:\s*([^,]+),\s*chunk:\s*([^)]+)\)")


def chunk_ids_from_context(context_block: str) -> list[str]:
    """Fallback cache-key material when a caller doesn't pass `chunk_ids`
    explicitly: pull the `(doc: X, chunk: Y)` tags `format_context` (in
    `app.domain.context`) emits straight out of the block text, so the cache
    still keys on chunk identity rather than raw text. If the block doesn't
    contain that pattern (format changed, or a caller passed something
    else), fall back to hashing the whole block — caching then degrades to
    per-context-text granularity instead of breaking.
    """
    pairs = _TAG_RE.findall(context_block)
    if pairs:
        return [f"{doc.strip()}:{idx.strip()}" for doc, idx in pairs]
    return [hashlib.sha256(context_block.encode("utf-8")).hexdigest()]


def _prompt_fingerprint() -> str:
    """Short digest of the system prompt, for the cache key.

    Without this the cache is keyed on (question, chunks, model) only, so
    editing `SYSTEM_PROMPT` leaves every entry looking valid and the next eval
    run silently replays generations produced by the *previous* prompt. A
    prompt change would then measure as "no effect" — the most convincing way
    to get a wrong answer, since nothing errors and the numbers look stable.

    Any edit to the prompt therefore invalidates the whole cache, which is the
    correct behaviour: those responses are no longer reachable from the code.
    """
    return hashlib.sha256(prompts.SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16]


def cache_key(question: str, chunk_ids: list[str], model: str) -> str:
    payload = json.dumps(
        {
            "question": question,
            "chunk_ids": sorted(chunk_ids),
            "model": model,
            "prompt": _prompt_fingerprint(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return _CACHE_DIR / f"{key}.json"


def load(key: str) -> dict | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Ignoring unreadable llm cache entry at %s", path, exc_info=True)
        return None


def store(key: str, value: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(json.dumps(value), encoding="utf-8")
    except OSError:
        logger.warning("Failed to write llm cache entry", exc_info=True)
