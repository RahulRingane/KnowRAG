# KnowRAG — Production Build Specification

Status: Draft v1 — derived from current codebase state (`pg.py`, `vdb.py`, `es.py`, `retriver.py`, `main.py`) and the target architecture in the README.

Goal: take the system from "four standalone scripts + an empty FastAPI shell" to a production service that retrieves evidence, generates an answer strictly grounded in that evidence, verifies every claim against the retrieved chunks, and refuses/flags claims it cannot support.

---

## 1. Scope of this spec

Covers the four missing pieces plus the connective tissue needed to make the existing pieces production-safe:

1. Dependency management (`pyproject.toml`)
2. Config layer (env vars, no hardcoded URLs)
3. LangChain (or LCEL-equivalent) orchestration layer
4. Generation step (LLM answer synthesis, grounded)
5. Claim verification stage (the core value prop — don't skip this to ship faster)
6. FastAPI routes wiring ingestion + query end-to-end
7. Incremental indexing (replace drop-and-recreate)
8. Observability, testing, and deployment

Each section below is written as an implementable spec: inputs, outputs, data contracts, and acceptance criteria, not just prose description.

---

## 2. Dependency management

Currently inferred-only from imports. Pin this now — a fact-checking system with silently-upgraded embedding/reranker models is a correctness bug waiting to happen.

`pyproject.toml` (or `requirements.txt` if you want to stay simple):

```toml
[project]
name = "knowrag"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "sqlalchemy>=2.0",
    "psycopg2-binary>=2.9",
    "python-dotenv>=1.0",
    "elasticsearch>=8.13,<9",
    "qdrant-client>=1.9",
    "sentence-transformers>=3.0",
    "llama-index-core>=0.10",
    "llama-index-readers-file>=0.1",
    "fastapi>=0.111",
    "uvicorn[standard]>=0.30",
    "langchain>=0.2",
    "langchain-core>=0.2",
    "langchain-community>=0.2",
    "google-genai>=1.0",
    "pydantic>=2.7",
    "pydantic-settings>=2.2",
    "tenacity>=8.2",
    "structlog>=24.1",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio", "httpx", "ragas>=0.1"]
```

Acceptance: `pip install -e .` (or `.[dev]`) reproduces the environment from a clean venv with no manual `pip install` steps.

---

## 3. Config layer

Replace hardcoded `http://localhost:9200` and scattered `os.getenv` calls with one `app/config.py`:

```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    es_url: str = "http://localhost:9200"
    qdrant_collection: str = "knowrag"
    es_index: str = "knowrag"
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    llm_provider: str = "gemini"
    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash"   # gemini-2.5-flash-lite if higher RPM is needed
    top_k_retrieval: int = 20
    top_k_rerank: int = 5
    claim_verification_threshold: float = 0.55

    class Config:
        env_file = ".env"

settings = Settings()
```

`.env` additions needed for the above: `GEMINI_API_KEY` (get one free at https://aistudio.google.com/apikey — no credit card required).

Acceptance: no module reads `os.getenv` or hardcodes a URL directly; everything imports `settings`.

---

## 4. Incremental indexing (fix before adding more on top)

Drop-and-recreate on every run doesn't survive production ingestion of new documents. Change `vdb.py`/`es.py` to:

- Qdrant: use `upsert` keyed by a deterministic point ID = hash of `(document_id, chunk_index)`, so re-running on the same document is idempotent instead of duplicating.
- Elasticsearch: use the same composite key as the document `_id` (e.g. `f"{document_id}:{chunk_index}"`), so `index()` calls overwrite rather than duplicate.
- Add a `--full-reindex` CLI flag that explicitly does the current drop/recreate behavior, so it's opt-in, not the only mode.
- Add a `documents` table (id, filename, ingested_at, chunk_count, content_hash) so re-ingesting an unchanged file is a no-op (`content_hash` short-circuits).

Acceptance: running ingestion twice on the same PDF produces the same chunk count in both stores, not double.

---

## 5. LangChain orchestration layer

Introduce `app/chain.py`. Use LCEL rather than legacy `Chain` classes — it composes cleanly with the retrieval you already have and keeps the verification step as a separate, inspectable link rather than buried inside a monolithic chain.

```
question
   │
   ▼
[hybrid_search()]  ──► retrieved_chunks: list[Chunk]  (existing retriver.py, unchanged)
   │
   ▼
[format_context()] ──► numbered context block, each chunk tagged [C1], [C2]...
   │
   ▼
[generate_answer()] ──► LLM call, forced to cite [Cn] per sentence
   │
   ▼
[extract_claims()]  ──► split answer into atomic claims (sentence-level or LLM-assisted)
   │
   ▼
[verify_claims()]   ──► each claim checked against its cited chunk(s) via NLI/cross-encoder
   │
   ▼
[assemble_response()] ──► final answer + per-claim support labels + rejected claims
```

Key design decision: **retrieval is not inside the LLM's control.** The LLM never decides to "search more" mid-chain (no agentic loop) — this keeps the system auditable and keeps latency bounded. If iterative retrieval turns out to be needed later, add it as an explicit re-query step with a hard cap (e.g. max 2 rounds), not an open-ended agent loop.

### 5.1 Context formatting contract

```python
def format_context(chunks: list[Chunk]) -> str:
    # [C1] (doc: <document_id>, chunk: <chunk_index>)
    # <chunk text>
    #
    # [C2] ...
```

Every chunk gets a stable citation tag. The generation prompt requires the LLM to tag every sentence with the `[Cn]` it drew from, or explicitly say "not found in provided context."

### 5.2 Generation prompt contract

**LLM provider: Google Gemini API (free tier), via the `google-genai` SDK.** Chosen for its no-cost entry point, generous 1M-token context window, and native structured-output (JSON schema) support, which removes the need for a separate parsing/repair step on the claim-extraction output. Use `gemini-2.5-flash` as the default model — `gemini-2.5-flash-lite` is the fallback if RPM becomes the bottleneck, `gemini-2.5-pro` is not viable for anything beyond isolated testing given its free-tier daily cap.

System prompt for the generation step (this is the one place where prompt engineering matters most — be explicit and adversarial about not letting the model fill gaps):

```
You answer ONLY using the numbered context blocks provided. Rules:
1. Every factual sentence must end with the citation tags of the chunks it draws from, e.g. "Revenue grew 12% in Q3 [C2][C4]."
2. If the context does not contain enough information to answer part or all of the question, say so explicitly for that part — do not infer, extrapolate, or use outside knowledge.
3. Do not combine two chunks to produce a conclusion neither one supports on its own; if you do combine them, cite both and make the inference explicit ("combining [C1] and [C3], ...").
4. Never state a number, date, name, or quote that does not appear verbatim or near-verbatim in a cited chunk.
```

Output format: structured JSON via Gemini's native `response_schema` (not free text + a parser), so downstream verification never has to re-parse prose or handle malformed output:

```json
{
  "claims": [
    {"text": "Revenue grew 12% in Q3.", "citations": ["C2", "C4"]},
    {"text": "The filing does not specify headcount changes.", "citations": []}
  ]
}
```

Implementation sketch (`app/chain.py`), called from the LCEL chain as the generation link:

```python
from google import genai
from google.genai import types
from tenacity import retry, wait_exponential, stop_after_attempt
from app.config import settings

client = genai.Client(api_key=settings.gemini_api_key)

CLAIM_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "citations"],
            },
        }
    },
    "required": ["claims"],
}

# Free-tier RPM is low (single digits to low tens depending on model/day) — retry
# with backoff on 429s is not optional, it's the default failure mode under load.
@retry(wait=wait_exponential(multiplier=1, min=2, max=30), stop=stop_after_attempt(5))
def generate_claims(question: str, context_block: str) -> dict:
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=f"{context_block}\n\nQuestion: {question}",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=CLAIM_SCHEMA,
            temperature=0.0,  # determinism matters for a fact-checker
        ),
    )
    return json.loads(response.text)
```

`generate_claims()` is the only function that touches the Gemini SDK — every other stage (verification, assembly) depends only on its `dict` return shape, so swapping providers later (paid tier, different model, different vendor entirely) is a one-function change, not a chain rewrite.

---

## 6. Claim verification stage — the core differentiator

This is the part that makes KnowRAG different from a normal RAG demo. Spec it precisely.

### 6.1 Verification model choice

Two viable options, pick one for v1:

- **Option A (cheap, fast, deterministic)**: reuse the existing cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`) as a relevance scorer between claim text and cited chunk text. Pros: no new model, already in the stack. Cons: cross-encoders trained for retrieval relevance aren't trained for entailment — they'll say "related" not "supported," which is a weaker guarantee.
- **Option B (recommended)**: add a dedicated NLI/entailment model, e.g. `cross-encoder/nli-deberta-v3-base` or `vectara/hallucination_evaluation_model`, scoring `(premise=chunk_text, hypothesis=claim_text)` → `{entailment, neutral, contradiction}`. This directly answers "does this chunk support this claim," which is what you actually want to guarantee.

Recommendation: ship Option B. The whole point of the project is rejecting unsupported claims — a relevance score is the wrong tool for that job.

### 6.2 Verification algorithm

```python
def verify_claim(claim: Claim, chunks_by_tag: dict[str, Chunk]) -> ClaimVerdict:
    if not claim.citations:
        return ClaimVerdict(claim, status="UNSUPPORTED", reason="no citation provided")

    scores = []
    for tag in claim.citations:
        chunk = chunks_by_tag.get(tag)
        if chunk is None:
            scores.append((tag, "MISSING_CHUNK", 0.0))
            continue
        label, score = nli_model.predict(premise=chunk.text, hypothesis=claim.text)
        scores.append((tag, label, score))

    best = max(scores, key=lambda s: s[2] if s[1] == "entailment" else -1)
    if best[1] == "entailment" and best[2] >= settings.claim_verification_threshold:
        return ClaimVerdict(claim, status="SUPPORTED", evidence=best[0], score=best[2])
    if any(s[1] == "contradiction" for s in scores):
        return ClaimVerdict(claim, status="CONTRADICTED", evidence=scores)
    return ClaimVerdict(claim, status="UNSUPPORTED", evidence=scores)
```

### 6.3 Response assembly rules

- `SUPPORTED` claims → kept in final answer, with citation.
- `UNSUPPORTED` claims → stripped from the final answer text, but logged and returned in a separate `rejected_claims` field so the caller (and the user, if the API surfaces it) can see what was filtered and why. Silent dropping defeats the audit purpose.
- `CONTRADICTED` claims → always stripped, and surfaced prominently — this usually means the LLM misread a chunk (e.g. sign flip, off-by-one on a date), which is worth a warning-level log line.
- If **all** claims in an answer end up unsupported, the API returns a distinct response state (`"insufficient_evidence"`) rather than an empty string — the caller needs to distinguish "no evidence exists" from "something broke."

### 6.4 Data contract

```python
class ClaimVerdict(BaseModel):
    text: str
    status: Literal["SUPPORTED", "UNSUPPORTED", "CONTRADICTED"]
    citations: list[str]
    evidence_score: float | None
    chunk_ids: list[str]  # (document_id, chunk_index) pairs actually used

class FactCheckedResponse(BaseModel):
    question: str
    answer: str                       # only SUPPORTED claims, reassembled
    state: Literal["ok", "insufficient_evidence"]
    claims: list[ClaimVerdict]        # full audit trail, including rejected ones
    retrieved_chunk_ids: list[str]
    latency_ms: dict[str, float]      # per-stage timing for observability
```

This is the contract the FastAPI layer serializes — build it before the routes, since it's the thing every downstream consumer (UI, eval harness, logs) depends on.

---

## 7. FastAPI routes

`main.py` currently only has `/` and `/health`. Add:

| Route | Method | Purpose |
|---|---|---|
| `/ingest` | POST | Upload a PDF (multipart), runs `pg.py` ingestion + `vdb.py`/`es.py` indexing synchronously for small files, or enqueues a background task (see §7.1) for large ones |
| `/ingest/{document_id}` | GET | Ingestion status (`pending`, `indexed`, `failed`) |
| `/query` | POST | `{"question": str}` → `FactCheckedResponse` |
| `/query/stream` | POST | SSE/streaming variant — stream generation tokens, then emit verification results as a final event once verification completes (verification can't stream since it needs the full answer first) |
| `/health` | GET | existing — extend to check Postgres, Qdrant, Elasticsearch connectivity, not just process liveness |
| `/` | GET | existing |

### 7.1 Background ingestion

Large PDFs shouldn't block the request thread. Use FastAPI `BackgroundTasks` for v1 (simplest, no new infra); if ingestion volume grows, swap to a real queue (Celery + Redis, or RQ) without changing the route contract — the route should already return a `document_id` + `202 Accepted` + status-polling pattern from day one so this swap is invisible to callers.

### 7.2 Route → module wiring

```
POST /ingest      → pg.ingest_pdf() → vdb.index_chunks() + es.index_chunks()
POST /query       → retriver.hybrid_search() → chain.run() → FactCheckedResponse
GET  /health      → pg engine.connect(), qdrant_client.get_collections(), es.ping()
```

No route should reimplement logic already in `pg.py`/`vdb.py`/`es.py`/`retriver.py` — routes are thin orchestration only. Refactor each script's `if __name__` block to call the same functions the routes call, so there's exactly one code path for ingestion and one for retrieval, exercised by both the CLI and the API.

---

## 8. Testing & evaluation

Because this is a fact-checking system, the eval plan matters as much as the code:

1. **Unit tests** — chunk dedup logic, claim extraction parsing, verification threshold math. Fast, no external services (mock Qdrant/ES/Postgres/LLM calls).
2. **Retrieval eval** — build a small labeled set (question → expected chunk IDs) from `example_file.pdf`; measure recall@k for `semantic_search`, `keyword_search`, and `hybrid_search` separately so regressions in one retrieval path are visible.
3. **Faithfulness eval** — this is the one that matters most. Use a framework like `ragas` (faithfulness + answer-relevancy metrics) or a hand-built set of (question, answer, expected verdict) triples, including deliberately unanswerable questions to confirm the system says "insufficient evidence" rather than hallucinating.
4. **Adversarial set** — questions designed to tempt fabrication (dates/numbers adjacent to but not in the document, questions combining two unrelated chunks into a false conclusion, questions about entities not in the document at all). Track false-support rate (claims marked SUPPORTED that shouldn't be) as the primary metric — false rejections are a UX cost, false supports are a trust failure.

Acceptance: CI runs unit + retrieval eval on every PR; faithfulness/adversarial eval runs on a schedule (nightly) against a fixed golden set, with alerting if false-support rate crosses a threshold.

---

## 9. Observability

- Structured logging (`structlog`) at each chain stage: retrieval count, rerank scores, generation latency, verification verdict distribution.
- Per-request trace ID threaded through Postgres/Qdrant/ES calls and the LLM call, so a bad answer can be traced back to which chunks were retrieved and why a claim was accepted/rejected.
- Metrics to export (Prometheus-style counters/histograms): `retrieval_latency_seconds`, `generation_latency_seconds`, `verification_latency_seconds`, `claims_supported_total`, `claims_unsupported_total`, `claims_contradicted_total`.

---

## 10. Deployment shape

- `docker-compose.yml` for local/staging: Postgres, Qdrant, Elasticsearch, the FastAPI app, one service each — this is the fastest way to make "not yet implemented" reproducible for anyone else pulling the repo.
- Health checks in compose gate app startup on the three datastores being ready (avoids the classic "app starts before ES is up" flake).
- Model loading: `sentence-transformers` and the NLI model should load once at app startup (FastAPI lifespan event), not per-request — cross-encoder load time is non-trivial.

---

## 11. Suggested build order

Sequenced so each step is independently testable and nothing blocks on the whole system existing:

1. `pyproject.toml` + `config.py` (foundation, no behavior change)
2. Incremental indexing fix in `vdb.py`/`es.py` (de-risks re-running ingestion during dev)
3. Refactor `pg.py`/`vdb.py`/`es.py`/`retriver.py` `__main__` blocks to call importable functions (no new logic, just makes routes possible)
4. FastAPI `/ingest` + `/query` routes wired to existing retrieval, generation stubbed to just echo top chunk (proves the plumbing before adding intelligence)
5. Generation step with structured-claim output (§5.2)
6. Claim verification stage (§6) — the highest-value, highest-risk piece; build the eval set (§8.3–8.4) alongside it, not after
7. `/query/stream`, background ingestion, observability, docker-compose

---

## 12. Open decisions to confirm before implementation

- **LLM provider for generation**: ~~open~~ **decided — Google Gemini API, free tier** (`gemini-2.5-flash`, called via the `google-genai` SDK directly rather than through LangChain's Gemini wrapper, so the native `response_schema` JSON mode is available for claim extraction). Revisit if free-tier RPM/RPD becomes a bottleneck in eval or production — see below.
- **NLI model hosting**: local `sentence-transformers` cross-encoder (simple, no extra infra) vs. a hosted endpoint (better latency/scaling at cost of infra). Recommend local for v1 given the rest of the stack is already local-model-based.
- **Claim granularity**: sentence-level (simple, may merge multiple facts into one claim) vs. LLM-assisted atomic-claim extraction (more precise, adds a generation call). Recommend starting sentence-level and only moving to LLM-assisted extraction if the eval set shows sentence-level is too coarse.

### 12.1 Gemini free-tier operational notes

- **Rate limits shift often** (there was a significant cut in Dec 2025) and vary by model/day — check https://ai.google.dev/gemini-api/docs/rate-limits before assuming a specific RPM/RPD figure; don't hardcode a quota number into code or docs.
- **Retry-with-backoff is mandatory, not optional** at this tier — build the `tenacity` retry decorator into `generate_claims()` from day one (§5.2), since 429s are expected steady-state behavior under any real eval or usage load, not an edge case.
- **Eval cost management** (§8): the faithfulness/adversarial eval sets will burn through free-tier daily quota fast if re-run naively. Cache `generate_claims()` output keyed on `(question, retrieved_chunk_ids, model)` during development so repeated eval runs against unchanged retrieval don't re-spend calls.
- **`temperature=0.0`** is set in the generation call specifically because determinism matters more than creativity for a fact-checking system — this is a Gemini-specific config knob worth calling out since some SDK defaults are non-zero.
- **Data usage**: Gemini's free tier may use prompts/responses for model training (unlike the paid tier) — worth flagging if `example_file.pdf` or later ingested documents contain anything sensitive; move to a paid Gemini tier or a different provider before ingesting non-public documents in production.
- **Swap-out path stays open**: since `generate_claims()` is the sole integration point, moving to Gemini's paid tier, a different Gemini model, or a different vendor entirely later requires touching one function, not the chain (§5.2, §5).