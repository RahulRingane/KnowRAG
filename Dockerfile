# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System deps:
#   curl      — used by the container healthcheck below (see
#               docker-compose.yml's `app` service).
#   wamerican — /usr/share/dict/american-english, read by
#               app/domain/text_normalization.py's
#               `_english_words()`. Ingestion is *correct*
#               without it but not *identical*: the ambiguous U+E000
#               ligature and line-break hyphens both fall back to their
#               majority expansion, so a corpus ingested in-container ends
#               up with "Classiffcation" where a host ingest produces
#               "Classification". A ~1MB wordlist is a cheap price for
#               making ingestion environment-independent.
#
# No compiler toolchain is needed — every dependency in pyproject.toml
# (incl. sentence-transformers/torch, psycopg2-binary) ships prebuilt
# manylinux wheels for this base image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl wamerican \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Model weights (bge-base-en-v1.5, the cross-encoder reranker, the NLI
# model) are intentionally NOT baked into this image — they're pulled at
# first run and cached in a named volume mounted at HF_HOME (see
# docker-compose.yml), so the image stays small and rebuilds stay fast.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/root/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/root/.cache/torch/sentence_transformers \
    TRANSFORMERS_CACHE=/root/.cache/huggingface

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# --- Dependency layer ---------------------------------------------------
# Only pyproject.toml plus a minimal `app/__init__.py` stub are copied
# here — just enough for setuptools' `packages.find` (see
# [tool.setuptools.packages.find] in pyproject.toml) to discover the
# "app" package and for `pip install -e .` to resolve and install every
# dependency. This layer only invalidates when pyproject.toml itself
# changes, so editing application source below never forces a dependency
# reinstall.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py
RUN pip install --no-cache-dir -e .

# --- Application source --------------------------------------------------
# Overwrites the app/__init__.py stub with the real package. The install
# above is editable (PEP 660), so setuptools resolves imports straight
# from this directory at import time — no reinstall needed after this
# COPY.
COPY app/ ./app/

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
