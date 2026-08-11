"""Command-line entrypoints.

Gathered here so no infrastructure or domain module carries an `argparse`
block or an `if __name__ == "__main__"`. The old layout had five of them
scattered across `pg.py`, `es.py`, `vdb.py`, `retriver.py`, `ingest.py`,
`chain.py` and `llm.py`, which is why importing a storage module pulled in
argument parsing.

    python -m app.cli.ingest   data.pdf      ingest + index (what POST /ingest runs)
    python -m app.cli.index    --full-reindex   re-index existing chunks only
    python -m app.cli.retrieve                interactive hybrid search
    python -m app.cli.query                   interactive full pipeline

Every one of these drives the same services the API drives — §7.2's
"exactly one code path for ingestion and one for retrieval, exercised by
both the CLI and the API".
"""

from __future__ import annotations

import logging
import os
import warnings


def quiet_third_party() -> None:
    """Suppress dependency chatter that `configure_logging` cannot reach.

    Three sources, each escaping the logging configuration a different way:

    - **tqdm progress bars.** `sentence-transformers` renders a "Loading
      weights" bar per model, straight to stderr. Three models means three
      bars before a single word of output. Silenced by env var, which is why
      this must run before those libraries are imported.
    - **`warnings.warn`.** `qdrant-client` warns about an API key over plain
      HTTP on every construction, which for a local compose stack is
      expected and unactionable.
    - **`huggingface_hub`'s own logger.** It attaches a handler to its own
      logger rather than propagating to the root, so neither the root level
      nor our handler's level applies. Its level has to be set by name.

    Called only by the CLI entrypoints, and only when not running verbosely —
    the server keeps every warning, where an operator wants them.
    """
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    os.environ.setdefault("TQDM_DISABLE", "1")

    warnings.filterwarnings("ignore")

    for noisy in ("huggingface_hub", "transformers", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
