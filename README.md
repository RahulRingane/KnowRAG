# KnowRAG

A fact-checking RAG system. It answers questions strictly from retrieved
evidence and **rejects claims it cannot support**, instead of letting an LLM
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
