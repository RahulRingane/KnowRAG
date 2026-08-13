# KnowRAG

A fact-checking RAG system. It answers strictly from retrieved evidence and marks every claim with what that evidence does or does not establish, instead of letting an LLM fill gaps with plausible-but-unverified text passed off as confirmed. Every returned sentence is scored against its cited passage by a dedicated NLI model, and that score is always on the response — claims that fail verification aren't deleted, they come back in `rejected_claims` with a reason.

## How it works

Every input is classified once, up front, into one of two routes — there's no third path and no re-routing mid-request:

- **Question** (e.g. `"what is RISC?"`) → the system retrieves evidence and **generates an answer** from the corpus, then verifies its own generated claims against what it cited.
- **Statement / fact** (e.g. `"RISC has instruction pipelining"`) → the caller's own sentence is checked as written. Retrieval still runs, but there's **no generation call at all** — the line is NLI-scored directly against the retrieved chunks, and the verdict (`SUPPORTED` / `CONTRADICTED` / `UNSUPPORTED`) is the answer.

Classification is a simple heuristic (trailing `?`, or a first word from a closed interrogative set like `what`/`is`/`does`/`has`) — not a model call. Ambiguous input defaults to the fact route on purpose, since mis-routing a question just produces a visibly odd verdict, while mis-routing a statement would silently check the wrong sentence.

```
POST /query  {"question": "What is an embedded system?"}
→ { "input_type": "question",
    "answer": "An embedded system is ... [C1].", "state": "ok",
    "claims": [ { "text": "...", "status": "SUPPORTED", "citations": ["C1"],
                  "evidence_score": 0.98, "chunk_ids": ["1:7"] } ],
    "rejected_claims": [], "refusals": [],
    "retrieved_chunk_ids": ["1:7", "1:8", "1:12"],
    "latency_ms": { "retrieval_ms": 41.2, "rerank_ms": 1204.6,
                    "generation_ms": 1180.4, "verification_ms": 512.9 } }
```

```
query:  input → classify ┬→ "question" → retrieve (once) → generate → verify → assemble
                          │              (system generates the answer, then fact-checks it)
                          └→ "fact"     → retrieve (once) → verify → assemble
                                         (caller's own line is fact-checked directly — no generation)
```

## Features

- **Hybrid retrieval** — Qdrant (dense, `bge-base-en-v1.5`) and Elasticsearch (BM25) each return top candidates; deduped and cross-encoder reranked before the LLM sees anything.
- **Claim-level verification** — generation returns structured `{kind, text, citations}` objects, never free prose. Each claim is scored against its cited chunk by a dedicated NLI model — relevance (reranker) and entailment (NLI) are never collapsed into one signal.
- **Two input routes, no re-entry** — a question triggers generation and gets answered from the corpus; a plain statement/line skips generation entirely and is NLI-scored as written against retrieved chunks. The fork is taken once; retrieval still runs exactly once per request either way.
- **Traceable evidence** — `document_id:chunk_index` is the join key between a claim's `chunk_ids` and the response's `retrieved_chunk_ids`, all the way back to the Elasticsearch/Qdrant record.
- **No agentic loop, by design** — one retrieval round, at most one generation call, one verification pass. No path back to re-retrieve.
- **Observability built in** — per-stage Prometheus histograms and verdict counters at `/metrics`, JSON logs with trace IDs, `/health` checks all three datastores, and every response carries a disjoint per-stage `latency_ms` breakdown that sums to wall time.

## Architecture

Dependencies point one way: `main.py` → `api/` → `services/` → `domain/` (business rules, imports nothing but `core/`), with `infrastructure/` (`db/`, `search/`, `ml/`, `llm/`, `pdf/`) implementing ports declared in `domain/ports.py`. Swapping Qdrant, Postgres, or the LLM vendor cannot touch the logic that decides whether an answer is trustworthy.

The layering is enforced, not just documented — `tests/test_architecture.py` reads the import graph and fails the build on a domain module importing infrastructure, a vendor SDK above infrastructure, an `os.getenv` outside `core/config.py`, or `argparse` outside `app/cli/`.

| Component | Role |
|---|---|
| `postgres:16-alpine` | source of truth for chunks (5432) |
| `qdrant/qdrant:v1.18.0` | dense vector search, collection `knowrag` (6333) |
| `elasticsearch:8.13.4` | BM25 keyword search, index `knowrag` (9200) |
| `app` | FastAPI service + reranker/NLI/embedding models (8000) |

## Requirements

- Docker Compose v2, ~4 GB free RAM
- One LLM provider key — Gemini or OpenAI
- Node 22+ for the frontend
- Python 3.11+ only if running host-side CLI/tests

## Quick Start

```bash
git clone https://github.com/RahulRingane/KnowRAG.git
cd KnowRAG/backend
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> JWT_SECRET
$EDITOR .env   # set GEMINI_API_KEY (or OPENAI_API_KEY + LLM_PROVIDER=openai) and JWT_SECRET

docker compose up -d --build
docker compose ps          # wait for all four "healthy" — ES takes ~30s first time
curl -s localhost:8000/health | jq
```

Registration is first-user-then-closed — once one account exists, `/auth/register` returns `403` to everyone else. All of `/query`, `/ingest`, `/documents` require a bearer token.

```bash
curl -s -X POST localhost:8000/auth/register -H 'content-type: application/json' \
  -d '{"username": "admin", "password": "at-least-8-chars"}' | jq

TOKEN=$(curl -s -X POST localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"username": "admin", "password": "at-least-8-chars"}' | jq -r .access_token)

curl -F "file=@data.pdf" -H "Authorization: Bearer $TOKEN" localhost:8000/ingest   # -> 202 + document_id
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/ingest/1 | jq             # poll until "indexed"

curl -s -X POST localhost:8000/query -H 'content-type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"question": "What is an embedded system?"}' | jq
```

Docs at `localhost:8000/docs`. First query after startup is slow (~4s model warm-up).

### Frontend (optional)

```bash
cd ../frontend
npm install
cp .env.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev                  # localhost:3000
```

Backend must list this origin in `CORS_ORIGINS` (defaults include `localhost:3000`/`3001`) or every request dies at preflight.

### Tearing down

```bash
docker compose down       # stop, keep data
docker compose down -v    # also destroy pgdata, qdrant, es, and the model cache
```

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/`, `/health`, `/metrics`, `/docs` | ping · datastore check (`503` if any down) · Prometheus · Swagger |
| `POST` | `/query`, `/query/stream` | classify → route → `FactCheckedResponse` · SSE (`retrieval` → `token`* → `verification` → `done`; fact route emits zero `token` events) |
| `POST` | `/ingest`, `GET /ingest/{id}` | multipart PDF → `202` + `document_id` · `pending` \| `indexed` \| `failed` |

Nothing before the terminal `verification` SSE event has been fact-checked — never surface raw `token` output as an answer.

## CLI

Same service code path as the API — one for ingestion, one for retrieval. Ingestion is idempotent (unchanged `content_hash` short-circuits chunking).

```bash
python -m app.cli.ingest data.pdf [--force] [--full-reindex]
python -m app.cli.index [--document-id N] [--index qdrant|elasticsearch] [--full-reindex]
python -m app.cli.retrieve                     # interactive hybrid search
python -m app.cli.query "what is X?"           # question -> generates + answers from corpus
python -m app.cli.query "X has property Y"     # plain statement -> fact-checked as written, no generation
                                                # --json, -q (answer only), -v (stage logs)
```

## Configuration

`app/core/config.py` is the only module permitted to read the environment — enforced by the architecture test.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | — | required |
| `GEMINI_API_KEY` | — | required; `GOOGLE_GENERATIVE_AI_API_KEY` also accepted |
| `LLM_PROVIDER` / `GEMINI_MODEL` | `gemini` / `gemini-3.6-flash` | native `response_schema` JSON mode |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | Structured Outputs (`strict: true`) |
| `QDRANT_URL` / `ES_URL` | `localhost:6333` / `localhost:9200` | legacy `QURL` / `QAPI` still resolve |
| `RERANKER_MODEL` / `NLI_MODEL` | `ms-marco-MiniLM-L-6-v2` / `DeBERTa-v3-base-mnli-fever-anli` | relevance vs. entailment — never collapse the two |
| `TOP_K_RETRIEVAL` / `TOP_K_RERANK` / `CLAIM_VERIFICATION_THRESHOLD` | `20` / `5` / `0.55` | per-store / reaching the LLM / entailment a claim must clear |

## Performance

Warm, the pipeline answers in **2.7–7.4s**: LLM generation (1.7–3.5s), NLI verification (0.5–4.3s, scales with claim count), cross-encoder rerank (1.1–1.4s). Retrieval itself is ~40ms.

Cold, the first query in a process additionally pays model-load cost — reduced from ~18s to **~2.9s** by loading from local cache first (`local_files_only=True`) instead of revalidating against huggingface.co on every construction. The API preloads and warms all three models at startup (~4s), so the first real request pays neither cost; the CLI is a fresh process per query and keeps a ~2.8s floor.

## Development

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
docker compose up -d postgres qdrant elasticsearch
uvicorn app.main:app --reload --port 8001            # 8000 belongs to the container
pytest -m "not slow"    # fast, fully offline — no datastores, no network
pytest -m slow          # adds tests that load real model weights
```

```bash
cd ../frontend
npm run dev
npm run typecheck && npm run lint
npm run test:run   # vitest, network mocked with MSW
```

## Data & Evaluation

`backend/data.pdf` is gitignored — a clean checkout has no corpus. `eval/` holds a hand-authored golden set (answerable + adversarial "trap" questions), scoring false support, bait rejection, and false rejection rates.

```bash
docker compose run --rm --no-deps -v "$PWD/eval:/app/eval" \
    --entrypoint python app -m eval.run_retrieval_eval      # or run_faithfulness_eval
```

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| `ValidationError` at startup, host scripts fine | space around `=` in `.env` — Compose's parser drops it, pydantic-settings doesn't |
| Retrieval empty, ingestion succeeded | wiped index, Postgres intact — check `localhost:9200/knowrag/_count`, then `app.cli.index --full-reindex` |
| Everything returns `insufficient_evidence` | expected when evidence is missing — confirm corpus is `indexed`; on the question route this means nothing answerable was generated, not a threshold issue |
| A line got answered when it should've been fact-checked (or vice versa) | check `input_type` on the response; classifier is heuristic (trailing `?`, then closed interrogative set), ambiguous input defaults to `fact`. Add a `?` to force the question route; drop leading `what`/`is`/`has` to force the fact route |
| Code changes don't take effect in container | image has no `--reload`; use the host venv or `docker compose up -d --build app` |
| Query unexpectedly slow | read the `timing` line first — `model_load` above ~1s means weights aren't cached where the process is looking |

## License

See `LICENSE`.