# KnowRAG (Fact-Checking RAG)

A fact-checking RAG system. It answers strictly from retrieved evidence and
**marks every claim with what that evidence does or does not establish**,
instead of letting an LLM fill the gaps with plausible-but-unverified text
passed off as confirmed. Every returned sentence has been scored against the
passage it cites by an NLI model, and what it scored is always on the response;
claims that fail are never deleted — they come back in `rejected_claims` with a
reason.

Every input is classified once, up front, and takes one of two routes:

```
"what is RISC?"                   → question → answered from the corpus
"RISC has instruction pipelining" → fact     → the caller's own sentence checked
```

```
POST /query  {"question": "What is an embedded system?"}
→ { "input_type": "question",
    "answer": "An embedded system is ... [C1].", "state": "ok",
    "claims": [ { "text": "...", "status": "SUPPORTED", "citations": ["C1"],
                  "evidence_score": 0.98, "chunk_ids": ["1:7"] } ],
    "rejected_claims": [], "refusals": [],
    "retrieved_chunk_ids": ["1:7", "1:8", "1:12"],
    "latency_ms": { "retrieval_ms": 41.2, "rerank_ms": 1204.6,
                    "generation_ms": 1180.4, "verification_ms": 512.9, ... } }
```

- **Hybrid retrieval.** Qdrant (dense, `bge-base-en-v1.5`) and Elasticsearch
  (BM25) each return 20 candidates; the union is deduped on
  `(document_id, chunk_index)` and cross-encoder reranked to the 5 the LLM sees.
- **Claim-level verification.** Generation returns structured
  `{kind, text, citations}` objects via the provider's native JSON-schema mode,
  never prose. Each claim is scored against its cited chunk by a dedicated NLI
  model — **not** the reranker. Relevance is not entailment. A `kind: "refusal"`
  is declared at generation time and routed to `refusals`, never scored.
- **Question or statement, routed once.** A question is answered from the
  corpus; a statement is NLI-scored *as written* against every retrieved chunk,
  with **no generation call at all**. Before routing, asserting something sent
  it to the LLM to be *answered*, so the thing fact-checked was the model's
  output and never the caller's assertion. One response type carries both, tagged
  with `input_type`; the fork is taken once and neither route can re-enter the
  other, so a request still retrieves exactly once.
- **Traceable evidence.** `document_id:chunk_index` is the Elasticsearch `_id`,
  the seed for Qdrant's point UUID, and the join key between a verdict's
  `chunk_ids` and the response's `retrieved_chunk_ids`.
- **No agentic loop, on purpose.** Retrieval runs once, before the LLM, with no
  path back — one retrieval round, at most one generation call (zero on the
  fact route), one verification pass.
- **Observability built in.** Per-stage Prometheus histograms and verdict
  counters at `/metrics`, JSON logs with a trace ID, and a `/health` that
  connects to all three datastores. Every response carries a per-stage
  `latency_ms` breakdown whose keys are **disjoint**, so they sum to the wall
  time of the call rather than double-counting nested work.

## Run it locally (after cloning)

This is a monorepo: **`backend/`** (formerly `rag/`) is the FastAPI service and
the datastores, with its own `docker-compose.yml` and `.env`; **`frontend/`**
is the Next.js UI with its own `.env.local`. Every backend command below is run
from `backend/`, not from the repo root.

Needs Docker Compose v2, ~4 GB free RAM, one LLM provider key (Gemini or
OpenAI), and Node 22+ for the UI; Python 3.11+ only for host-side CLI/tests.
First ingest downloads ~1 GB of model weights into the `model_cache` volume.

### 1. Clone

```bash
git clone https://github.com/RahulRingane/KnowRAG.git
cd KnowRAG
```

### 2. Backend config

```bash
cd backend
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> JWT_SECRET
$EDITOR .env    # set GEMINI_API_KEY (or OPENAI_API_KEY + LLM_PROVIDER=openai)
                # and JWT_SECRET — both are required, neither has a default
```

`DATABASE_URL` is required too, but the `app` service overrides it (along with
`QDRANT_URL` and `ES_URL`) to the compose service names, so the value in `.env`
only matters for **host-side** runs — leave the `.env.example` default alone
unless you run the CLI or `uvicorn` outside Docker.

> Compose's `env_file` parser requires strict `KEY=value` with **no space**
> before the `=`. pydantic-settings tolerates `KEY = value`, so a stray space
> works on the host and silently drops the variable inside the container —
> which surfaces as a startup `ValidationError` in the container only.

### 3. Start the stack

```bash
docker compose up -d --build     # postgres, qdrant, elasticsearch, app
docker compose ps                # all four "healthy" — ES takes ~30s the first time
curl -s localhost:8000/health | jq
```

Docs at <http://localhost:8000/docs>.

### 4. Create the first account

Registration is **first-user-then-closed**: once one user exists,
`/auth/register` answers `403` to everyone else. `/query`, `/ingest` and
`/documents` all require a bearer token, so this step comes before any of them.

```bash
curl -s -X POST localhost:8000/auth/register -H 'content-type: application/json' \
  -d '{"username": "admin", "password": "at-least-8-chars"}' | jq

TOKEN=$(curl -s -X POST localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"username": "admin", "password": "at-least-8-chars"}' | jq -r .access_token)
```

### 5. Ingest a PDF and ask it something

A clean checkout has **no corpus** — `data.pdf` is gitignored (see
[Data and evaluation](#data-and-evaluation)). Point this at any PDF of your own.

```bash
curl -F "file=@data.pdf" -H "Authorization: Bearer $TOKEN" \
  localhost:8000/ingest                                  # -> 202 + document_id
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/ingest/1 | jq  # poll -> "indexed"

curl -s -X POST localhost:8000/query -H 'content-type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"question": "What is an embedded system?"}' | jq
```

The first query after startup is the slow one (~4s of model warm-up); see
[Performance](#performance).

### 6. Frontend (optional)

```bash
cd ../frontend
npm install
cp .env.example .env.local       # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev                      # http://localhost:3000
```

The backend must list this origin in `CORS_ORIGINS` (it defaults to
`http://localhost:3000,http://localhost:3001`) or every request dies at the
preflight. Register the first account at `/register` in the UI instead of
step 4 if you'd rather not use curl.

### Tearing down

```bash
cd backend
docker compose down          # stop, keep data
docker compose down -v       # also DESTROY pgdata, qdrant, es and the model cache
```

## CLI and API

The CLI drives the same services as the API — one code path for ingestion, one
for retrieval. Ingestion is idempotent (an unchanged `content_hash`
short-circuits chunking); `app.cli.index` rebuilds a search index from Postgres
without re-reading the PDF.

Run these from `backend/` with the host venv active (see
[Development](#development)) — they talk to the datastores directly, so
`docker compose up -d postgres qdrant elasticsearch` is enough; the `app`
container is not needed and there is no token to pass.

```bash
python -m app.cli.ingest data.pdf [--force] [--full-reindex]
python -m app.cli.index [--document-id N] [--index qdrant|elasticsearch] [--full-reindex]
python -m app.cli.retrieve                     # interactive hybrid search
python -m app.cli.query "what is X?"           # question -> answered
python -m app.cli.query "X has property Y"     # statement -> fact-checked
                                               # --json, -q (answer only), -v (stage logs)
```

The classifier runs inside the service, so the CLI and the API cannot disagree
about what an input is — the CLI reads the decision off `response.input_type`
and prints it, never makes one.

`app.cli.query` prints a per-stage `timing` line on every run, not behind a
flag — `took` tells you a query was slow and never which stage was:

```
  took      6.5s
  timing    model_load 0.6s, retrieval 2.8s, rerank 1.2s, generation 1.7s, verification 0.2s
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` · `/health` · `/metrics` · `/docs` | ping · datastore check (`503` if any is down) · Prometheus · Swagger |
| `POST` | `/query` · `/query/stream` | classify → route → `FactCheckedResponse` (carries `input_type`) · SSE `retrieval` → `token`* → `verification` → `done`; a fact emits zero `token` events |
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
query:  input → classify ┬→ "question" → retrieve (once) → format_context
                         │                → generate → verify → assemble
                         └→ "fact"     → retrieve (once) → verify → assemble
                                          (no generation call)
ingest: PDF → reader → table detection/linearization (geometry separates a table
        from a run of short paragraphs) → ligature + hyphen repair
        → SentenceSplitter(512/64) → Postgres `chunks` → Qdrant + ES → `indexed`
```

Classification is a heuristic in `domain/classification.py`: a trailing `?`
wins, else a first word in a small closed set (`what`/`why`/`how`/`when`/
`where`/`who`/`is`/`does`/`can`/`has`). Ambiguity defaults to `fact` on
purpose — mis-routing a question yields a visibly odd verdict, while
mis-routing a statement silently checks the wrong sentence. Swapping in a
model- or LLM-backed classifier means implementing `InputClassifier`
(`domain/ports.py`) and passing it to `QueryService(classifier=...)`; nothing
else changes.

`format_context` numbers the chunks `[C1]..[C5]`, and the **exact** tag map it
handed the generator is threaded into the verifier rather than rebuilt there —
two derivations would diverge and emit confidently SUPPORTED verdicts backed by
chunks the generator never saw. Per claim, in priority order: `kind ==
"refusal"` → `REFUSAL` (never scored); no citations → `UNSUPPORTED`; best cited
entailment ≥ `CLAIM_VERIFICATION_THRESHOLD` (0.55) → `SUPPORTED`; a cited chunk
scored *contradiction* → `CONTRADICTED`; otherwise `UNSUPPORTED`. Citation tags
are stripped before scoring — a trailing `[C1]` made the NLI model answer
*neutral* to otherwise-verbatim claims.

**What that threshold then does differs by route, and that asymmetry is
deliberate.** On the **fact** route it decides the answer: the caller's own
assertion *is* the question, so SUPPORTED → `state: "ok"`, CONTRADICTED →
`"contradicted"`, UNSUPPORTED → `"insufficient_evidence"`, and `answer` is a
sentence *about the evidence* rather than the statement echoed back with tags
appended. On the **question** route it annotates rather than gates: `answer` is
assembled from SUPPORTED **and** UNSUPPORTED claims, and every score and reason
stays on `claims`. A sub-threshold claim therefore appears in the answer *and*
in `rejected_claims` — those two sets no longer coincide. The reason is that a
paraphrase no single chunk entails verbatim is the normal case for a generated
answer, not a hallucination signal: "what is pipelining" scored 0.489 on its
best chunk and the caller used to get a refusal message instead of a correct,
grounded answer.

CONTRADICTED claims are still withheld from `answer` on both routes — evidence
that disagrees is a finding, not an unconfirmed phrasing — and a refusal is
still not evidence, so a refusals-only result stays `insufficient_evidence`.
When nothing answerable is generated, `state` is `insufficient_evidence` — a
normal `200`, not an error. A document reaches `indexed` only once **every**
search index is written; Postgres holding the chunks doesn't make it searchable.

Containers: `postgres:16-alpine` (5432, source of truth), `qdrant/qdrant:v1.18.0`
(6333, collection `knowrag`), `elasticsearch:8.13.4` (9200, index `knowrag`),
`app` (8000, FastAPI + the three local models). Qdrant must stay ≥ 1.10, when
`query_points` was introduced — pinned at v1.9.0 the server 404'd every search
while `upsert` and `get_collections` kept working, so ingestion and `/health`
looked fine and retrieval was silently dead.

## Performance

Warm, the pipeline answers in **2.7–7.4s**, spent almost entirely in three
places: the LLM generation call (1.7–3.5s), NLI verification (0.5–4.3s, scaling
with claim count), and the cross-encoder rerank (1.1–1.4s on every query).
Retrieval itself is ~40ms — Qdrant 26–43ms and Elasticsearch 4.5–6ms — so
parallelizing the two search legs would save ~5ms of a ~5000ms query and is
deliberately not done.

Cold, the first query in a process additionally pays the model loads. That used
to be 12–16s of an 18–25s query, and the cause was not disk: `SentenceTransformer`
and `CrossEncoder` contact huggingface.co on **every** construction to
revalidate files already in the local cache. Loading all three serially took
18.14s by default and **2.87s** with `local_files_only=True` — identical weights,
identical scores. Models now load from the cache first and fall back to a
networked load, so a genuinely first run still downloads.

- **API / container.** `preload_models()` loads the three on parallel threads at
  startup and puts one throwaway forward pass through each (the first `encode()`
  costs 2.8s against 35ms for every call after it, building tokenizer state and
  torch's execution graph). Startup warmup is ~4s and the first request pays
  neither the load nor the forward pass.
- **CLI.** One process per query, so it keeps a ~2.8s first-forward-pass floor
  it cannot amortize. A cold `app.cli.query` run is ~6s.

Two optimizations were measured and rejected rather than left as open questions:
batching the NLI pairs is **1.00x** on CPU (torch already saturates 12 threads
on a single pair, so batching serializes the same FLOPs), and parallelizing
Qdrant and Elasticsearch saves ~5ms of a ~5000ms query. The remaining levers —
`TOP_K_RETRIEVAL` and the NLI checkpoint — change what the system *decides*, so
re-run `eval/` before and after touching either.

## Configuration

`app/core/config.py` is the **only** module permitted to read the environment,
and the architecture test enforces that. `.env` is read from `backend/` or
`backend/app/.env`; anything not listed has a working default there.

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

Backend, from `backend/`:

```bash
python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
docker compose up -d postgres qdrant elasticsearch   # datastores only
uvicorn app.main:app --reload --port 8001            # 8000 belongs to the container
pytest -m "not slow"    # 323 tests, ~8s, fully offline — no datastores, no network
pytest -m slow          # adds the 6 tests that load real model weights (~400MB)
```

Frontend, from `frontend/` (point `NEXT_PUBLIC_API_BASE_URL` at `:8001` to use
the reloading backend above):

```bash
npm run dev        # http://localhost:3000
npm run typecheck && npm run lint
npm run test:run   # vitest, network mocked with MSW — no backend, no LLM spend
```

The default suite stubs every model and datastore and must stay that way. No
migrations exist or are needed — `create_tables()` runs at startup and is
best-effort, so a transient Postgres blip leaves the app up and lets `/health`
report it rather than triggering a restart loop.

## Data and evaluation

`backend/data.pdf` is the corpus — 28 pages of Embedded Systems notes,
`document_id=1` → **42 chunks** — and is **gitignored** (3.7 MB personal
document), so a clean checkout has none. `eval/` holds a hand-authored golden
set: 39 answerable retrieval questions and 16 adversarial "trap" questions.
Measured 2026-08-03 (`gpt-4o-mini`): false support (D1, primary) **0.000**
(0/16), bait rejected (D4) 1.000 (16/16), false rejection **0.000** (0/39).
Method and history in `eval/results/baseline.md`.

> **These numbers predate the 2026-08-09 assembly change and need
> re-baselining before they are compared against.** `eval/` scores `state`, and
> the question route now says `ok` in cases that previously said
> `insufficient_evidence`, so D1 is no longer 0/16 by construction rather than
> by regression. Adversarial rejection now rests on the generator emitting
> `kind: "refusal"` (D4), not on the threshold. The fact route is unmeasured
> entirely — `eval/retrieval_set.jsonl` has no statement items.

```bash
cd backend    # the compose file and eval/ both live here
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
  `app.cli.retrieve`. On the **question** route, note that
  `CLAIM_VERIFICATION_THRESHOLD` is *not* the lever: since 2026-08-09 it no
  longer gates that route's answer, so this state means nothing answerable was
  generated (usually the model returned only refusals, or retrieval came back
  empty). It is still the lever on the **fact** route.
- **A statement got answered instead of checked, or vice versa** — read
  `input_type` on the response first. The classifier is a heuristic (trailing
  `?`, then a closed set of interrogative openers), and ambiguous input defaults
  to `fact`. Adding a `?` forces the question route; rephrasing away from a
  leading `what`/`is`/`has` forces the fact route.
- **Text artifacts like `Classiffcation`** — the `wamerican` wordlist is
  missing; it ships in the app image, a host ingest needs it installed.
- **Code changes don't take effect in the container** — they won't; the image
  `COPY`s the source and the CMD has no `--reload`. Use the host venv or
  `docker compose up -d --build app`.
- **A query is unexpectedly slow** — read the `timing` line before guessing.
  `model_load` above a second means the weights aren't cached where the process
  is looking (a fresh `model_cache` volume, or a host run against a cold
  `~/.cache/huggingface`); everything else is steady-state cost, covered under
  [Performance](#performance).
