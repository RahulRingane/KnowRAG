# KnowRAG

A fact-checking RAG system. It answers questions strictly from retrieved
evidence and **rejects claims it cannot support**, instead of letting an LLM
fill the gaps with plausible-but-unverified text.

Every sentence the system returns has been independently checked against the
source passage it cites by a natural-language-inference model. Sentences that
fail that check are not deleted — they are returned in a separate
`rejected_claims` field with the reason, so a caller can see exactly what was
filtered and why.

```
POST /query  {"question": "What is an embedded system?"}

{
  "answer":  "An embedded system is a computer system with a dedicated
              function within a larger mechanical or electrical system [C1].",
  "state":   "ok",
  "claims":  [ { "text": "...", "status": "SUPPORTED",
                 "citations": ["C1"], "evidence_score": 0.98,
                 "chunk_ids": ["1:7"] } ],
  "rejected_claims": [],
  "refusals": [],
  "retrieved_chunk_ids": ["1:7", "1:8", "1:12", "1:19", "1:23"],
  "latency_ms": { "retrieval_ms": 412.7, "generation_ms": 1180.4,
                  "verification_ms": 903.1, "assembly_ms": 0.2 }
}
```

---

## Features

**Hybrid retrieval.** Every question hits Qdrant (dense vectors,
`BAAI/bge-base-en-v1.5`) and Elasticsearch (BM25) in parallel. The two result
sets are deduplicated on `(document_id, chunk_index)` and reranked by a
cross-encoder (`ms-marco-MiniLM-L-6-v2`), which cuts 20 candidates down to the
5 that reach the LLM.

**Claim-level verification.** Generation returns *structured claims*, not
prose — each one a `{kind, text, citations}` object produced through the
provider's native JSON-schema mode (Gemini `response_schema` / OpenAI
Structured Outputs). Every claim is then scored against the chunk it cites by
a dedicated NLI model (`MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli`), which
is **deliberately not the reranker**: a reranker answers "is this passage
related to the claim", and the question that matters is "does this passage
*entail* the claim".

**Refusals are first-class.** A model declining to answer is not an
unsupported assertion. `kind: "refusal"` is declared at generation time and
carried through to a separate `refusals` field, so "I cannot answer that from
the corpus" can never be scored as evidence and promoted into a supported
claim.

**Evidence tracking end to end.** A chunk's identity — `document_id:chunk_index`
— is the Elasticsearch `_id`, the seed for Qdrant's deterministic point UUID,
and the join key between a verdict's `chunk_ids` and the response's
`retrieved_chunk_ids`. Any answer can be traced back to the exact stored
passage that justified it.

**No agentic loop, on purpose.** Retrieval runs exactly once, before the LLM
is invoked, and there is no path back from generation or verification into
retrieval. Every answer traces to one fixed retrieval set, and latency is
bounded at one retrieval round + one generation call + one verification pass.

**Observability built in.** Prometheus counters and latency histograms per
stage at `/metrics`, structured JSON logs with a per-request trace ID, and a
`/health` endpoint that actually connects to all three datastores rather than
reporting process liveness.

---

## Quick Start

### Prerequisites

- Docker + Docker Compose v2 (`env_file:` with `required:` needs a recent v2)
- ~4 GB free RAM (Elasticsearch is capped at a 512 MB heap; the three
  transformer models add roughly 1 GB)
- An API key for one LLM provider — Gemini **or** OpenAI
- Python 3.11+ only if you want to run the CLI or tests on the host

### 1. Configure

```bash
cp .env.example .env
$EDITOR .env          # set DATABASE_URL + at least one provider key
```

`DATABASE_URL` and `GEMINI_API_KEY` are the only required variables;
everything else has a working default in `app/core/config.py`.

> Compose's `env_file` parser requires strict `KEY=value` with **no space**
> before the `=`. pydantic-settings tolerates `KEY = value`, so a stray space
> works on the host and silently drops the variable inside the container —
> surfacing as a `ValidationError` at app startup.

### 2. Bring up the stack

```bash
docker compose up -d --build
```

Four containers come up: `postgres`, `qdrant`, `elasticsearch`, and `app`.
The app's `depends_on` gates on `condition: service_healthy` for all three
datastores, so it will not start until they can actually serve traffic.

```bash
curl -s localhost:8000/health | jq
```

### 3. Ingest the corpus

```bash
curl -F "file=@data.pdf" localhost:8000/ingest      # -> 202 + document_id
curl -s localhost:8000/ingest/1 | jq                # poll until "indexed"
```

First ingest downloads ~1 GB of model weights into the `model_cache` volume.
Subsequent runs reuse it.

### 4. Ask something

```bash
curl -s -X POST localhost:8000/query \
  -H 'content-type: application/json' \
  -d '{"question": "What is an embedded system?"}' | jq
```

Interactive docs are at <http://localhost:8000/docs>.

---

## Usage

### Query

```bash
# HTTP
curl -s -X POST localhost:8000/query \
  -H 'content-type: application/json' \
  -d '{"question": "What is the RISC philosophy?"}' | jq

# Streaming (SSE): retrieval -> token* -> verification -> done
curl -N -X POST localhost:8000/query/stream \
  -H 'content-type: application/json' \
  -d '{"question": "What is the RISC philosophy?"}'

# CLI — same QueryService, same code path as the route
python -m app.cli.query "What is the RISC philosophy?"
python -m app.cli.query --json "..." | jq .claims   # full §6.4 contract
python -m app.cli.query -q "..."                    # answer text only
python -m app.cli.query -v "..."                    # per-stage pipeline logs
echo "..." | python -m app.cli.query                # reads stdin
```

Note on `/query/stream`: nothing before the terminal `verification` event has
been fact-checked. Verification cannot begin until the answer is complete, so
a client must never present raw `token` output to a user as an answer.

### Ingest PDFs

```bash
python -m app.cli.ingest data.pdf              # chunk into Postgres + index both stores
python -m app.cli.ingest data.pdf --force      # re-chunk even if content_hash matches
python -m app.cli.ingest data.pdf --full-reindex
```

Ingestion is idempotent: an unchanged `content_hash` short-circuits the
chunking stage. Drop-and-recreate is an explicit `--full-reindex` opt-in.

### Re-index without re-reading the PDF

For when Postgres is warm but a search index was wiped or its schema changed:

```bash
python -m app.cli.index --full-reindex
python -m app.cli.index --index qdrant --document-id 1
```

### Inspect retrieval

```bash
python -m app.cli.retrieve      # prompts, then prints chunk keys + rerank scores
```

### Tests

```bash
pytest -m "not slow"    # 252 tests, ~14s, fully offline — no datastores, no network
pytest -m slow          # adds the tests that load real model weights (~400MB)
```

The default suite stubs every model and datastore. `tests/test_architecture.py`
additionally reads the import graph and fails the build on a layering
violation — see [Architecture](#architecture).

---

## Architecture

### Layers

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

`infrastructure` sits below `domain` in the diagram but depends *on* it — it
implements the ports `app/domain/ports.py` declares. That inversion is the
point: swapping Qdrant, Postgres, or the LLM vendor cannot touch the logic
that decides whether an answer is trustworthy.

**The layering is enforced, not documented.** `tests/test_architecture.py`
fails the build when a domain module imports infrastructure, a vendor SDK
appears above the infrastructure layer, an `os.getenv` shows up outside
`core/config.py`, or an argparse block appears outside `app/cli/`.

### Query pipeline

```
question
   │
   ├─► HybridRetriever.search()
   │      ├── QdrantIndex        dense, bge-base-en-v1.5      ─┐
   │      └── ElasticsearchIndex BM25                          ├─ top_k_retrieval = 20 each
   │      dedupe on (document_id, chunk_index)                 │
   │      cross-encoder rerank ────────────────────────────────┘ → top_k_rerank = 5
   │
   ├─► format_context()      numbered [C1]..[C5] block + tag → Chunk map
   │
   ├─► ClaimGenerator.generate()      Gemini or OpenAI, JSON-schema mode
   │      → [{kind, text, citations}, ...]
   │
   ├─► ClaimVerifier.verify_tagged()  NLI entailment, per claim vs. cited chunk
   │      → SUPPORTED | UNSUPPORTED | CONTRADICTED | REFUSAL
   │
   └─► assemble_response()   answer from SUPPORTED claims only;
                             everything else preserved in the audit trail
```

The **exact** tag map that `format_context` handed the generator is threaded
into the verifier rather than rebuilt there. Both derivations are positional
and agree today, but deriving the same mapping twice means a future
divergence would resolve citations against the wrong chunks and emit
confidently SUPPORTED verdicts backed by evidence the generator never saw —
a silent failure, and precisely what this system exists to prevent.

### Verification decision

Checked in priority order, per claim:

| # | Condition | Verdict |
|---|---|---|
| 1 | `kind == "refusal"` | `REFUSAL` — never scored, never counted as evidence |
| 2 | No citations at all | `UNSUPPORTED`, reason `"no citation provided"` |
| 3 | Best cited *entailment* ≥ `CLAIM_VERIFICATION_THRESHOLD` (0.55) | `SUPPORTED` |
| 4 | Any cited chunk scored *contradiction* | `CONTRADICTED` |
| 5 | Otherwise | `UNSUPPORTED` |

A citation tag that resolves to no retrieved chunk is recorded and scored
`0.0` rather than raising, so a stale or hallucinated tag can never crash
verification — it simply wins nothing and the claim falls through to 4 or 5.

Citation tags are stripped from the hypothesis before scoring — a trailing
`[C1]` is metadata, not a proposition, and leaving it in made the NLI model
answer *neutral* to otherwise-verbatim claims. On premises over 2200 chars
that the model has already declined, a length-gated sentence-window fallback
re-scores against narrower spans.

`answer` contains only SUPPORTED claims. When nothing survives, `state` is
`insufficient_evidence` — a normal `200`, not an error. That path is the
reason this system exists.

### Ingestion pipeline

```
PDF → reader (text + positioned ops)
    → table detection / linearization   (geometry distinguishes a table
                                         from a run of short paragraphs)
    → ligature + line-break-hyphen repair
    → SentenceSplitter(chunk_size=512, chunk_overlap=64)
    → Postgres `chunks`   (content_hash gates re-chunking)
    → Qdrant + Elasticsearch   → document row flips to `indexed`
```

A document is `indexed` only once **every** configured search index has been
written. Postgres holding the chunks does not make it searchable.

### The four containers

| Service | Image | Port | Role |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 5432 | source of truth — `documents`, `chunks` |
| `qdrant` | `qdrant/qdrant:v1.18.0` | 6333 | dense vector search, collection `knowrag` |
| `elasticsearch` | `elasticsearch:8.13.4` | 9200 | BM25 keyword search, index `knowrag` |
| `app` | built from `Dockerfile` | 8000 | FastAPI + the three local models |

Qdrant must stay ≥ 1.10, when the `query_points` API the adapter calls was
introduced. Pinned at v1.9.0 the server answered 404 to every search while
`upsert` and `get_collections` kept working — so ingestion and `/health`
looked healthy and retrieval was silently dead.

### API endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | liveness ping |
| `GET` | `/health` | connects to Postgres, Qdrant and ES; `503` if any is down |
| `GET` | `/metrics` | Prometheus exposition — latency histograms + verdict counters |
| `GET` | `/docs` | Swagger UI (`/redoc`, `/openapi.json` also served) |
| `POST` | `/query` | full fact-checked pipeline → `FactCheckedResponse` |
| `POST` | `/query/stream` | same, as SSE: `retrieval` → `token`* → `verification` → `done` |
| `POST` | `/ingest` | multipart PDF upload → `202` + `document_id` |
| `GET` | `/ingest/{document_id}` | `pending` \| `indexed` \| `failed`, with chunk count |

---

## Environment Setup

Copy `.env.example` to `.env` (repo root or `app/.env` — both are read).
`app/core/config.py` is the **only** module permitted to read the
environment, and the architecture test enforces that.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | — | **required** |
| `GEMINI_API_KEY` | — | **required**; `GOOGLE_GENERATIVE_AI_API_KEY` also accepted |
| `LLM_PROVIDER` | `gemini` | `gemini` \| `openai` |
| `GEMINI_MODEL` | `gemini-3.6-flash` | |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | only needed when `LLM_PROVIDER=openai` |
| `QDRANT_URL` / `QDRANT_API_KEY` | `http://localhost:6333` / — | legacy `QURL` / `QAPI` still resolve |
| `ES_URL` | `http://localhost:9200` | |
| `QDRANT_COLLECTION` / `ES_INDEX` | `knowrag` / `knowrag` | keep the two in sync |
| `EMBEDDING_MODEL` | `BAAI/bge-base-en-v1.5` | |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | relevance |
| `NLI_MODEL` | `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` | entailment — never collapse with the reranker |
| `TOP_K_RETRIEVAL` / `TOP_K_RERANK` | `20` / `5` | candidates per store / chunks reaching the LLM |
| `CLAIM_VERIFICATION_THRESHOLD` | `0.55` | entailment score a claim must clear |

### Choosing an LLM provider

Both providers are constructed lazily, so a Gemini-only or OpenAI-only `.env`
both start cleanly with the other key absent. Only the selected provider's
client is imported.

- **Gemini** (`gemini-3.6-flash`) — the code default, via `google-genai`'s
  native `response_schema` JSON mode. Note that `gemini-2.5-flash` returns
  404 for keys created after ~2026-08 even though `ListModels` still reports
  it.
- **OpenAI** (`gpt-4o-mini`) — via Structured Outputs (`strict: true`),
  which makes its schema guarantee equivalent to Gemini's. Chosen on cost:
  this workload sends ~3.5K input-dominated tokens per call, ≈ $0.0006–0.0011
  each. `gpt-4o` is ~20× that per call — redo the arithmetic before switching.

Everything downstream consumes the same plain dict and never learns which
vendor produced it.

`.cache/llm/` holds a dev cache keyed on `(question, retrieved_chunk_ids,
model)`, bind-mounted into the container so repeated eval runs don't re-spend
free-tier quota.

---

## Data

`data.pdf` at the repo root is the local corpus: 28 pages of Embedded Systems
study notes, ingested as `document_id=1` and yielding **42 chunks** across
Postgres, Qdrant and Elasticsearch.

It is **gitignored** — a 3.7 MB personal document that must not be committed.
A clean checkout has no corpus; supply your own PDF and ingest it the same
way. (`eval/README.md` refers to this corpus by its earlier name, `notes.pdf`.)

### Evaluation

`eval/` holds a hand-authored golden set — 39 answerable retrieval questions
and 16 adversarial "trap" questions designed to invite unsupported answers.

```bash
docker compose run --rm --no-deps -v "$PWD/eval:/app/eval" \
    --entrypoint python app -m eval.run_retrieval_eval
docker compose run --rm --no-deps -v "$PWD/eval:/app/eval" \
    --entrypoint python app -m eval.run_faithfulness_eval
```

Measured 2026-08-03 (`gpt-4o-mini` generation, 42-chunk corpus):

| metric | value |
|---|---|
| false support (D1, primary) | **0.000** (0/16) |
| bait rejected (D4) | 1.000 (16/16) |
| false rejection | **0.000** (0/39) |
| adversarial items with an explicit refusal | 16/16 |

Full methodology, confound analysis and the history behind these numbers are
in `eval/README.md` and `eval/results/baseline.md`.

---

## Troubleshooting

```bash
docker compose ps                        # who is healthy
docker compose logs -f app               # app logs (structured JSON)
docker compose logs elasticsearch | tail
docker compose restart app
docker compose down                      # keep volumes
docker compose down -v                   # DESTROY pgdata, qdrant, es, model cache
```

**`ValidationError` at app startup, but host scripts work.** A space around
`=` in `.env`. Compose's parser drops the variable; pydantic-settings does
not. Rewrite as `KEY=value`.

**`/health` reports a datastore down.**

```bash
curl -s localhost:8000/health | jq        # per-datastore breakdown
curl -s localhost:9200/_cluster/health | jq
curl -s localhost:6333/collections | jq
docker compose exec postgres psql -U knowrag -d knowrag -c '\dt'
```

**Retrieval returns nothing, but ingestion succeeded.** Check that the
indexes actually have data — a wiped Qdrant collection or ES index leaves
Postgres intact and retrieval empty:

```bash
curl -s localhost:6333/collections/knowrag | jq .result.points_count
curl -s localhost:9200/knowrag/_count | jq
python -m app.cli.index --full-reindex   # rebuild both from Postgres
```

**Elasticsearch exits or never turns healthy.** Almost always memory. The
heap is capped at 512 MB in compose; the container still needs headroom above
that. Check `docker compose logs elasticsearch` for `vm.max_map_count`.

**First query is slow.** Model weights download on first use into the
`model_cache` volume (~1 GB). Startup calls `preload_models()`
non-blockingly, so the app answers `/health` while weights are still loading.

**Answers keep coming back `insufficient_evidence`.** That is the designed
behavior when evidence is missing, but verify the corpus is actually indexed
(`GET /ingest/{id}` → `indexed`) and eyeball retrieval with
`python -m app.cli.retrieve` before tuning `CLAIM_VERIFICATION_THRESHOLD`.

**Ingested text has artifacts like `Classiffcation`.** The `wamerican`
wordlist (`/usr/share/dict/american-english`) is missing. It ships in the app
image; a host-side ingest needs `apt-get install wamerican` to produce
byte-identical output.

**Code changes don't take effect in the container.** They won't — the image
`COPY`s the source and the CMD has no `--reload`. Run the app from a host
venv during development (see below), or rebuild with
`docker compose up -d --build app`.

### Development on the host

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

docker compose up -d postgres qdrant elasticsearch    # datastores only
uvicorn app.main:app --reload --port 8001             # 8000 belongs to the container
```

Port 8001 keeps the hot-reloading host process and the containerized app
running side by side. No migrations exist or are needed — `create_tables()`
runs at startup and is best-effort, so a transient Postgres blip leaves the
app up and lets `/health` report the problem rather than triggering a restart
loop.
