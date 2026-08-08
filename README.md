# KnowRAG

A fact-checking RAG system. It answers questions strictly from retrieved
evidence and **rejects claims it cannot support**, instead of letting an LLM
<<<<<<< Updated upstream
fill the gaps with plausible-but-unverified text. Every returned sentence has
been checked against the passage it cites by an NLI model; ones that fail are
not deleted — they come back in `rejected_claims` with a reason.

```
POST /query  {"question": "What is an embedded system?"}
→ { "answer": "An embedded system is ... [C1].", "state": "ok",
    "claims": [ { "text": "...", "status": "SUPPORTED", "citations": ["C1"],
                  "evidence_score": 0.98, "chunk_ids": ["1:7"] } ],
    "rejected_claims": [], "refusals": [],
    "retrieved_chunk_ids": ["1:7", "1:8", "1:12"],
    "latency_ms": { "retrieval_ms": 412.7, "generation_ms": 1180.4, ... } }
```

- **Hybrid retrieval.** Qdrant (dense, `bge-base-en-v1.5`) and Elasticsearch
  (BM25) each return 20 candidates; the union is deduped on
  `(document_id, chunk_index)` and cross-encoder reranked to the 5 the LLM sees.
- **Claim-level verification.** Generation returns structured
  `{kind, text, citations}` objects via the provider's native JSON-schema mode,
  never prose. Each claim is scored against its cited chunk by a dedicated NLI
  model — **not** the reranker. Relevance is not entailment. A `kind: "refusal"`
  is declared at generation time and routed to `refusals`, never scored.
- **Traceable evidence.** `document_id:chunk_index` is the Elasticsearch `_id`,
  the seed for Qdrant's point UUID, and the join key between a verdict's
  `chunk_ids` and the response's `retrieved_chunk_ids`.
- **No agentic loop, on purpose.** Retrieval runs once, before the LLM, with no
  path back — one retrieval round, one generation call, one verification pass.
- **Observability built in.** Per-stage Prometheus histograms and verdict
  counters at `/metrics`, JSON logs with a trace ID, and a `/health` that
  connects to all three datastores.

## Quick start

Needs Docker Compose v2, ~4 GB free RAM, one LLM provider key (Gemini or
OpenAI); Python 3.11+ only for host-side CLI/tests. First ingest downloads ~1 GB
of weights into the `model_cache` volume; docs at <http://localhost:8000/docs>.

```bash
cp .env.example .env && $EDITOR .env           # DATABASE_URL + one provider key
docker compose up -d --build                   # postgres, qdrant, elasticsearch, app
curl -F "file=@data.pdf" localhost:8000/ingest # -> 202 + document_id
curl -s localhost:8000/ingest/1 | jq           # poll until "indexed"
curl -s -X POST localhost:8000/query -H 'content-type: application/json' \
  -d '{"question": "What is an embedded system?"}' | jq
```

> Compose's `env_file` parser requires strict `KEY=value` with **no space**
> before the `=`. pydantic-settings tolerates `KEY = value`, so a stray space
> works on the host and silently drops the variable inside the container.

## CLI and API

The CLI drives the same services as the API — one code path for ingestion, one
for retrieval. Ingestion is idempotent (an unchanged `content_hash`
short-circuits chunking); `app.cli.index` rebuilds a search index from Postgres
without re-reading the PDF.

```bash
python -m app.cli.ingest data.pdf [--force] [--full-reindex]
python -m app.cli.index [--document-id N] [--index qdrant|elasticsearch] [--full-reindex]
python -m app.cli.retrieve                     # interactive hybrid search
python -m app.cli.query "what is X?"           # --json, -q (answer only), -v (stage logs)
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` · `/health` · `/metrics` · `/docs` | ping · datastore check (`503` if any is down) · Prometheus · Swagger |
| `POST` | `/query` · `/query/stream` | full pipeline → `FactCheckedResponse` · SSE `retrieval` → `token`* → `verification` → `done` |
| `POST` | `/ingest` · `GET /ingest/{id}` | multipart PDF → `202` + `document_id` · `pending` \| `indexed` \| `failed` |

Nothing before the terminal `verification` event on `/query/stream` has been
fact-checked — never present raw `token` output as an answer.

## Architecture

Dependencies point one way: `main.py` (composition root) → `api/` → `services/`
→ `domain/` (rules; imports nothing but `core/`), with `infrastructure/`
(`db/`, `search/`, `ml/`, `llm/`, `pdf/`) implementing the ports declared in
`app/domain/ports.py`. That inversion is the point: swapping Qdrant, Postgres or
the LLM vendor cannot touch the logic that decides whether an answer is
trustworthy. **The layering is enforced, not documented** —
`tests/test_architecture.py` reads the import graph and fails the build on a
domain module importing infrastructure, a vendor SDK above infrastructure, an
`os.getenv` outside `core/config.py`, or argparse outside `app/cli/`.

```
query:  question → retrieve (once) → format_context → generate → verify → assemble
ingest: PDF → reader → table detection/linearization (geometry separates a table
        from a run of short paragraphs) → ligature + hyphen repair
        → SentenceSplitter(512/64) → Postgres `chunks` → Qdrant + ES → `indexed`
```

`format_context` numbers the chunks `[C1]..[C5]`, and the **exact** tag map it
handed the generator is threaded into the verifier rather than rebuilt there —
two derivations would diverge and emit confidently SUPPORTED verdicts backed by
chunks the generator never saw. Per claim, in priority order: `kind ==
"refusal"` → `REFUSAL` (never scored); no citations → `UNSUPPORTED`; best cited
entailment ≥ `CLAIM_VERIFICATION_THRESHOLD` (0.55) → `SUPPORTED`; a cited chunk
scored *contradiction* → `CONTRADICTED`; otherwise `UNSUPPORTED`. Citation tags
are stripped before scoring — a trailing `[C1]` made the NLI model answer
*neutral* to otherwise-verbatim claims. `answer` is assembled from SUPPORTED
claims only; when nothing survives, `state` is `insufficient_evidence` — a
normal `200`, not an error. A document reaches `indexed` only once **every**
search index is written; Postgres holding the chunks doesn't make it searchable.

Containers: `postgres:16-alpine` (5432, source of truth), `qdrant/qdrant:v1.18.0`
(6333, collection `knowrag`), `elasticsearch:8.13.4` (9200, index `knowrag`),
`app` (8000, FastAPI + the three local models). Qdrant must stay ≥ 1.10, when
`query_points` was introduced — pinned at v1.9.0 the server 404'd every search
while `upsert` and `get_collections` kept working, so ingestion and `/health`
looked fine and retrieval was silently dead.

## Configuration

`app/core/config.py` is the **only** module permitted to read the environment,
and the architecture test enforces that. `.env` is read from the repo root or
`app/.env`; anything not listed has a working default there.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | — | **required** |
| `GEMINI_API_KEY` | — | **required**; `GOOGLE_GENERATIVE_AI_API_KEY` also accepted |
| `LLM_PROVIDER` / `GEMINI_MODEL` | `gemini` / `gemini-3.6-flash` | native `response_schema` JSON mode |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | Structured Outputs (`strict: true`) |
| `QDRANT_URL` / `ES_URL` | `localhost:6333` / `localhost:9200` | legacy `QURL` / `QAPI` still resolve |
| `RERANKER_MODEL` / `NLI_MODEL` | `ms-marco-MiniLM-L-6-v2` / `DeBERTa-v3-base-mnli-fever-anli` | relevance / entailment — never collapse the two |
| `TOP_K_RETRIEVAL` / `TOP_K_RERANK` / `CLAIM_VERIFICATION_THRESHOLD` | `20` / `5` / `0.55` | per store / reaching the LLM / entailment a claim must clear |

Both clients are constructed lazily, so a single-provider `.env` starts cleanly
with the other key absent. `gemini-2.5-flash` 404s for keys created after
~2026-08 even though `ListModels` still reports it. `gpt-4o-mini` was chosen on
cost — ~3.5K input-dominated tokens/call ≈ $0.0006–0.0011, where `gpt-4o` is
~20× that. `.cache/llm/` is a dev cache keyed on `(question,
retrieved_chunk_ids, model)`.

## Development

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
docker compose up -d postgres qdrant elasticsearch   # datastores only
uvicorn app.main:app --reload --port 8001            # 8000 belongs to the container
pytest -m "not slow"    # 252 tests, ~14s, fully offline — no datastores, no network
pytest -m slow          # adds the tests that load real model weights (~400MB)
```

The default suite stubs every model and datastore and must stay that way. No
migrations exist or are needed — `create_tables()` runs at startup and is
best-effort, so a transient Postgres blip leaves the app up and lets `/health`
report it rather than triggering a restart loop.

## Data and evaluation

`data.pdf` at the repo root is the corpus — 28 pages of Embedded Systems notes,
`document_id=1` → **42 chunks** — and is **gitignored** (3.7 MB personal
document), so a clean checkout has none. `eval/` holds a hand-authored golden
set: 39 answerable retrieval questions and 16 adversarial "trap" questions.
Measured 2026-08-03 (`gpt-4o-mini`): false support (D1, primary) **0.000**
(0/16), bait rejected (D4) 1.000 (16/16), false rejection **0.000** (0/39).
Method and history in `eval/results/baseline.md`.

```bash
docker compose run --rm --no-deps -v "$PWD/eval:/app/eval" \
    --entrypoint python app -m eval.run_retrieval_eval      # or run_faithfulness_eval
```

## Troubleshooting

`docker compose ps` for health, `logs -f app` for JSON logs, `down -v` to
DESTROY pgdata, qdrant, es and the model cache.

- **`ValidationError` at startup, but host scripts work** — a space around `=`
  in `.env`; Compose's parser drops the variable, pydantic-settings does not.
- **Retrieval empty but ingestion succeeded** — a wiped index leaves Postgres
  intact. Check `localhost:9200/knowrag/_count`, then `app.cli.index
  --full-reindex`.
- **Everything comes back `insufficient_evidence`** — designed behavior when
  evidence is missing. Confirm the corpus is `indexed` and eyeball
  `app.cli.retrieve` before tuning `CLAIM_VERIFICATION_THRESHOLD`.
- **Text artifacts like `Classiffcation`** — the `wamerican` wordlist is
  missing; it ships in the app image, a host ingest needs it installed.
- **Code changes don't take effect in the container** — they won't; the image
  `COPY`s the source and the CMD has no `--reload`. Use the host venv or
  `docker compose up -d --build app`.
=======
fill the gaps with plausible-but-unverified text.

Every sentence returned has been independently checked against the source
passage it cites by a natural-language-inference model. Sentences that fail
are not deleted — they come back in `rejected_claims` with a reason.

```
POST /query  {"question": "What is an embedded system?"}

{
  "answer":  "An embedded system is a computer system with a dedicated
              function within a larger mechanical or electrical system [C1].",
  "state":   "ok",
  "claims":  [ { "text": "...", "status": "SUPPORTED", "citations": ["C1"],
                 "evidence_score": 0.98, "chunk_ids": ["1:7"] } ],
  "rejected_claims": [], "refusals": [],
  "retrieved_chunk_ids": ["1:7", "1:8", "1:12", "1:19", "1:23"],
  "latency_ms": { "retrieval_ms": 412.7, "generation_ms": 1180.4,
                  "verification_ms": 903.1, "assembly_ms": 0.2 }
}
```

## Features

- **Hybrid retrieval.** Qdrant (dense, `bge-base-en-v1.5`) and Elasticsearch
  (BM25) in parallel, deduplicated on `(document_id, chunk_index)`, then
  cross-encoder reranked from 20 candidates to the 5 that reach the LLM.
- **Claim-level verification.** Generation returns *structured claims* —
  `{kind, text, citations}` via the provider's JSON-schema mode, never parsed
  prose. Each is scored against its cited chunk by a dedicated NLI model,
  **deliberately not the reranker**: relevance is not entailment.
- **Refusals are first-class.** `kind: "refusal"` is declared at generation
  time and routed to a separate field, so "I cannot answer that" can never be
  scored as evidence and promoted into a supported claim.
- **Evidence tracking end to end.** `document_id:chunk_index` is the ES `_id`,
  the seed for Qdrant's deterministic point UUID, and the join key between a
  verdict's `chunk_ids` and `retrieved_chunk_ids`.
- **No agentic loop, on purpose.** Retrieval runs once, before the LLM, with
  no path back — bounding latency at one round of each stage.
- **Observability built in.** Prometheus per-stage histograms at `/metrics`,
  JSON logs with a trace ID, and a `/health` that really connects to all three
  datastores.

## Quick start

Needs Docker + Compose v2, ~4 GB free RAM, and a Gemini **or** OpenAI key.
Python 3.11+ only for host-side CLI and tests.

```bash
cp .env.example .env
$EDITOR .env                    # DATABASE_URL + at least one provider key
docker compose up -d --build    # postgres, qdrant, elasticsearch, app
curl -s localhost:8000/health | jq

curl -F "file=@data.pdf" localhost:8000/ingest      # -> 202 + document_id
curl -s localhost:8000/ingest/1 | jq                # poll until "indexed"

curl -s -X POST localhost:8000/query \
  -H 'content-type: application/json' \
  -d '{"question": "What is an embedded system?"}' | jq
```

`DATABASE_URL` and `GEMINI_API_KEY` are the only required variables; the rest
default in `app/core/config.py`, the only module permitted to read the
environment. `depends_on` gates the app on `service_healthy`, so it will not
start before the datastores can serve. First ingest downloads ~1 GB of weights
into the `model_cache` volume. Docs at <http://localhost:8000/docs>.

> Compose's `env_file` parser requires strict `KEY=value` with **no space**
> before the `=`. pydantic-settings tolerates `KEY = value`, so a stray space
> works on the host and silently drops the variable in-container — surfacing
> as a `ValidationError` at startup.

## Usage

```bash
# Streaming (SSE): retrieval -> token* -> verification -> done
curl -N -X POST localhost:8000/query/stream \
  -H 'content-type: application/json' -d '{"question": "..."}'

# CLI — same services, same code path as the routes
python -m app.cli.query "What is the RISC philosophy?"   # --json | -q | -v, reads stdin
python -m app.cli.ingest data.pdf                        # --force | --full-reindex
python -m app.cli.index --full-reindex                   # re-index from Postgres only
python -m app.cli.retrieve                               # chunk keys + rerank scores

pytest -m "not slow"    # 252 tests, ~14s, offline — every model and store stubbed
pytest -m slow          # adds tests that load real weights (~400MB)
```

Ingestion is idempotent: an unchanged `content_hash` skips re-chunking, and
drop-and-recreate is an explicit `--full-reindex` opt-in. Nothing before the
terminal `verification` event on `/query/stream` has been fact-checked, so a
client must never present raw `token` output as an answer.

## Architecture

Dependencies point in one direction only.

```
app/
├── main.py             composition root: create_app(), lifespan, middleware
├── api/                HTTP — routes, DTOs, DI wiring, exception -> status
├── services/           use cases — QueryService, IngestionService, HealthService
├── domain/             rules and vocabulary; imports no layer but core
├── infrastructure/     adapters — db/, search/, ml/, llm/, pdf/
└── core/               config, observability, exceptions (a leaf)
```

`infrastructure` depends *on* `domain` — it implements the ports
`app/domain/ports.py` declares. That inversion is the point: swapping Qdrant,
Postgres, or the LLM vendor cannot touch the logic that decides whether an
answer is trustworthy. **The layering is enforced, not documented.**
`tests/test_architecture.py` reads the import graph and fails the build on a
domain module importing infrastructure, a vendor SDK above infrastructure, an
`os.getenv` outside `core/config.py`, or argparse outside `app/cli/`.

### Query pipeline

```
question
   ├─► HybridRetriever.search()   Qdrant dense + ES BM25, 20 each
   │      dedupe on (document_id, chunk_index) → cross-encoder rerank → top 5
   ├─► format_context()           numbered [C1]..[C5] block + tag → Chunk map
   ├─► ClaimGenerator.generate()  Gemini or OpenAI, JSON-schema mode
   │                              → [{kind, text, citations}, ...]
   ├─► ClaimVerifier.verify_tagged()   NLI entailment, claim vs. cited chunk
   │                              → SUPPORTED | UNSUPPORTED | CONTRADICTED | REFUSAL
   └─► assemble_response()        answer from SUPPORTED claims only;
                                  everything else kept in the audit trail
```

The **exact** tag map `format_context` handed the generator is threaded into
the verifier rather than rebuilt there. Deriving the same mapping twice means
a future divergence would resolve citations against the wrong chunks and emit
confidently SUPPORTED verdicts backed by evidence the generator never saw.

Verification, checked in priority order per claim:

| # | Condition | Verdict |
|---|---|---|
| 1 | `kind == "refusal"` | `REFUSAL` — never scored, never evidence |
| 2 | No citations at all | `UNSUPPORTED`, `"no citation provided"` |
| 3 | Best cited *entailment* ≥ `CLAIM_VERIFICATION_THRESHOLD` (0.55) | `SUPPORTED` |
| 4 | Any cited chunk scored *contradiction* | `CONTRADICTED` |
| 5 | Otherwise | `UNSUPPORTED` |

A tag resolving to no retrieved chunk scores `0.0` rather than raising, so a
stale or hallucinated tag can never crash verification. Citation tags are
stripped before scoring — a trailing `[C1]` is metadata, and leaving it in
made the NLI model answer *neutral* to verbatim claims. Declined premises over
2200 chars get a sentence-window re-score. `answer` contains only SUPPORTED
claims; when nothing survives, `state` is `insufficient_evidence` — a normal
`200`, not an error.

### Ingestion pipeline

PDF → reader (text + positioned ops) → table detection and linearization
(geometry distinguishes a table from short paragraphs) → ligature and
line-break-hyphen repair → `SentenceSplitter(chunk_size=512, overlap=64)` →
Postgres `chunks` (`content_hash` gates re-chunking) → Qdrant + Elasticsearch.
A document flips to `indexed` only once **every** configured index has been
written — Postgres holding the chunks does not make it searchable.

### Containers and endpoints

| Service | Image | Port | Role |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 5432 | source of truth — `documents`, `chunks` |
| `qdrant` | `qdrant/qdrant:v1.18.0` | 6333 | dense vectors, collection `knowrag` |
| `elasticsearch` | `elasticsearch:8.13.4` | 9200 | BM25, index `knowrag` |
| `app` | `Dockerfile` | 8000 | FastAPI + the three local models |

Qdrant must stay ≥ 1.10, when the `query_points` API the adapter calls landed.
Pinned at v1.9.0 it 404'd every search while `upsert` and `get_collections`
kept working — ingestion and `/health` looked fine, retrieval was silently dead.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` · `/health` · `/metrics` · `/docs` | ping · datastore check (`503` if down) · Prometheus · Swagger |
| `POST` | `/query` · `/query/stream` | full pipeline → `FactCheckedResponse` · same as SSE |
| `POST` | `/ingest` | multipart PDF → `202` + `document_id` |
| `GET` | `/ingest/{document_id}` | `pending` \| `indexed` \| `failed`, with chunk count |

## Configuration

`.env` is read from the repo root or `app/.env`. Beyond the two required
variables, the settings worth knowing:

| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | `gemini` \| `openai` |
| `GEMINI_MODEL` / `OPENAI_MODEL` | `gemini-3.6-flash` / `gpt-4o-mini` | |
| `QDRANT_URL` / `ES_URL` | `localhost:6333` / `:9200` | legacy `QURL`/`QAPI` still resolve |
| `QDRANT_COLLECTION` / `ES_INDEX` | `knowrag` | keep the two in sync |
| `EMBEDDING_MODEL` / `RERANKER_MODEL` / `NLI_MODEL` | bge-base · ms-marco-MiniLM · DeBERTa-v3-mnli-fever-anli | never collapse the last two |
| `TOP_K_RETRIEVAL` / `TOP_K_RERANK` | `20` / `5` | per store / reaching the LLM |
| `CLAIM_VERIFICATION_THRESHOLD` | `0.55` | entailment a claim must clear |

Both providers are constructed lazily, so a single-provider `.env` starts
cleanly with the other key absent, and everything downstream consumes the same
plain dict without learning the vendor. Gemini is the code default via native
`response_schema`; OpenAI uses Structured Outputs (`strict: true`) and was
chosen on cost — ~3.5K input-dominated tokens/call ≈ $0.0006–0.0011, where
`gpt-4o` is ~20× that. Note `gemini-2.5-flash` 404s for keys created after
~2026-08 even though `ListModels` still reports it. `.cache/llm/` holds a dev
cache keyed on `(question, retrieved_chunk_ids, model)`.

## Data and evaluation

`data.pdf` at the repo root is the corpus: 28 pages of Embedded Systems notes,
`document_id=1` → **42 chunks**. It is **gitignored** (3.7 MB personal
document), so a clean checkout has none — supply your own PDF. `eval/` holds a
hand-authored golden set: 39 answerable questions and 16 adversarial "trap"
questions designed to invite unsupported answers.

```bash
docker compose run --rm --no-deps -v "$PWD/eval:/app/eval" \
    --entrypoint python app -m eval.run_faithfulness_eval   # or run_retrieval_eval
```

Measured 2026-08-03 (`gpt-4o-mini`, 42-chunk corpus): false support (D1,
primary) **0.000** (0/16), bait rejected (D4) 1.000 (16/16), false rejection
**0.000** (0/39), explicit refusal on 16/16 adversarial items. Methodology and
history in `eval/README.md` and `eval/results/baseline.md`.

## Troubleshooting

```bash
docker compose ps                        # who is healthy
docker compose logs -f app               # structured JSON logs
docker compose down                      # keep volumes; -v DESTROYS them
```

- **`ValidationError` at startup but host scripts work** — a space around `=`
  in `.env`; Compose drops the variable, pydantic-settings does not.
- **`/health` says a datastore is down** — `curl -s localhost:8000/health | jq`
  for the per-datastore breakdown, then check that store directly.
- **Retrieval empty though ingestion succeeded** — a wiped index leaves
  Postgres intact; check `localhost:6333/collections/knowrag` and
  `localhost:9200/knowrag/_count`, then `python -m app.cli.index --full-reindex`.
- **Elasticsearch never turns healthy** — almost always memory; the heap is
  capped at 512 MB and the container needs headroom. Check `vm.max_map_count`.
- **First query is slow** — weights download on first use (~1 GB);
  `preload_models()` is non-blocking, so `/health` answers while they load.
- **Persistent `insufficient_evidence`** — designed behavior when evidence is
  missing, but confirm the corpus is `indexed` and eyeball
  `python -m app.cli.retrieve` before tuning the threshold.
- **Artifacts like `Classiffcation`** — the `wamerican` wordlist is missing;
  it ships in the image, so a host ingest needs `apt-get install wamerican`.
- **Code changes don't take effect in the container** — they won't; the image
  `COPY`s the source and the CMD has no `--reload`.

Develop on the host instead:

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
docker compose up -d postgres qdrant elasticsearch    # datastores only
uvicorn app.main:app --reload --port 8001             # 8000 is the container's
```

No migrations exist or are needed — `create_tables()` runs at startup and is
best-effort, so a transient Postgres blip leaves the app up and lets `/health`
report it rather than triggering a restart loop.
>>>>>>> Stashed changes
