# KnowRAG

A fact-checking RAG system. It answers questions strictly from retrieved
evidence and **rejects claims it cannot support**, instead of letting an LLM
fill the gaps with plausible-but-unverified text.

Every sentence returned has been checked against the passage it cites by an NLI
model. Sentences that fail are not deleted — they come back in `rejected_claims`
with a reason, so a caller can see what was filtered and why.

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

## What makes it different

- **Hybrid retrieval.** Qdrant (dense, `bge-base-en-v1.5`) and Elasticsearch
  (BM25) each return 20 candidates; the union is deduped on
  `(document_id, chunk_index)` and cross-encoder reranked down to the 5 that
  reach the LLM.
- **Claim-level verification.** Generation returns structured
  `{kind, text, citations}` objects via the provider's native JSON-schema mode,
  never prose. Each claim is scored against its cited chunk by a dedicated NLI
  model — **not** the reranker. Relevance is not entailment.
- **Refusals are first-class.** `kind: "refusal"` is declared at generation time
  and routed to a separate `refusals` field, so "I cannot answer that" is never
  scored as evidence.
- **Traceable evidence.** `document_id:chunk_index` is the Elasticsearch `_id`,
  the seed for Qdrant's point UUID, and the join key between a verdict's
  `chunk_ids` and the response's `retrieved_chunk_ids`.
- **No agentic loop, on purpose.** Retrieval runs exactly once, before the LLM,
  with no path back. Latency is bounded at one retrieval round + one generation
  call + one verification pass.
- **Observability built in.** Per-stage Prometheus histograms and verdict
  counters at `/metrics`, JSON logs with a per-request trace ID, and a `/health`
  that actually connects to all three datastores.

## Quick start

Needs Docker Compose v2, ~4 GB free RAM, and one LLM provider key (Gemini or
OpenAI). Python 3.11+ only for host-side CLI/tests.

```bash
cp .env.example .env
$EDITOR .env                                   # DATABASE_URL + one provider key
docker compose up -d --build                   # postgres, qdrant, elasticsearch, app
curl -s localhost:8000/health | jq

curl -F "file=@data.pdf" localhost:8000/ingest # -> 202 + document_id
curl -s localhost:8000/ingest/1 | jq           # poll until "indexed"

curl -s -X POST localhost:8000/query \
  -H 'content-type: application/json' \
  -d '{"question": "What is an embedded system?"}' | jq
```

First ingest downloads ~1 GB of model weights into the `model_cache` volume.
Interactive docs at <http://localhost:8000/docs>.

> Compose's `env_file` parser requires strict `KEY=value` with **no space**
> before the `=`. pydantic-settings tolerates `KEY = value`, so a stray space
> works on the host and silently drops the variable inside the container.

## CLI

The CLI drives the same services as the API — one code path for ingestion, one
for retrieval.

```bash
python -m app.cli.ingest data.pdf [--force] [--full-reindex]
python -m app.cli.index [--document-id N] [--index qdrant|elasticsearch] [--full-reindex]
python -m app.cli.retrieve                     # interactive hybrid search
python -m app.cli.query "what is X?"           # --json, -q (answer only), -v (stage logs)
```

Ingestion is idempotent: an unchanged `content_hash` short-circuits chunking.
`app.cli.index` rebuilds a search index from Postgres without re-reading the PDF.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | liveness ping |
| `GET` | `/health` | connects to Postgres, Qdrant and ES; `503` if any is down |
| `GET` | `/metrics` | Prometheus — latency histograms + verdict counters |
| `GET` | `/docs` | Swagger UI (`/redoc`, `/openapi.json` also served) |
| `POST` | `/query` | full pipeline → `FactCheckedResponse` |
| `POST` | `/query/stream` | SSE: `retrieval` → `token`* → `verification` → `done` |
| `POST` | `/ingest` | multipart PDF → `202` + `document_id` |
| `GET` | `/ingest/{document_id}` | `pending` \| `indexed` \| `failed`, with chunk count |

Nothing before the terminal `verification` event on `/query/stream` has been
fact-checked — a client must never present raw `token` output as an answer.

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

`infrastructure` depends *on* `domain` — it implements the ports declared in
`app/domain/ports.py`. That inversion is the point: swapping Qdrant, Postgres or
the LLM vendor cannot touch the logic that decides whether an answer is
trustworthy.

**The layering is enforced, not documented.** `tests/test_architecture.py` reads
the import graph and fails the build when a domain module imports
infrastructure, a vendor SDK appears above the infrastructure layer, an
`os.getenv` shows up outside `core/config.py`, or argparse appears outside
`app/cli/`.

### Query pipeline

```
question → retrieve (once) → format_context → generate claims → verify → assemble
```

`format_context` numbers the chunks `[C1]..[C5]`, and the **exact** tag map it
handed the generator is threaded into the verifier rather than rebuilt there.
Two independent derivations would eventually diverge and emit confidently
SUPPORTED verdicts backed by chunks the generator never saw.

Verification, checked in priority order per claim:

| # | Condition | Verdict |
|---|---|---|
| 1 | `kind == "refusal"` | `REFUSAL` — never scored |
| 2 | No citations | `UNSUPPORTED`, reason `"no citation provided"` |
| 3 | Best cited entailment ≥ `CLAIM_VERIFICATION_THRESHOLD` (0.55) | `SUPPORTED` |
| 4 | Any cited chunk scored *contradiction* | `CONTRADICTED` |
| 5 | Otherwise | `UNSUPPORTED` |

Citation tags are stripped from the hypothesis before scoring — a trailing
`[C1]` is metadata, and leaving it in made the NLI model answer *neutral* to
otherwise-verbatim claims. A tag resolving to no retrieved chunk scores `0.0`
rather than raising. Premises over 2200 chars that the model declined get a
sentence-window retry.

`answer` is assembled from SUPPORTED claims only. When nothing survives, `state`
is `insufficient_evidence` — a normal `200`, not an error.

### Ingestion pipeline

```
PDF → reader (text + positioned ops)
    → table detection / linearization (geometry distinguishes a table
                                       from a run of short paragraphs)
    → ligature + line-break-hyphen repair
    → SentenceSplitter(chunk_size=512, chunk_overlap=64)
    → Postgres `chunks` (content_hash gates re-chunking)
    → Qdrant + Elasticsearch → document row flips to `indexed`
```

A document is `indexed` only once **every** configured search index has been
written. Postgres holding the chunks does not make it searchable.

### Containers

| Service | Image | Port | Role |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | 5432 | source of truth — `documents`, `chunks` |
| `qdrant` | `qdrant/qdrant:v1.18.0` | 6333 | dense vectors, collection `knowrag` |
| `elasticsearch` | `elasticsearch:8.13.4` | 9200 | BM25, index `knowrag` |
| `app` | `Dockerfile` | 8000 | FastAPI + the three local models |

Qdrant must stay ≥ 1.10, when `query_points` was introduced. Pinned at v1.9.0
the server 404'd every search while `upsert` and `get_collections` kept working
— ingestion and `/health` looked fine and retrieval was silently dead.

## Configuration

`app/core/config.py` is the **only** module permitted to read the environment,
and the architecture test enforces that. `.env` is read from the repo root or
`app/.env`.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | — | **required** |
| `GEMINI_API_KEY` | — | **required**; `GOOGLE_GENERATIVE_AI_API_KEY` also accepted |
| `LLM_PROVIDER` | `gemini` | `gemini` \| `openai` |
| `GEMINI_MODEL` | `gemini-3.6-flash` | |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | only when `LLM_PROVIDER=openai` |
| `QDRANT_URL` / `QDRANT_API_KEY` | `http://localhost:6333` / — | legacy `QURL` / `QAPI` still resolve |
| `ES_URL` | `http://localhost:9200` | |
| `QDRANT_COLLECTION` / `ES_INDEX` | `knowrag` / `knowrag` | keep the two in sync |
| `EMBEDDING_MODEL` | `BAAI/bge-base-en-v1.5` | |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | relevance |
| `NLI_MODEL` | `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` | entailment — never collapse with the reranker |
| `TOP_K_RETRIEVAL` / `TOP_K_RERANK` | `20` / `5` | per store / reaching the LLM |
| `CLAIM_VERIFICATION_THRESHOLD` | `0.55` | entailment score a claim must clear |

Both LLM clients are constructed lazily, so a single-provider `.env` starts
cleanly with the other key absent, and everything downstream consumes the same
plain dict.

- **Gemini** (`gemini-3.6-flash`) — the code default, native `response_schema`
  JSON mode. `gemini-2.5-flash` 404s for keys created after ~2026-08 even though
  `ListModels` still reports it.
- **OpenAI** (`gpt-4o-mini`) — Structured Outputs (`strict: true`). Chosen on
  cost: ~3.5K input-dominated tokens per call ≈ $0.0006–0.0011. `gpt-4o` is ~20×
  that — redo the arithmetic before switching.

`.cache/llm/` holds a dev cache keyed on `(question, retrieved_chunk_ids,
model)`, bind-mounted into the container so repeated eval runs don't re-spend
free-tier quota.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

docker compose up -d postgres qdrant elasticsearch   # datastores only
uvicorn app.main:app --reload --port 8001            # 8000 belongs to the container

pytest -m "not slow"    # 252 tests, ~14s, fully offline — no datastores, no network
pytest -m slow          # adds the tests that load real model weights (~400MB)
```

The default suite stubs every model and datastore and must stay that way. Port
8001 keeps the hot-reloading host process and the containerized app side by
side. No migrations exist or are needed — `create_tables()` runs at startup and
is best-effort, so a transient Postgres blip leaves the app up and lets
`/health` report it rather than triggering a restart loop.

## Data and evaluation

`data.pdf` at the repo root is the local corpus: 28 pages of Embedded Systems
notes, ingested as `document_id=1` → **42 chunks**. It is **gitignored** (3.7 MB
personal document), so a clean checkout has no corpus — supply your own PDF.
(`eval/README.md` calls it by its earlier name, `notes.pdf`.)

`eval/` holds a hand-authored golden set: 39 answerable retrieval questions and
16 adversarial "trap" questions designed to invite unsupported answers.

```bash
docker compose run --rm --no-deps -v "$PWD/eval:/app/eval" \
    --entrypoint python app -m eval.run_retrieval_eval
docker compose run --rm --no-deps -v "$PWD/eval:/app/eval" \
    --entrypoint python app -m eval.run_faithfulness_eval
```

Measured 2026-08-03 (`gpt-4o-mini`, 42-chunk corpus):

| metric | value |
|---|---|
| false support (D1, primary) | **0.000** (0/16) |
| bait rejected (D4) | 1.000 (16/16) |
| false rejection | **0.000** (0/39) |
| adversarial items with an explicit refusal | 16/16 |

Methodology, confound analysis and history are in `eval/README.md` and
`eval/results/baseline.md`.

## Troubleshooting

```bash
docker compose ps                        # who is healthy
docker compose logs -f app               # structured JSON logs
docker compose down -v                   # DESTROY pgdata, qdrant, es, model cache
```

**`ValidationError` at startup, but host scripts work.** A space around `=` in
`.env`. Compose's parser drops the variable; pydantic-settings does not.

**`/health` reports a datastore down.** Get the per-datastore breakdown from
`/health`, then check the store directly (`localhost:9200/_cluster/health`,
`localhost:6333/collections`, `docker compose exec postgres psql -U knowrag -d
knowrag -c '\dt'`).

**Retrieval returns nothing, but ingestion succeeded.** A wiped index leaves
Postgres intact and retrieval empty. Check
`localhost:6333/collections/knowrag | jq .result.points_count` and
`localhost:9200/knowrag/_count`, then `python -m app.cli.index --full-reindex`.

**Elasticsearch exits or never turns healthy.** Almost always memory. The heap
is capped at 512 MB; the container needs headroom above that. Check the logs for
`vm.max_map_count`.

**First query is slow.** Weights download on first use (~1 GB).
`preload_models()` is non-blocking, so `/health` answers while they load.

**Answers keep coming back `insufficient_evidence`.** That is the designed
behavior when evidence is missing. Verify the corpus is `indexed` and eyeball
`python -m app.cli.retrieve` before tuning `CLAIM_VERIFICATION_THRESHOLD`.

**Text artifacts like `Classiffcation`.** The `wamerican` wordlist is missing.
It ships in the app image; a host ingest needs `apt-get install wamerican`.

**Code changes don't take effect in the container.** They won't — the image
`COPY`s the source and the CMD has no `--reload`. Use the host venv, or rebuild
with `docker compose up -d --build app`.
